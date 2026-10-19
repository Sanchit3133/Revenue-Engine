"""Customer-facing Razorpay checkout page for an owner-approved payment order."""

from __future__ import annotations

from datetime import datetime, timezone
import hmac

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from app.config import settings
from app.database import connect
from app.payment_checkout import token_hash, verify_checkout_signature

payment_checkout_bp = Blueprint("payment_checkout", __name__, template_folder="templates")


def _payment_for_token(token: str):
    clean = (token or "").strip()
    if not clean or len(clean) > 256:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT p.id,p.provider,p.provider_order_id,p.amount,p.currency,p.status,
                      p.client_request_public_id,p.checkout_token_hash,p.checkout_expires_at,
                      cr.business_name,cr.email,cr.status AS request_status
               FROM payments p
               JOIN client_requests cr ON cr.public_id=p.client_request_public_id
               WHERE p.checkout_token_hash=?
               ORDER BY p.id DESC LIMIT 1""",
            (token_hash(clean),),
        ).fetchone()
    if row is None or str(row["provider"]).lower() != "razorpay" or not row["provider_order_id"]:
        return None
    expiry_text = str(row["checkout_expires_at"] or "")
    try:
        expiry = datetime.fromisoformat(expiry_text.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if datetime.now(timezone.utc) >= expiry:
        return None
    # Recompute the deterministic capability token, too, so a corrupt/stale
    # database token hash cannot make an unrelated order payable.
    signing_secret = getattr(settings, "flask_secret_key", None) or current_app.secret_key
    from app.payment_checkout import checkout_token
    expected_token = checkout_token(
        secret=str(signing_secret),
        request_public_id=str(row["client_request_public_id"]),
        order_id=str(row["provider_order_id"]),
        expires_at=expiry_text,
    )
    if not hmac.compare_digest(expected_token, clean):
        return None
    return row


@payment_checkout_bp.get("/pay/<token>")
def payment_page(token: str):
    row = _payment_for_token(token)
    if row is None:
        return render_template("payment_unavailable.html"), 404
    if str(row["status"]).lower() == "captured" or str(row["request_status"]).upper() == "PAID":
        return render_template("payment_complete.html", already_paid=True), 200
    if str(row["status"]).lower() not in {"link_sent", "link_email_failed"}:
        return render_template("payment_unavailable.html"), 404
    key_id = getattr(settings, "razorpay_key_id", None)
    if not key_id:
        return render_template("payment_unavailable.html"), 503
    return render_template(
        "payment_checkout.html",
        payment_token=token,
        key_id=key_id,
        order_id=row["provider_order_id"],
        amount=int(row["amount"]),
        currency=str(row["currency"]).upper(),
        business_name=row["business_name"],
    ), 200


@payment_checkout_bp.post("/pay/<token>/verify")
def verify_checkout_result(token: str):
    row = _payment_for_token(token)
    if row is None:
        return jsonify({"error": "payment_link_invalid_or_expired"}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "json_required"}), 400
    order_id = str(payload.get("razorpay_order_id") or "").strip()
    payment_id = str(payload.get("razorpay_payment_id") or "").strip()
    signature = str(payload.get("razorpay_signature") or "").strip()
    if not hmac.compare_digest(order_id, str(row["provider_order_id"])):
        return jsonify({"error": "order_mismatch"}), 400
    key_secret = getattr(settings, "razorpay_key_secret", None)
    if not verify_checkout_signature(
        order_id=order_id,
        payment_id=payment_id,
        signature=signature,
        key_secret=key_secret,
    ):
        return jsonify({"error": "invalid_checkout_signature"}), 401

    # Do not mark the request PAID based on browser callback alone. The
    # authenticated payment.captured webhook must confirm the exact order,
    # expected amount and currency before delivery can be approved.
    return jsonify({
        "verified": True,
        "payment_status": "pending_webhook_confirmation",
        "message": "Payment response verified. Final payment confirmation is pending from Razorpay.",
    }), 200
