"""Owner notifications and manually approved file delivery through Brevo."""
from __future__ import annotations

import base64
import re
from typing import Any

import requests

from app.config import settings

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


def _email_ok(value: str | None) -> bool:
    value = (value or "").strip()
    return bool(re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value))


def send_brevo_email(
    *, to_email: str, subject: str, body: str, attachment_name: str | None = None,
    attachment_content: str | None = None, timeout: int = 18, session: Any = requests,
) -> dict[str, Any]:
    """Submit email. Does not update order state; caller owns the transaction."""
    token = getattr(settings, "brevo_api_key", None)
    sender = getattr(settings, "brevo_sender", None)
    if not token or not sender:
        return {"success": False, "reason": "brevo_not_configured", "uncertain": False}
    if not _email_ok(to_email) or not _email_ok(str(sender).split("<")[-1].replace(">", "").strip()):
        return {"success": False, "reason": "invalid_email_configuration", "uncertain": False}
    payload: dict[str, Any] = {
        "sender": {"email": str(sender).split("<")[-1].replace(">", "").strip()},
        "to": [{"email": to_email.strip()}],
        "subject": subject[:180],
        "textContent": body[:12000],
    }
    if attachment_name and attachment_content is not None:
        # Keep attachment bounded; generated HTML is served as a download, not rendered inline.
        encoded = base64.b64encode(attachment_content.encode("utf-8")).decode("ascii")
        payload["attachment"] = [{"name": attachment_name[:120], "content": encoded}]
    reply_to = getattr(settings, "brevo_reply_to", None)
    if reply_to and _email_ok(reply_to):
        payload["replyTo"] = {"email": reply_to}
    try:
        response = session.post(
            BREVO_SEND_URL,
            headers={"api-key": str(token), "accept": "application/json", "content-type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout:
        # A timeout cannot prove whether the provider accepted it. Do not auto-retry.
        return {"success": False, "reason": "provider_timeout_check_activity_before_retry", "uncertain": True}
    except requests.RequestException as exc:
        return {"success": False, "reason": f"provider_request_failed:{type(exc).__name__}", "uncertain": True}
    message_id = None
    try:
        data = response.json()
        if isinstance(data, dict):
            message_id = data.get("messageId")
    except (ValueError, AttributeError):
        pass
    if response.status_code == 201:
        return {"success": True, "reason": "accepted", "message_id": message_id, "uncertain": False}
    if response.status_code >= 500:
        return {"success": False, "reason": f"brevo_http_{response.status_code}_check_activity_before_retry", "uncertain": True}
    return {"success": False, "reason": f"brevo_http_{response.status_code}", "message_id": message_id, "uncertain": False}


def notify_owner(subject: str, body: str) -> dict[str, Any]:
    recipient = getattr(settings, "owner_notification_email", None)
    if not recipient:
        return {"success": False, "reason": "OWNER_NOTIFICATION_EMAIL_not_configured", "uncertain": False}
    return send_brevo_email(to_email=recipient, subject=subject, body=body)


def deliver_approved_file(*, to_email: str, business_name: str, public_id: str,
                          filename: str, content: str, payment_reference: str) -> dict[str, Any]:
    subject = f"Your reviewed project draft — {business_name[:80]}"
    body = (
        f"Hello {business_name},\n\n"
        "Your requested project draft has been reviewed and approved for delivery. It is attached to this email.\n\n"
        "Please review all details and contact us if you need a correction within the agreed scope. "
        "The attached HTML is a draft/blueprint and is not a live website, connected payment form, lead-capture endpoint, or activated automation unless the agreed integration work has been implemented and tested separately.\n\n"
        f"Request reference: {public_id}\n"
        "Thank you.\nRevenue Engine"
    )
    return send_brevo_email(
        to_email=to_email,
        subject=subject,
        body=body,
        attachment_name=filename,
        attachment_content=content,
    )
