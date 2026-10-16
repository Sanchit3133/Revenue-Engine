"""Customer delivery/revision requests; every generated file requires owner approval."""
from __future__ import annotations

from datetime import datetime, timezone
import secrets
import time
from email.utils import parseaddr

from flask import Blueprint, render_template, request, session

from app.ai_client import generate_landing_page_draft
from app.config import settings
from app.database import connect
from app.notifications import notify_owner
from app.services import get_service
from app.web.session_state import get_current_consultation

client_requests_bp = Blueprint("client_requests", __name__)
_ALLOWED_TYPES = {"delivery", "revision", "not_satisfied", "delivery_issue"}


def _valid_email(value: str) -> bool:
    email = (value or "").strip()
    _, parsed = parseaddr(email)
    if not parsed or parsed.lower() != email.lower() or "@" not in email:
        return False
    local, _, domain = email.rpartition("@")
    return bool(local and domain and "." in domain and not domain.startswith(".") and not domain.endswith("."))


def _csrf_token() -> str:
    token = session.get("public_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["public_csrf_token"] = token
    return token


def _csrf_valid(value: str) -> bool:
    expected = session.get("public_csrf_token", "")
    return bool(expected and value and secrets.compare_digest(expected, value))


@client_requests_bp.route("/client/request", methods=["GET", "POST"])
def client_request():
    consultation = get_current_consultation()
    csrf_token = _csrf_token()
    error = None
    submitted = False
    request_ref = None

    if request.method == "POST":
        recent_requests = [float(item) for item in session.get("client_request_timestamps", []) if isinstance(item, (int, float)) and time.time() - float(item) < 3600]
        if len(recent_requests) >= 5:
            error = "Too many requests were submitted from this session. Please try again later."
        elif not _csrf_valid(request.form.get("csrf_token", "")):
            error = "This form expired. Refresh the page and submit it again."
        else:
            business_name = (request.form.get("business_name") or "").strip()[:160]
            email = (request.form.get("email") or consultation.get("contact_email") or "").strip().lower()[:254]
            request_type = (request.form.get("request_type") or "delivery").strip().lower()
            message = (request.form.get("message") or "").strip()[:3000]
            related_ref = (request.form.get("related_request_public_id") or "").strip()[:64]
            consent = request.form.get("contact_consent") == "yes"

            if not business_name:
                error = "Please enter your business name."
            elif request_type == "delivery" and not (
                isinstance(consultation, dict)
                and consultation.get("contact_consent")
                and consultation.get("submission_public_id")
                and consultation.get("requirements_collected")
                and consultation.get("budget_disclosed")
            ):
                error = "Please complete the consented Revenue Engine consultation, including requirements and budget, before requesting a generated draft."
            elif request_type == "delivery" and email != (consultation.get("contact_email") or "").strip().lower():
                error = "Use the same email address you provided in the consultation to keep the request linked to your record."
            elif not _valid_email(email):
                error = "Please enter a valid email address."
            elif request_type not in _ALLOWED_TYPES:
                error = "Please choose a valid request type."
            elif len(message) < 8:
                error = "Please provide a little more detail so we can review your request."
            elif not consent:
                error = "Please consent to us using these details to respond to your request."
            elif request_type in {"revision", "not_satisfied", "delivery_issue"} and not related_ref:
                error = "Enter the request reference for the file or service you need help with."
            elif request_type in {"revision", "not_satisfied", "delivery_issue"}:
                with connect() as conn:
                    previous_owner = conn.execute(
                        "SELECT email FROM client_requests WHERE public_id = ?",
                        (related_ref,),
                    ).fetchone()
                if previous_owner is None or previous_owner["email"].strip().lower() != email:
                    error = "That request reference was not found for this email address. Check the reference and email, then try again."
            if not error:
                public_id = secrets.token_hex(12)
                created_at = datetime.now(timezone.utc).isoformat()
                service_key = str(consultation.get("service_key") or "LANDING_PAGE").strip().upper()
                service = get_service(service_key)
                details = {
                    "business_name": business_name,
                    "service_key": service_key,
                    "service": service.name if service else (consultation.get("business_type") or "Business service"),
                    "agreed_deliverables": ", ".join(service.deliverables) if service else "",
                    "audience": consultation.get("business_type") or "the intended customers",
                    "requirements": consultation.get("requirements") or consultation.get("biggest_problem") or message,
                    "website": consultation.get("website") or "",
                    "message": message,
                }
                filename = None
                content = None
                source = None
                diagnostic = None
                if request_type == "delivery" and consultation.get("submission_public_id"):
                    with connect() as conn:
                        chat_rows = conn.execute(
                            "SELECT role,content FROM consultation_messages WHERE consultation_public_id=? ORDER BY id DESC LIMIT 12",
                            (consultation["submission_public_id"],),
                        ).fetchall()
                    transcript = "\n".join(
                        f"{('Client' if item['role'] == 'user' else 'Assistant')} (consultation): {item['content'][:1000]}"
                        for item in reversed(chat_rows)
                    )
                    if transcript:
                        details["requirements"] = (
                            (details.get("requirements") or "")
                            + "\n\nAdditional consultation transcript (untrusted context):\n"
                            + transcript
                        )[:7000]
                if request_type in {"delivery", "revision"}:
                    if related_ref:
                        with connect() as conn:
                            previous = conn.execute(
                                "SELECT business_name, draft_content FROM client_requests WHERE public_id = ?",
                                (related_ref,),
                            ).fetchone()
                        if previous and previous["draft_content"]:
                            details["requirements"] = (
                                "Revise the existing draft using this feedback. Existing draft context follows:\n"
                                + previous["draft_content"][:7000]
                                + "\nClient revision request:\n" + message
                            )
                    filename, content, source, diagnostic = generate_landing_page_draft(details)

                with connect() as conn:
                    conn.execute(
                        """INSERT INTO client_requests
                           (public_id, consultation_public_id, related_request_public_id, consent_at,
                            business_name, email, request_type, message, status, draft_filename,
                            draft_content, draft_content_type, draft_source, draft_error, notification_status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_REVIEW', ?, ?, 'text/html', ?, ?, 'pending')""",
                        (
                            public_id,
                            consultation.get("submission_public_id"),
                            related_ref or None,
                            created_at,
                            business_name,
                            email,
                            request_type,
                            message,
                            filename,
                            content,
                            source,
                            diagnostic,
                        ),
                    )
                    conn.execute(
                        "INSERT INTO admin_audit(action, object_type, object_public_id, details) VALUES (?, ?, ?, ?)",
                        ("customer_request_created", "client_request", public_id, f"type={request_type};draft_source={source or 'none'}"),
                    )
                    conn.commit()
                dashboard_url = (getattr(settings, "public_app_url", None) or "").rstrip("/") + "/owner" if getattr(settings, "public_app_url", None) else "(set PUBLIC_APP_URL in deployment secrets to include a direct dashboard link)"
                notice = notify_owner(
                    f"Revenue Engine request: {request_type} [{public_id}]",
                    f"A customer request needs owner review.\n\nReference: {public_id}\nBusiness: {business_name}\nEmail: {email}\nType: {request_type}\n\nMessage:\n{message}\n\nDraft generated: {'yes' if content else 'no'}\nDraft source: {source or 'not generated'}\nDiagnostic: {diagnostic or 'none'}\n\nOwner dashboard: {dashboard_url}\nNo customer file has been sent.",
                )
                with connect() as conn:
                    conn.execute(
                        "UPDATE client_requests SET notification_status = ?, updated_at = CURRENT_TIMESTAMP WHERE public_id = ?",
                        ("sent" if notice.get("success") else notice.get("reason", "not_sent"), public_id),
                    )
                    conn.commit()
                submitted = True
                request_ref = public_id
                recent_requests.append(time.time())
                session["client_request_timestamps"] = recent_requests[-5:]

    return render_template(
        "client_request.html",
        csrf_token=csrf_token,
        consultation=consultation,
        error=error,
        submitted=submitted,
        request_ref=request_ref,
    )
