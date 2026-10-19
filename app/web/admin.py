"""Authenticated owner dashboard and explicit delivery approval workflow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from functools import wraps
import io
import hashlib
import secrets
import subprocess
import sys
from pathlib import Path
from flask import url_for

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session

from app.config import settings
from app.database import connect
from app.notifications import deliver_approved_file, send_brevo_email
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
            "pending_requests": conn.execute("SELECT COUNT(*) FROM client_requests WHERE status IN ('PENDING_REVIEW','APPROVED','DELIVERY_UNCERTAIN')").fetchone()[0],
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
            "SELECT id,public_id,business_name,email,request_type,status,draft_source,draft_error,scope_confirmed,payment_reference,created_at FROM client_requests ORDER BY id DESC LIMIT 15"
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


def _verified_captured_payment(conn, payment_reference: str, customer_email: str) -> bool:
    """Require a known captured payment ID and reject known payer mismatches.

    A payment may be in the payments table, or may be a captured, signed Razorpay
    event retained in webhook_events while awaiting explicit reconciliation. If
    the webhook provides payer email, it must match the customer's request email.
    """
    reference = (payment_reference or "").strip()
    expected_email = (customer_email or "").strip().lower()
    if not reference or not expected_email:
        return False
    row = conn.execute(
        """SELECT 1 FROM payments
           WHERE lower(provider)='razorpay' AND provider_payment_id=?
             AND lower(COALESCE(status,'')) IN ('captured','paid','success')
           LIMIT 1""",
        (reference,),
    ).fetchone()
    payment_known = row is not None
    events = conn.execute(
        """SELECT payload_json FROM webhook_events
           WHERE provider='razorpay' AND event_type='payment.captured' AND resource_id=?
           ORDER BY id DESC LIMIT 10""",
        (reference,),
    ).fetchall()
    for event in events:
        try:
            payload = json.loads(event["payload_json"] or "{}")
            payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
            captured = (
                payload.get("event") == "payment.captured"
                and payment.get("id") == reference
                and str(payment.get("status", "")).lower() == "captured"
                and payment.get("captured") is not False
                and int(payment.get("amount") or 0) > 0
            )
            if not captured:
                continue
            payment_known = True
            payer_email = str(payment.get("email") or "").strip().lower()
            if payer_email and payer_email != expected_email:
                return False
        except (ValueError, TypeError, AttributeError):
            continue
    return payment_known



@admin_bp.post("/owner/requests/<int:request_id>/quote")
@_owner_required
def create_request_quote(request_id: int):
    """Create and deliberately email an owner-priced, expiring customer quote."""
    if not _csrf_valid(request.form.get("csrf_token", "")):
        abort(400, description="Invalid CSRF token")

    raw_amount = (request.form.get("quote_amount") or "").strip().replace(",", "")
    scope_summary = (request.form.get("quote_scope") or "").strip()[:2500]
    note = (request.form.get("quote_note") or "").strip()[:1000]
    if request.form.get("confirm_scope") != "yes":
        flash("Quote blocked: confirm that the written scope and agreement are on record.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")
    if not scope_summary or len(scope_summary) < 12:
        flash("Add a clear scope summary before sending a quote.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")
    try:
        rupees = Decimal(raw_amount)
        if not rupees.is_finite() or rupees <= 0 or rupees > Decimal("10000000") or rupees.as_tuple().exponent < -2:
            raise InvalidOperation
        amount_minor = int(rupees * 100)
    except (InvalidOperation, ValueError):
        flash("Enter a valid INR quote amount with at most two decimal places.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")

    app_url = (getattr(settings, "public_app_url", None) or "").strip().rstrip("/")
    if not app_url.startswith("https://"):
        flash("Quote blocked: set PUBLIC_APP_URL to the published HTTPS URL.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")
    if not (getattr(settings, "brevo_api_key", None) and getattr(settings, "brevo_sender", None)):
        flash("Quote blocked: configure the Brevo API key and sender in the published app secrets first.", "error")
        return redirect(url_for("admin.owner_dashboard") + "#client-requests")

    with connect() as conn:
        row = conn.execute("SELECT * FROM client_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            abort(404)
        if row["request_type"] != "delivery":
            flash("Quotes can only be created for a delivery request.", "error")
            return redirect(url_for("admin.owner_dashboard") + "#client-requests")
        if not row["draft_filename"] or not row["draft_content"]:
            flash("Quote blocked: generate and review a private draft first.", "error")
            return redirect(url_for("admin.owner_dashboard") + "#client-requests")
        if row["status"] in {"DELIVERED", "SENDING", "DELIVERY_UNCERTAIN", "REJECTED"}:
            flash(f"Quote blocked because the request is {row['status']}.", "error")
            return redirect(url_for("admin.owner_dashboard") + "#client-requests")

        prior = conn.execute(
            "SELECT id,status FROM client_orders WHERE client_request_id=? ORDER BY id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        if prior and prior["status"] not in {"QUOTE_EMAIL_FAILED", "ORDER_CREATE_FAILED", "EXPIRED"}:
            flash(
                "An existing quote/payment attempt is active or needs reconciliation. Check its status before creating another.",
                "warning",
            )
            return redirect(url_for("admin.owner_dashboard") + "#client-requests")
        if prior:
            conn.execute(
                "UPDATE client_orders SET status='EXPIRED',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (prior["id"],),
            )

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        public_id = secrets.token_hex(12)
        expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        conn.execute(
            """INSERT INTO client_orders
               (public_id,client_request_id,token_hash,amount,currency,scope_summary,status,expires_at)
               VALUES(?,?,?,?,?,?,'QUOTE_PREPARED',?)""",
            (public_id, request_id, token_hash, amount_minor, "INR", scope_summary, expiry),
        )
        conn.execute(
            """UPDATE client_requests SET scope_confirmed=1,approval_note=?,updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (note or "Written scope confirmed before quote.", request_id),
        )
        _audit(conn, "quote_prepared", public_id, f"request_id={request_id};amount_minor={amount_minor};currency=INR")
        conn.commit()
        business_name = row["business_name"]
        email = row["email"]
        request_ref = row["public_id"]

    payment_url = f"{app_url}/pay/{raw_token}"
    subject = f"Your Revenue Engine quote [{request_ref}]"
    body = (
        f"Hello {business_name},\n\n"
        "Here is the quote for the scope discussed with Revenue Engine.\n\n"
        f"Request reference: {request_ref}\n"
        f"Quote reference: {public_id}\n"
        f"Amount due: INR {rupees:,.2f}\n"
        f"Scope: {scope_summary}\n"
        "Payment link (valid for 7 days):\n"
        f"{payment_url}\n\n"
        "The payment page will show the approved amount before checkout. A draft or deliverable is not released automatically after payment; Revenue Engine will verify capture and complete owner review first. "
        "This quote covers the scope listed above. Any hosting, domain, integrations, or full production deployment are included only if explicitly listed in that scope.\n\n"
        "If anything looks incorrect, please reply before paying.\nRevenue Engine"
    )
    try:
        sent = send_brevo_email(to_email=email, subject=subject, body=body)
    except Exception as exc:
        sent = {"success": False, "uncertain": True, "reason": f"provider_error:{type(exc).__name__}"}

    if sent.get("success"):
        status = "QUOTED"
        message = "Quote email was accepted by Brevo. Payment will not be marked captured until Razorpay confirms it."
    elif sent.get("uncertain"):
        status = "QUOTE_EMAIL_UNCERTAIN"
        message = "Quote email outcome is uncertain. Check Brevo activity before sending another quote."
    else:
        status = "QUOTE_EMAIL_FAILED"
        message = "Quote email was not accepted. Check configuration and provider activity before retrying."
    with connect() as conn:
        conn.execute(
            """UPDATE client_orders SET status=?,quote_sent_at=?,error_note=?,updated_at=CURRENT_TIMESTAMP
               WHERE public_id=?""",
            (
                status,
                datetime.now(timezone.utc).isoformat() if sent.get("success") else None,
                None if sent.get("success") else str(sent.get("reason") or status)[:500],
                public_id,
            ),
        )
        _audit(conn, "quote_email_result", public_id, f"status={status};provider_message_id={sent.get('message_id') or 'none'}")
        conn.commit()
    flash(message, "success" if sent.get("success") else "warning" if sent.get("uncertain") else "error")
    return redirect(url_for("admin.owner_dashboard") + "#client-requests")


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
            if not _verified_captured_payment(conn, row["payment_reference"], row["email"]):
                flash("Approval blocked: that reference does not match a captured Razorpay payment record or a signed captured-payment event in this database. Verify/reconcile the payment before retrying.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            if not row["draft_content"] or not row["draft_filename"]:
                flash("This request has no generated file to deliver. Review the request manually.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            if row["status"] not in {"PENDING_REVIEW", "APPROVED", "DELIVERY_FAILED"}:
                flash(f"Delivery blocked because request status is {row['status']}.", "error")
                return redirect(url_for("admin.owner_dashboard") + "#client-requests")
            # Atomically claim delivery before any provider call so double-clicks cannot send twice.
            changed = conn.execute(
                "UPDATE client_requests SET status='SENDING',approved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('PENDING_REVIEW','APPROVED','DELIVERY_FAILED')",
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
            if row["status"] in {"DELIVERED", "SENDING", "DELIVERY_UNCERTAIN"}:
                flash(f"Cannot reject a request in {row['status']} status.", "error")
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
