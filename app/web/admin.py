"""Authenticated owner dashboard and explicit delivery approval workflow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from functools import wraps
import io
import secrets
import subprocess
import sys
from pathlib import Path
from flask import url_for

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session

from app.config import settings
from app.database import connect
from app.notifications import deliver_approved_file, send_brevo_email
from app.payment_checkout import amount_to_minor_units, checkout_token, create_razorpay_order, token_hash
from app.models import LeadState
from app.outreach import prepare_first_contact
from app.reply_processor import process_reply
from app.market_strategy import get_market, list_markets, market_validation_complete
from app.campaign_sender import send_prepared_batch

admin_bp = Blueprint("admin", __name__, template_folder="templates")


def _owner_password() -> str | None:
    return getattr(settings, "owner_dashboard_password", None)


def _csrf_token() -> str:
    token = session.get("owner_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["owner_csrf_token"] = token
    return token


def _csrf_valid(value: str) -> bool:
    expected = session.get("owner_csrf_token", "")
    return bool(expected and value and secrets.compare_digest(expected, value))


def _owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("owner_authenticated"):
            return redirect(url_for("admin.owner_login", next=request.path))
        if not _owner_password():
            session.clear()
            return render_template(
                "owner_login.html",
                csrf_token=_csrf_token(),
                error="Set OWNER_DASHBOARD_PASSWORD in the deployment secrets before enabling the owner dashboard.",
                disabled=True,
            ), 503
        return view(*args, **kwargs)
    return wrapped


def _audit(conn, action: str, public_id: str | None, details: str = "") -> None:
    conn.execute(
        "INSERT INTO admin_audit(action, object_type, object_public_id, details) VALUES (?, 'client_request', ?, ?)",
        (action, public_id, (details or "")[:1000]),
    )


def _record_module_run(module: str, status: str, summary: str, log_text: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO module_runs(module,status,summary,log_text) VALUES(?,?,?,?)",
            (module[:80], status[:40], summary[:1000], (log_text or "")[-20000:]),
        )
        conn.execute(
            "INSERT INTO admin_audit(action,object_type,object_public_id,details) VALUES(?,?,NULL,?)",
            (f"module_run:{module[:60]}", "module_run", f"status={status};{summary}"[:1000]),
        )
        conn.commit()


@admin_bp.route("/owner/login", methods=["GET", "POST"])
def owner_login():
    configured = bool(_owner_password())
    error = None
    token = _csrf_token()
    if request.method == "POST":
        if not _csrf_valid(request.form.get("csrf_token", "")):
            error = "The login form expired. Refresh and try again."
        elif not configured:
            error = "Owner login is disabled until OWNER_DASHBOARD_PASSWORD is configured in Replit Secrets."
        else:
            supplied = request.form.get("password", "")
            if secrets.compare_digest(supplied, _owner_password() or ""):
                session.clear()
                session["owner_authenticated"] = True
                session["owner_csrf_token"] = secrets.token_urlsafe(32)
                session["owner_authenticated_at"] = datetime.now(timezone.utc).isoformat()
                target = request.args.get("next", "")
                if not target.startswith("/") or target.startswith("//"):
                    target = url_for("admin.owner_dashboard")
                return redirect(target)
            error = "Incorrect owner password."
            token = _csrf_token()
    return render_template("owner_login.html", csrf_token=token, error=error, disabled=not configured), (503 if not configured else 200)


@admin_bp.post("/owner/logout")
@_owner_required
def owner_logout():
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")
    session.clear()
    return redirect(url_for("admin.owner_login"))


@admin_bp.get("/owner")
@_owner_required
def owner_dashboard():
    with connect() as conn:
        metrics = {
            "leads": conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
            "qualified_leads": conn.execute("SELECT COUNT(*) FROM leads WHERE state IN ('QUALIFIED','VERIFIED')").fetchone()[0],
            "prepared_outreach": conn.execute("SELECT COUNT(*) FROM outreach WHERE status='PREPARED'").fetchone()[0],
            "sent_outreach": conn.execute("SELECT COUNT(*) FROM outreach WHERE status='SENT'").fetchone()[0],
            "failed_outreach": conn.execute("SELECT COUNT(*) FROM outreach WHERE status='FAILED'").fetchone()[0],
            "consultations": conn.execute("SELECT COUNT(*) FROM consultation_submissions").fetchone()[0],
            "payments": conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
            "captured_payments": conn.execute("SELECT COUNT(*) FROM payments WHERE lower(COALESCE(status,'')) IN ('captured','paid','success')").fetchone()[0],
            "pending_requests": conn.execute("SELECT COUNT(*) FROM client_requests WHERE status IN ('PENDING_REVIEW','APPROVED','DELIVERY_UNCERTAIN','PAYMENT_ORDER_CREATING','PAYMENT_ORDER_UNCERTAIN','PAYMENT_LINK_FAILED','PAYMENT_LINK_EMAIL_UNCERTAIN','AWAITING_PAYMENT','PAID')").fetchone()[0],
            "delivered_files": conn.execute("SELECT COUNT(*) FROM client_requests WHERE status='DELIVERED'").fetchone()[0],
            "webhook_events": conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0],
            "suppressed_emails": conn.execute("SELECT COUNT(*) FROM suppressions").fetchone()[0],
        }
        leads = conn.execute(
            "SELECT id,business_name,email,country,state,score,created_at FROM leads ORDER BY id DESC LIMIT 8"
        ).fetchall()
        market_validations = conn.execute(
            "SELECT country,status,evidence_notes,validated_at,updated_at FROM market_validations ORDER BY lower(country), country"
        ).fetchall()
        outreach = conn.execute(
            "SELECT id,email,subject,status,provider,last_error,created_at FROM outreach ORDER BY id DESC LIMIT 8"
        ).fetchall()
        payments = conn.execute(
            "SELECT id,lead_id,provider,provider_payment_id,provider_order_id,amount,currency,status,created_at FROM payments ORDER BY id DESC LIMIT 8"
        ).fetchall()
        consultations = conn.execute(
            "SELECT public_id,business_name,email,stage,updated_at FROM consultation_submissions ORDER BY updated_at DESC LIMIT 8"
        ).fetchall()
        replies = conn.execute(
            """SELECT r.id,r.lead_id,r.from_email,r.subject,r.body,r.intent,r.budget_signal,r.source,r.received_at,
                      l.business_name AS business_name
               FROM reply_events r JOIN leads l ON l.id=r.lead_id
               ORDER BY r.id DESC LIMIT 15"""
        ).fetchall()
        assistant_messages = conn.execute(
            """SELECT consultation_public_id,role,content,created_at
               FROM consultation_messages ORDER BY id DESC LIMIT 50"""
        ).fetchall()
        requests_rows = conn.execute(
            """SELECT cr.id,cr.public_id,cr.business_name,cr.email,cr.request_type,cr.status,
                      cr.draft_source,cr.draft_error,cr.scope_confirmed,cr.payment_reference,cr.approval_note,cr.created_at,
                      p.amount AS payment_amount,p.currency AS payment_currency,
                      p.status AS payment_status,p.provider_order_id
               FROM client_requests cr
               LEFT JOIN payments p ON p.id=(
                   SELECT MAX(p2.id) FROM payments p2
                   WHERE p2.client_request_public_id=cr.public_id
               )
               ORDER BY cr.id DESC LIMIT 15"""
        ).fetchall()
        events = conn.execute(
            "SELECT provider,event_type,resource_id,received_at,processed_at FROM webhook_events ORDER BY id DESC LIMIT 8"
        ).fetchall()
        audit = conn.execute(
            "SELECT action,object_type,object_public_id,details,created_at FROM admin_audit ORDER BY id DESC LIMIT 10"
        ).fetchall()
        module_runs = conn.execute(
            "SELECT module,status,summary,log_text,created_at FROM module_runs ORDER BY id DESC LIMIT 12"
        ).fetchall()
    return render_template(
        "owner_dashboard.html",
        csrf_token=_csrf_token(),
        metrics=metrics,
        leads=leads,
        market_validations=market_validations,
        markets=list_markets(),
        target_market_country=getattr(settings, "target_market_country", "India"),
        outreach=outreach,
        payments=payments,
        consultations=consultations,
        replies=replies,
        assistant_messages=assistant_messages,
        requests_rows=requests_rows,
        events=events,
        audit=audit,
        module_runs=module_runs,
        email_provider=getattr(settings, "email_provider", "unknown"),
        hf_configured=bool(getattr(settings, "hf_token", None) or getattr(settings, "hf_api_key", None)),
        notification_configured=bool(getattr(settings, "owner_notification_email", None) and getattr(settings, "brevo_api_key", None) and getattr(settings, "brevo_sender", None)),
        google_configured=bool(getattr(settings, "google_api_key", None)),
    )


@admin_bp.post("/owner/requests/<int:request_id>/payment-link")
@_owner_required
def create_or_resend_payment_link(request_id: int):
    """Owner-approved quote action; creates an order and emails a one-use checkout link."""
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")

    amount_raw = (request.form.get("amount_rupees") or "").strip()
    currency = (request.form.get("currency") or "INR").strip().upper()
    scope_note = (request.form.get("scope_note") or "").strip()[:1000]
    if currency != "INR":
        flash("Phase 1 checkout is enabled for INR only.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")
    try:
        amount_minor = amount_to_minor_units(amount_raw, currency=currency)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")
    if request.form.get("csrf_scope_confirmation") != "yes":
        flash("Confirm that the customer agreed to this scope and amount before creating a payment order.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")
    if len(scope_note) < 8:
        flash("Record the agreed written scope or agreement reference before requesting payment.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")

    key_id = getattr(settings, "razorpay_key_id", None)
    key_secret = getattr(settings, "razorpay_key_secret", None)
    signing_secret = getattr(settings, "flask_secret_key", None)
    if not key_id or not key_secret or not signing_secret:
        flash("Checkout is not configured. Add RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, and FLASK_SECRET_KEY in the published deployment secrets.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")
    if not getattr(settings, "brevo_api_key", None) or not getattr(settings, "brevo_sender", None):
        flash("Configure BREVO_API_KEY and BREVO_SENDER before sending a payment link.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")
    public_app_url = str(getattr(settings, "public_app_url", "") or "").rstrip("/")
    if not public_app_url.startswith("https://"):
        flash("PUBLIC_APP_URL must be the HTTPS published URL before a checkout link can be sent.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")

    with connect() as conn:
        row = conn.execute(
            """SELECT id,public_id,business_name,email,request_type,status,consent_at,
                      draft_filename,draft_content,scope_confirmed
               FROM client_requests WHERE id=?""",
            (request_id,),
        ).fetchone()
        if row is None:
            abort(404)
        if row["request_type"] != "delivery" or not row["consent_at"] or not row["draft_content"] or not row["draft_filename"]:
            flash("Only a consented delivery request with a generated private draft can be quoted.", "error")
            return redirect(url_for("admin.owner_dashboard") + "#client-requests")
        if str(row["status"]).upper() in {"DELIVERED", "REJECTED", "SENDING", "DELIVERY_UNCERTAIN", "PAID"}:
            flash(f"Cannot create a payment link while this request is {row['status']}.", "error")
            return redirect(url_for("admin.owner_dashboard") + "#client-requests")

        existing = conn.execute(
            """SELECT id,amount,currency,status,provider_order_id,checkout_expires_at
               FROM payments WHERE lower(provider)='razorpay' AND client_request_public_id=?
               ORDER BY id DESC LIMIT 1""",
            (row["public_id"],),
        ).fetchone()

        if existing and str(existing["status"]).lower() in {"link_sent", "link_email_failed"}:
            if int(existing["amount"] or 0) != amount_minor or str(existing["currency"] or "").upper() != currency:
                flash("A payment order already exists for a different quote. Do not create a second order automatically; reconcile the existing order first.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            if str(existing["status"]).lower() == "link_sent":
                flash("A payment link has already been sent for this request. Check its status before sending another.", "warning")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            # Reuse the existing order after a definite email rejection; don't create
            # another provider order. A timeout is marked link_email_uncertain below
            # and intentionally cannot be retried automatically.
            order_id = str(existing["provider_order_id"] or "")
            expiry_text = str(existing["checkout_expires_at"] or "")
            if not order_id or not expiry_text:
                flash("The saved payment order is incomplete and needs manual reconciliation.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            token = checkout_token(secret=str(signing_secret), request_public_id=str(row["public_id"]), order_id=order_id, expires_at=expiry_text)
            payment_row_id = int(existing["id"])
            resend_existing = True
        elif existing and str(existing["status"]).lower() == "order_failed":
            # Provider explicitly rejected the earlier order request, so it is safe
            # to retry the same local payment row with the newly owner-approved quote.
            payment_row_id = int(existing["id"])
            conn.execute(
                """UPDATE payments SET amount=?,currency=?,status='order_creating',
                   provider_order_id=NULL,checkout_token_hash=NULL,checkout_expires_at=NULL,
                   provider_payment_id=NULL WHERE id=? AND status='order_failed'""",
                (amount_minor, currency, payment_row_id),
            )
            conn.execute(
                "UPDATE client_requests SET scope_confirmed=1,approval_note=?,status='PAYMENT_ORDER_CREATING',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (scope_note, request_id),
            )
            _audit(conn, "payment_order_creation_retried_after_definite_failure", str(row["public_id"]), f"currency={currency};amount_minor={amount_minor}")
            conn.commit()
            resend_existing = False
            token = ""
            order_id = ""
            expiry_text = ""
        elif existing:
            flash(f"A Razorpay order already exists for this request with status {existing['status']}. Review/reconcile it before creating another.", "warning")
            return redirect(url_for("admin.owner_dashboard") + "#client-requests")
        else:
            conn.execute(
                """INSERT INTO payments
                   (lead_id,provider,amount,currency,status,client_request_public_id)
                   VALUES (NULL,'razorpay',?,?,'order_creating',?)""",
                (amount_minor, currency, row["public_id"]),
            )
            payment_row_id = int(conn.execute("SELECT id FROM payments WHERE client_request_public_id=? ORDER BY id DESC LIMIT 1", (row["public_id"],)).fetchone()[0])
            conn.execute(
                "UPDATE client_requests SET scope_confirmed=1,approval_note=?,status='PAYMENT_ORDER_CREATING',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (scope_note, request_id),
            )
            _audit(conn, "payment_order_creation_started", str(row["public_id"]), f"currency={currency};amount_minor={amount_minor};scope_note_recorded=true")
            conn.commit()
            resend_existing = False
            token = ""
            order_id = ""
            expiry_text = ""

    if not resend_existing:
        result = create_razorpay_order(
            key_id=str(key_id),
            key_secret=str(key_secret),
            amount_minor=amount_minor,
            currency=currency,
            receipt=("re-" + secrets.token_hex(16))[:40],
            request_public_id=str(row["public_id"]),
        )
        if not result.get("success"):
            payment_status = "order_creation_uncertain" if result.get("uncertain") else "order_failed"
            request_status = "PAYMENT_ORDER_UNCERTAIN" if result.get("uncertain") else "PAYMENT_LINK_FAILED"
            with connect() as conn:
                conn.execute("UPDATE payments SET status=? WHERE id=? AND status='order_creating'", (payment_status, payment_row_id))
                conn.execute("UPDATE client_requests SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status NOT IN ('PAID','REJECTED','DELIVERED','SENDING')", (request_status, request_id))
                _audit(conn, "payment_order_creation_failed", str(row["public_id"]), str(result.get("reason", "failed"))[:500])
                conn.commit()
            flash("Razorpay order creation did not complete. " + ("The provider response may be ambiguous; inspect Razorpay Test/Live Dashboard activity before retrying." if result.get("uncertain") else "Check API credentials and the reported order error before retrying."), "error")
            return redirect(url_for("admin.owner_dashboard") + "#client-requests")

        order_id = str(result["order_id"])
        expiry_text = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        token = checkout_token(secret=str(signing_secret), request_public_id=str(row["public_id"]), order_id=order_id, expires_at=expiry_text)
        with connect() as conn:
            conn.execute(
                """UPDATE payments SET provider_order_id=?,checkout_token_hash=?,checkout_expires_at=?,status='order_created'
                   WHERE id=? AND status='order_creating'""",
                (order_id, token_hash(token), expiry_text, payment_row_id),
            )
            conn.commit()

    payment_url = f"{public_app_url}/pay/{token}"
    email_body = (
        f"Hello {row['business_name']},\n\n"
        "Following your Revenue Engine consultation and the agreed project scope, here is your secure payment link.\n\n"
        f"Project request reference: {row['public_id']}\n"
        f"Approved amount: ₹{amount_minor / 100:.2f} {currency}\n"
        f"Razorpay order reference: {order_id}\n"
        f"Secure checkout: {payment_url}\n\n"
        "The link expires in 7 days. Payment is considered received only after provider verification. "
        "The generated HTML is a private draft; delivery remains subject to payment verification and owner approval.\n\n"
        "If the amount or scope does not match your written agreement, do not pay and reply to this email.\n"
        "Revenue Engine"
    )
    email_result = send_brevo_email(
        to_email=str(row["email"]),
        subject=f"Approved project quote and payment link — {row['public_id']}",
        body=email_body,
    )
    email_status = "link_sent" if email_result.get("success") else ("link_email_uncertain" if email_result.get("uncertain") else "link_email_failed")
    request_status = "AWAITING_PAYMENT" if email_result.get("success") else ("PAYMENT_LINK_EMAIL_UNCERTAIN" if email_result.get("uncertain") else "PAYMENT_LINK_FAILED")
    with connect() as conn:
        conn.execute("UPDATE payments SET status=? WHERE id=? AND status IN ('order_created','link_email_failed')", (email_status, payment_row_id))
        conn.execute("UPDATE client_requests SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (request_status, request_id))
        _audit(conn, "payment_link_email_result", str(row["public_id"]), f"status={email_status};currency={currency};amount_minor={amount_minor};reason={email_result.get('reason','unknown')}")
        conn.commit()

    if email_result.get("success"):
        flash("Approved quote and secure payment link were emailed. The request will become PAID only after a valid captured-payment webhook matches the order, amount, and currency.", "success")
    elif email_result.get("uncertain"):
        flash("The email provider outcome is uncertain. Check Brevo activity before retrying; the system will not auto-resend.", "warning")
    else:
        flash("Payment order created, but Brevo rejected the quote email. No automatic retry was attempted; resolve the email setup before an explicit resend.", "error")
    return redirect(url_for("admin.owner_dashboard") + "#client-requests")


def _verified_captured_payment(
    conn, payment_reference: str, customer_email: str, request_public_id: str
) -> bool:
    """Require a captured payment explicitly mapped to this exact client request."""
    reference = (payment_reference or "").strip()
    expected_email = (customer_email or "").strip().lower()
    expected_request = (request_public_id or "").strip()
    if not reference or not expected_email or not expected_request:
        return False
    row = conn.execute(
        """SELECT client_request_public_id,status,amount,currency
           FROM payments
           WHERE lower(provider)='razorpay' AND provider_payment_id=?
           LIMIT 1""",
        (reference,),
    ).fetchone()
    if row is None:
        return False
    if row["client_request_public_id"] != expected_request:
        return False
    if str(row["status"] or "").lower() not in {"captured", "paid", "success"}:
        return False
    event_rows = conn.execute(
        """SELECT payload_json FROM webhook_events
           WHERE provider='razorpay' AND event_type='payment.captured' AND resource_id=?
           ORDER BY id DESC LIMIT 10""",
        (reference,),
    ).fetchall()
    for event in event_rows:
        try:
            payload = json.loads(event["payload_json"] or "{}")
            payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
            if payment.get("id") != reference:
                continue
            payer_email = str(payment.get("email") or "").strip().lower()
            if payer_email and payer_email != expected_email:
                return False
            if int(payment.get("amount") or 0) != int(row["amount"] or 0):
                return False
            if str(payment.get("currency") or "").upper() != str(row["currency"] or "").upper():
                return False
            return (
                payload.get("event") == "payment.captured"
                and str(payment.get("status", "")).lower() == "captured"
                and payment.get("captured") is not False
            )
        except (ValueError, TypeError, AttributeError):
            continue
    return False


@admin_bp.post("/owner/requests/<int:request_id>")
@_owner_required
def update_client_request(request_id: int):
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")
    action = (request.form.get("action") or "").strip()
    with connect() as conn:
        row = conn.execute("SELECT * FROM client_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            abort(404)
        public_id = row["public_id"]
        if action in {"save_review", "approve_and_send"}:
            scope_confirmed = 1 if request.form.get("scope_confirmed") == "yes" else 0
            payment_reference = (request.form.get("payment_reference") or "").strip()[:180]
            note = (request.form.get("approval_note") or "").strip()[:1000]
            conn.execute(
                """UPDATE client_requests SET scope_confirmed=?,payment_reference=?,approval_note=?,updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status NOT IN ('DELIVERED','REJECTED')""",
                (scope_confirmed, payment_reference or None, note or None, request_id),
            )
            _audit(conn, "review_details_saved", public_id, f"scope_confirmed={scope_confirmed};payment_reference_provided={bool(payment_reference)}")
            conn.commit()
            row = conn.execute("SELECT * FROM client_requests WHERE id=?", (request_id,)).fetchone()
            if action == "save_review":
                flash("Review details saved.", "success")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            if request.form.get("confirm_approval") != "yes":
                flash("Check the explicit approval confirmation before sending a file.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            if not row["scope_confirmed"]:
                flash("Approval blocked: confirm the written scope/agreement first.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            if not (row["payment_reference"] or "").strip():
                flash("Approval blocked: enter the verified captured Razorpay payment ID first.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            if not _verified_captured_payment(conn, row["payment_reference"], row["email"], row["public_id"]):
                flash("Approval blocked: that reference does not match a captured Razorpay payment record or a signed captured-payment event in this database. Verify/reconcile the payment before retrying.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            if not row["draft_content"] or not row["draft_filename"]:
                flash("This request has no generated file to deliver. Review the request manually.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            if row["status"] != "PAID":
                flash(f"Delivery blocked because this request is not in PAID state (current status: {row['status']}). Wait for the signed Razorpay captured-payment webhook.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            # Atomically claim delivery before any provider call so double-clicks cannot send twice.
            changed = conn.execute(
                "UPDATE client_requests SET status='SENDING',approved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='PAID'",
                (request_id,),
            ).rowcount
            if changed != 1:
                conn.commit()
                flash("Another action has already claimed this request. Refresh the dashboard.", "warning")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            _audit(conn, "delivery_approval_claimed", public_id, f"payment_reference={row['payment_reference']};scope_confirmed=true")
            conn.commit()
            # Keep the actual send outside the database transaction.
            try:
                send_result = deliver_approved_file(
                    to_email=row["email"],
                    business_name=row["business_name"],
                    public_id=public_id,
                    filename=row["draft_filename"],
                    content=row["draft_content"],
                    payment_reference=row["payment_reference"],
                )
            except Exception as exc:  # preserve the no-double-send rule on ambiguous exceptions
                send_result = {
                    "success": False,
                    "uncertain": True,
                    "reason": f"unhandled_provider_error_check_provider_activity:{type(exc).__name__}",
                }
            with connect() as update_conn:
                if send_result.get("success"):
                    status = "DELIVERED"
                    details = f"email_accepted;message_id={send_result.get('message_id') or 'unknown'}"
                    update_conn.execute(
                        "UPDATE client_requests SET status='DELIVERED',delivery_message_id=?,delivered_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='SENDING'",
                        (send_result.get("message_id"), request_id),
                    )
                    flash("Approved file was accepted by the email provider for delivery.", "success")
                elif send_result.get("uncertain"):
                    status = "DELIVERY_UNCERTAIN"
                    details = send_result.get("reason", "delivery_uncertain")
                    update_conn.execute(
                        "UPDATE client_requests SET status='DELIVERY_UNCERTAIN',approval_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='SENDING'",
                        (details[:1000], request_id),
                    )
                    flash("Provider outcome is uncertain. Check the email provider activity before attempting another send.", "warning")
                else:
                    status = "DELIVERY_FAILED"
                    details = send_result.get("reason", "delivery_failed")
                    update_conn.execute(
                        "UPDATE client_requests SET status='DELIVERY_FAILED',approval_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='SENDING'",
                        (details[:1000], request_id),
                    )
                    flash(f"File not sent: {details}", "error")
                _audit(update_conn, "delivery_send_result", public_id, f"status={status};{details}")
                update_conn.commit()
            return redirect(url_for("admin.owner_dashboard") + "#client-requests")
        elif action == "reject":
            if row["status"] in {"DELIVERED", "SENDING", "DELIVERY_UNCERTAIN", "PAID", "AWAITING_PAYMENT", "PAYMENT_ORDER_CREATING", "PAYMENT_ORDER_UNCERTAIN", "PAYMENT_LINK_EMAIL_UNCERTAIN"}:
                flash(f"Cannot reject a request in {row['status']} status. Reconcile payment/link activity first.", "error")
            else:
                note = (request.form.get("approval_note") or "Rejected during owner review.").strip()[:1000]
                conn.execute("UPDATE client_requests SET status='REJECTED',approval_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (note, request_id))
                _audit(conn, "request_rejected", public_id, note)
                conn.commit()
                flash("Request marked as rejected. No file was sent.", "success")
        elif action == "resolve_uncertain":
            if row["status"] != "DELIVERY_UNCERTAIN":
                flash("Only delivery-uncertain requests can be reconciled this way.", "error")
            else:
                decision = request.form.get("decision")
                note = (request.form.get("approval_note") or "").strip()[:1000]
                if decision not in {"confirmed_sent", "confirmed_not_sent"} or not note:
                    flash("Provide a reconciliation decision and note.", "error")
                elif decision == "confirmed_sent":
                    conn.execute("UPDATE client_requests SET status='DELIVERED',approval_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (note, request_id))
                    _audit(conn, "uncertain_delivery_confirmed_sent", public_id, note)
                    conn.commit()
                    flash("Request marked delivered after provider activity was reviewed.", "success")
                else:
                    conn.execute("UPDATE client_requests SET status='APPROVED',approval_note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (note, request_id))
                    _audit(conn, "uncertain_delivery_confirmed_not_sent", public_id, note)
                    conn.commit()
                    flash("Request returned to approved status. A new send attempt is now available.", "success")
        else:
            flash("Unknown request action.", "error")
        conn.commit()
    return redirect(url_for("admin.owner_dashboard") + "#client-requests")


@admin_bp.post("/owner/replies/process")
@_owner_required
def record_prospect_reply():
    """Manually record an inbox reply; never send an automatic response."""
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")
    if request.form.get("confirm_reply_belongs") != "yes":
        flash("No reply was recorded. Confirm that you verified which lead the reply belongs to.", "warning")
        return redirect(url_for("admin.owner_dashboard") + "#replies")
    try:
        lead_id = int(request.form.get("lead_id", ""))
    except ValueError:
        lead_id = 0
    from_email = (request.form.get("from_email") or "").strip().lower()[:254]
    subject = (request.form.get("subject") or "").strip()[:300]
    body = (request.form.get("body") or "").strip()[:5000]
    if lead_id < 1 or not from_email or "@" not in from_email or len(body) < 1:
        flash("Enter a valid lead ID, sender email, and reply body.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#replies")
    with connect() as conn:
        lead = conn.execute("SELECT id,email,business_name FROM leads WHERE id=?", (lead_id,)).fetchone()
        if lead is None:
            flash("That lead ID was not found.", "error")
            return redirect(url_for("admin.owner_dashboard") + "#replies")
        result = process_reply(conn, lead_id=lead_id, from_email=from_email, subject=subject, body=body, source="owner_dashboard_manual")
        _audit(conn, "prospect_reply_recorded", str(lead_id), f"intent={result.get('intent','unknown')};accepted={result.get('accepted')};duplicate={result.get('duplicate',False)}")
        conn.commit()
    status = "success" if result.get("accepted") else "error"
    flash(
        (f"Reply recorded for {lead['business_name']}: {result.get('intent') or result.get('reason')}. No automatic response was sent.")
        if result.get("accepted") else f"Reply not recorded: {result.get('reason','unknown error')}.",
        status,
    )
    _record_module_run("reply_processing", "success" if result.get("accepted") else "failed", f"lead_id={lead_id};intent={result.get('intent')};reason={result.get('reason')};automatic_response=disabled")
    return redirect(url_for("admin.owner_dashboard") + "#replies")


@admin_bp.post("/owner/market-strategy")
@_owner_required
def run_market_strategy_review():
    """Record market hypotheses and validation checks; never initiate outreach."""
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")
    country = (request.form.get("country") or "").strip()[:120]
    try:
        profiles = [get_market(country)] if country else list_markets()
        blocks = []
        for profile in profiles:
            blocks.append(
                f"{profile.country} | group={profile.group} | priority={profile.priority}\n"
                f"Language hypothesis: {profile.language_hypothesis}\n"
                f"Initial hypothesis (NOT verified): {profile.initial_hypothesis}\n"
                f"Risks: {', '.join(profile.main_risks)}\n"
                "Validation gates:\n" + "\n".join(f"- {item}" for item in profile.validation_checks)
            )
        output = "\n\n".join(blocks)
        summary = f"Reviewed {len(profiles)} country hypothesis profile(s); planning only; no outreach initiated."
        _record_module_run("market_strategy", "success", summary, output)
        flash(summary, "success")
    except ValueError as exc:
        _record_module_run("market_strategy", "failed", "Unknown market lookup", str(exc))
        flash("That country is not in the configured hypothesis list. Leave the field blank to list all profiles.", "error")
    return redirect(url_for("admin.owner_dashboard") + "#market-strategy")


@admin_bp.post("/owner/market-validation")
@_owner_required
def save_market_validation():
    """Record an operator attestation that a country's market gates were checked."""
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")
    country_input = (request.form.get("country") or "").strip()[:120]
    try:
        country = get_market(country_input).country
    except ValueError:
        flash("Choose a country from the configured market list.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#market-strategy")

    checks = {
        "payment_checked": request.form.get("payment_checked") == "yes",
        "language_checked": request.form.get("language_checked") == "yes",
        "reachability_checked": request.form.get("reachability_checked") == "yes",
        "competition_checked": request.form.get("competition_checked") == "yes",
        "demand_checked": request.form.get("demand_checked") == "yes",
        "compliance_checked": request.form.get("compliance_checked") == "yes",
        "unit_economics_checked": request.form.get("unit_economics_checked") == "yes",
    }
    notes = (request.form.get("evidence_notes") or "").strip()[:5000]
    all_checked = all(checks.values())
    status = "VALIDATED" if market_validation_complete(checks, notes) else "RESEARCH_REQUIRED"
    validated_at = datetime.now(timezone.utc).isoformat() if status == "VALIDATED" else None

    with connect() as conn:
        conn.execute(
            """INSERT INTO market_validations (
                   country,status,payment_checked,language_checked,reachability_checked,
                   competition_checked,demand_checked,compliance_checked,unit_economics_checked,
                   evidence_notes,validated_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(country) DO UPDATE SET
                   status=excluded.status,payment_checked=excluded.payment_checked,
                   language_checked=excluded.language_checked,reachability_checked=excluded.reachability_checked,
                   competition_checked=excluded.competition_checked,demand_checked=excluded.demand_checked,
                   compliance_checked=excluded.compliance_checked,unit_economics_checked=excluded.unit_economics_checked,
                   evidence_notes=excluded.evidence_notes,validated_at=excluded.validated_at,
                   updated_at=CURRENT_TIMESTAMP""",
            (country,status,*[int(value) for value in checks.values()],notes,validated_at),
        )
        _audit(conn, "market_validation_recorded", country, f"status={status};all_checks={all_checked};evidence_chars={len(notes)}")
        conn.commit()
    if status == "VALIDATED":
        flash(f"{country} is marked VALIDATED based on your checklist and evidence note. The operator remains responsible for the accuracy of this review.", "success")
    else:
        flash(f"{country} remains RESEARCH_REQUIRED. Check every gate and provide at least 30 characters of dated evidence notes before live outreach.", "warning")
    return redirect(url_for("admin.owner_dashboard") + "#market-strategy")


@admin_bp.post("/owner/leads/discover")
@_owner_required
def run_lead_discovery():
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")
    flash(
        "Manual Google Places discovery has been retired. "
        "The replacement Overture Maps acquisition worker is being validated "
        "before scheduled production automation is enabled.",
        "warning",
    )
    return redirect(url_for("admin.owner_dashboard") + "#lead-discovery")


@admin_bp.post("/owner/outreach/prepare")
@_owner_required
def prepare_outreach_records():
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")
    try:
        limit = int(request.form.get("limit", "5"))
    except ValueError:
        limit = 0
    if not 1 <= limit <= 10:
        flash("Preparation limit must be from 1 to 10.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#outreach")
    output = []
    prepared = skipped = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id,business_name,email FROM leads WHERE state=? ORDER BY score DESC,id ASC LIMIT ?",
            (LeadState.QUALIFIED.value, limit),
        ).fetchall()
        for lead in rows:
            result = prepare_first_contact(
                conn,
                int(lead["id"]),
                daily_limit=getattr(settings, "daily_limit", 25),
                domain_cooldown=getattr(settings, "domain_cooldown", 60),
            )
            if result.allowed:
                prepared += 1
                output.append(f"PREPARED lead_id={lead['id']} outreach_id={result.outreach_id} business={lead['business_name']}")
            else:
                skipped += 1
                output.append(f"SKIPPED lead_id={lead['id']} business={lead['business_name']} reason={result.reason}")
        conn.commit()
    summary = f"Prepared={prepared}; skipped={skipped}; considered={len(rows)}. No messages were sent."
    _record_module_run("outreach_preparation", "success", summary, "\n".join(output))
    flash(summary, "success")
    return redirect(url_for("admin.owner_dashboard") + "#outreach")


@admin_bp.post("/owner/outreach/send-one")
@_owner_required
def send_one_prepared_email():
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")
    if request.form.get("confirm_send") != "yes":
        flash("No email was sent. Confirm that you reviewed the prepared message first.", "warning")
        return redirect(url_for("admin.owner_dashboard") + "#outreach")
    provider = getattr(settings, "email_provider", "brevo")
    if provider == "brevo":
        configured = bool(getattr(settings, "brevo_api_key", None) and getattr(settings, "brevo_sender", None))
    else:
        configured = bool(getattr(settings, "sendgrid_api_key", None) and getattr(settings, "sendgrid_sender", None))
    if not configured:
        flash(f"Sending is disabled: {provider} credentials/sender are missing.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#outreach")
    with connect() as conn:
        prepared = conn.execute(
            """SELECT o.id AS outreach_id,l.country,l.business_name
               FROM outreach o JOIN leads l ON l.id=o.lead_id
               WHERE o.status='PREPARED' ORDER BY o.id ASC LIMIT 1"""
        ).fetchone()
        if prepared is None:
            flash("There are no PREPARED messages to send.", "warning")
            return redirect(url_for("admin.owner_dashboard") + "#outreach")
        if getattr(settings, "market_validation_required", True):
            country = (prepared["country"] or "India").strip() or "India"
            validation = conn.execute(
                "SELECT status FROM market_validations WHERE lower(country)=lower(?) LIMIT 1", (country,)
            ).fetchone()
            if validation is None or validation["status"] != "VALIDATED":
                flash(f"Sending is blocked: {country} is RESEARCH_REQUIRED. Complete and record the market validation checklist before sending.", "error")
                _record_module_run("outreach_delivery", "blocked", f"country={country}; reason=market_not_validated; outreach_id={prepared['outreach_id']}")
                return redirect(url_for("admin.owner_dashboard") + "#market-strategy")
        # A single message per explicit click limits risk and avoids long web requests.
        results = send_prepared_batch(conn, dry_run=False, limit=1, provider=provider, respect_rate_limits=True)
    details = "\n".join(
        f"outreach_id={item.outreach_id};success={item.success};reason={item.reason};provider={item.provider}"
        for item in results
    ) or "No item processed."
    success_count = sum(1 for item in results if item.success)
    summary = f"Processed {len(results)} prepared email(s); accepted by provider={success_count}. Check provider activity for actual receipt."
    _record_module_run("outreach_delivery", "success" if success_count else "failed", summary, details)
    flash(summary, "success" if success_count else "error")
    return redirect(url_for("admin.owner_dashboard") + "#outreach")


@admin_bp.get("/owner/requests/<int:request_id>/download")
@_owner_required
def download_draft(request_id: int):
    with connect() as conn:
        row = conn.execute("SELECT public_id,draft_filename,draft_content FROM client_requests WHERE id=?", (request_id,)).fetchone()
    if row is None or not row["draft_content"] or not row["draft_filename"]:
        abort(404)
    content = row["draft_content"].encode("utf-8")
    return send_file(
        io.BytesIO(content),
        mimetype="text/html",
        as_attachment=True,
        download_name=row["draft_filename"],
        max_age=0,
    )
