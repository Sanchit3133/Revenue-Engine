"""Customer-facing, one-time quote checkout backed by server-created Razorpay orders."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import secrets
from typing import Any

import requests
from flask import Blueprint, current_app, jsonify, render_template, request, session

from app.config import settings
from app.database import connect
from app.webhook_processing import process_razorpay_event

payments_bp = Blueprint("payments_web", __name__, template_folder="templates")
RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"
RAZORPAY_PAYMENTS_URL = "https://api.razorpay.com/v1/payments"
_CHECKOUT_STATUSES = {"QUOTED", "ORDER_CREATED"}


def checkout_token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _csrf_token() -> str:
    token = session.get("payment_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["payment_csrf_token"] = token
    return token


def _csrf_valid(value: str | None) -> bool:
    expected = session.get("payment_csrf_token", "")
    return bool(expected and value and secrets.compare_digest(expected, value))


def _load_order(token: str) -> dict[str, Any] | None:
    if not token or len(token) > 200:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT o.*, r.business_name, r.email, r.request_type, r.message AS request_message
               FROM client_orders AS o
               JOIN client_requests AS r ON r.id=o.client_request_id
               WHERE o.token_hash=? LIMIT 1""",
            (checkout_token_hash(token),),
        ).fetchone()
    return dict(row) if row is not None else None


def _expired(value: str | None) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


def _payment_configuration_error() -> str | None:
    key_id = getattr(settings, "razorpay_key_id", None)
    key_secret = getattr(settings, "razorpay_key_secret", None)
    if not key_id or not key_secret:
        return "Razorpay API credentials are not configured in the deployment secrets."
    if key_id.startswith("rzp_live_") and not getattr(settings, "razorpay_live_payments_enabled", False):
        return "Live payments are disabled. Configure Razorpay Test Mode credentials for acceptance testing."
    if not (key_id.startswith("rzp_test_") or key_id.startswith("rzp_live_")):
        return "Razorpay key ID format is not recognized."
    return None


def _mark_order_error(order_id: int, status: str, note: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE client_orders SET status=?, error_note=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, note[:500], order_id),
        )
        conn.commit()


@payments_bp.get("/pay/<token>")
def quote_checkout(token: str):
    order = _load_order(token)
    if order is None:
        return render_template(
            "payment_checkout.html",
            state="unavailable",
            order=None,
            csrf_token=_csrf_token(),
            key_id=None,
            error="This payment link is invalid or no longer available. Contact Revenue Engine using your request reference.",
            token=token,
            checkout_configured=False,
        ), 404

    state = str(order.get("status") or "")
    if state == "QUOTED" and _expired(order.get("expires_at")):
        with connect() as conn:
            conn.execute(
                "UPDATE client_orders SET status='EXPIRED',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='QUOTED'",
                (order["id"],),
            )
            conn.commit()
        state = "EXPIRED"

    display_state = "paid" if state == "PAID" else "checkout" if state in _CHECKOUT_STATUSES else "unavailable"
    amount_display = f"{Decimal(int(order['amount'])) / Decimal(100):,.2f}"
    return render_template(
        "payment_checkout.html",
        state=display_state,
        order=order,
        amount_display=amount_display,
        csrf_token=_csrf_token(),
        key_id=getattr(settings, "razorpay_key_id", None) if display_state == "checkout" else None,
        error=None,
        token=token,
        checkout_configured=_payment_configuration_error() is None,
    ), 200


@payments_bp.post("/pay/<token>/order")
def create_checkout_order(token: str):
    if not _csrf_valid(request.headers.get("X-CSRF-Token")):
        return jsonify({"error": "form_expired_refresh_payment_page"}), 400
    config_error = _payment_configuration_error()
    if config_error:
        return jsonify({"error": config_error}), 503

    order = _load_order(token)
    if order is None:
        return jsonify({"error": "payment_link_unavailable"}), 404
    state = str(order.get("status") or "")
    if state == "ORDER_CREATED" and order.get("razorpay_order_id"):
        return jsonify({
            "order_id": order["razorpay_order_id"],
            "amount": int(order["amount"]),
            "currency": order["currency"],
            "key_id": settings.razorpay_key_id,
        }), 200
    if state != "QUOTED" or _expired(order.get("expires_at")):
        return jsonify({"error": "quote_not_payable_or_expired"}), 409

    with connect() as conn:
        claimed = conn.execute(
            """UPDATE client_orders SET status='ORDER_CREATING',error_note=NULL,updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='QUOTED'""",
            (order["id"],),
        ).rowcount
        conn.commit()
    if claimed != 1:
        return jsonify({"error": "checkout_is_already_being_prepared_refresh_shortly"}), 409

    payload = {
        "amount": int(order["amount"]),
        "currency": str(order["currency"]).upper(),
        "receipt": str(order["public_id"])[:40],
        "notes": {
            "revenue_engine_client_order_id": str(order["public_id"]),
            "revenue_engine_client_request_id": str(order["client_request_id"]),
        },
    }
    try:
        response = requests.post(
            RAZORPAY_ORDERS_URL,
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        _mark_order_error(
            int(order["id"]),
            "ORDER_CREATE_UNCERTAIN",
            f"Razorpay order creation outcome uncertain ({type(exc).__name__}); check provider activity before retrying.",
        )
        return jsonify({"error": "order_creation_outcome_uncertain_check_provider_dashboard_before_retry"}), 503

    if response.status_code not in {200, 201}:
        _mark_order_error(
            int(order["id"]),
            "ORDER_CREATE_FAILED",
            f"Razorpay order creation returned HTTP {response.status_code}; no automatic retry was made.",
        )
        return jsonify({"error": "razorpay_order_creation_failed_check_server_status"}), 502
    try:
        provider_order = response.json()
    except (ValueError, AttributeError):
        _mark_order_error(int(order["id"]), "ORDER_CREATE_UNCERTAIN", "Razorpay returned an unreadable order response.")
        return jsonify({"error": "order_creation_outcome_uncertain"}), 502

    provider_order_id = str(provider_order.get("id") or "").strip()
    try:
        provider_amount = int(provider_order.get("amount"))
    except (TypeError, ValueError):
        provider_amount = 0
    provider_currency = str(provider_order.get("currency") or "").upper()
    if (
        not provider_order_id
        or provider_amount != int(order["amount"])
        or provider_currency != str(order["currency"]).upper()
        or str(provider_order.get("status") or "").lower() not in {"created", "attempted"}
    ):
        _mark_order_error(int(order["id"]), "PAYMENT_MISMATCH", "Razorpay order response did not match the approved quote.")
        return jsonify({"error": "provider_order_did_not_match_approved_quote"}), 502

    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM payments WHERE lower(provider)='razorpay' AND provider_order_id=? ORDER BY id DESC LIMIT 1",
            (provider_order_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO payments(lead_id,provider,provider_payment_id,amount,currency,status,provider_order_id)
                   VALUES(NULL,'razorpay',NULL,?,?,'created',?)""",
                (int(order["amount"]), str(order["currency"]).upper(), provider_order_id),
            )
        conn.execute(
            """UPDATE client_orders SET status='ORDER_CREATED',razorpay_order_id=?,error_note=NULL,
               updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='ORDER_CREATING'""",
            (provider_order_id, order["id"]),
        )
        conn.commit()
    return jsonify({
        "order_id": provider_order_id,
        "amount": int(order["amount"]),
        "currency": str(order["currency"]).upper(),
        "key_id": settings.razorpay_key_id,
    }), 200


@payments_bp.post("/pay/<token>/verify")
def verify_checkout_payment(token: str):
    if not _csrf_valid(request.headers.get("X-CSRF-Token")):
        return jsonify({"error": "form_expired_refresh_payment_page"}), 400
    config_error = _payment_configuration_error()
    if config_error:
        return jsonify({"error": config_error}), 503
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "json_required"}), 400

    provider_order_id = str(data.get("razorpay_order_id") or "").strip()
    payment_id = str(data.get("razorpay_payment_id") or "").strip()
    signature = str(data.get("razorpay_signature") or "").strip()
    if not provider_order_id or not payment_id or not signature:
        return jsonify({"error": "razorpay_checkout_fields_missing"}), 400

    order = _load_order(token)
    if order is None or order.get("status") not in {"ORDER_CREATED", "PAID"}:
        return jsonify({"error": "payment_order_unavailable"}), 404
    if str(order.get("razorpay_order_id") or "") != provider_order_id:
        return jsonify({"error": "order_id_mismatch"}), 400

    signature_message = f"{provider_order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(
        str(settings.razorpay_key_secret).encode("utf-8"),
        signature_message,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return jsonify({"error": "invalid_checkout_signature"}), 401

    try:
        response = requests.get(
            f"{RAZORPAY_PAYMENTS_URL}/{payment_id}",
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            timeout=15,
        )
    except requests.RequestException:
        return jsonify({"status": "pending", "message": "Checkout response verified; payment capture is awaiting provider confirmation. Do not pay again."}), 202
    if response.status_code != 200:
        return jsonify({"status": "pending", "message": "Payment is awaiting provider confirmation. Check the dashboard before retrying."}), 202
    try:
        provider_payment = response.json()
    except (ValueError, AttributeError):
        return jsonify({"status": "pending", "message": "Payment is awaiting provider confirmation."}), 202

    if (
        str(provider_payment.get("id") or "") != payment_id
        or str(provider_payment.get("order_id") or "") != provider_order_id
        or int(provider_payment.get("amount") or 0) != int(order["amount"])
        or str(provider_payment.get("currency") or "").upper() != str(order["currency"]).upper()
    ):
        _mark_order_error(int(order["id"]), "PAYMENT_MISMATCH", "Razorpay payment record did not match the approved quote.")
        return jsonify({"error": "payment_record_did_not_match_approved_quote"}), 409
    if str(provider_payment.get("status") or "").lower() != "captured":
        return jsonify({"status": "pending", "message": "Payment received by Razorpay; final capture confirmation is pending. You do not need to pay again."}), 202

    payload = {
        "id": f"checkout-confirmed:{payment_id}",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": provider_order_id,
                    "status": "captured",
                    "captured": True,
                    "amount": int(provider_payment["amount"]),
                    "currency": str(provider_payment["currency"]).upper(),
                    "email": provider_payment.get("email"),
                    "notes": provider_payment.get("notes") or {},
                }
            }
        },
    }
    with connect() as conn:
        result = process_razorpay_event(conn, payload)
    if result.get("accepted") and not result.get("reconciliation_required"):
        return jsonify({"status": "paid", "message": "Payment confirmed. Your request is now waiting for owner review before delivery."}), 200
    if result.get("reconciliation_required"):
        return jsonify({"status": "pending", "message": "Payment was received but needs manual reconciliation. Do not pay again."}), 202
    return jsonify({"error": "payment_verification_failed"}), 409
