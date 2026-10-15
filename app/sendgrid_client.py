"""SendGrid HTTP client for the Revenue Engine.

This module only handles communication with SendGrid's Mail Send API.
It does not decide whether a lead is eligible for outreach and it does
not change lead/outreach database state.

The campaign layer is responsible for:
    PREPARED -> send -> mark_sent()
    PREPARED -> failure -> mark_failed()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.outreach import Message


SENDGRID_MAIL_SEND_URL = "https://api.sendgrid.com/v3/mail/send"
DEFAULT_TIMEOUT = 20


@dataclass(frozen=True)
class SendGridResult:
    """Result returned by a SendGrid send attempt."""

    success: bool
    status_code: int | None
    message_id: str | None
    reason: str


def _clean(value: str | None) -> str:
    """Return a safely stripped string."""

    if value is None:
        return ""

    return value.strip()


def _validate_configuration(
    api_key: str | None,
    sender: str | None,
) -> tuple[bool, str]:
    """Validate the minimum SendGrid configuration."""

    if not _clean(api_key):
        return False, "sendgrid_api_key_missing"

    if not _clean(sender):
        return False, "sendgrid_sender_missing"

    return True, ""


def _build_payload(message: Message, sender: str, reply_to: str | None = None) -> dict[str, Any]:
    """Build the SendGrid v3 Mail Send JSON payload."""

    payload: dict[str, Any] = {
        "personalizations": [
            {
                "to": [
                    {
                        "email": message.email,
                    }
                ]
            }
        ],
        "from": {
            "email": sender,
        },
        "subject": message.subject,
        "content": [
            {
                "type": "text/plain",
                "value": message.body,
            }
        ],
    }

    cleaned_reply_to = _clean(reply_to)

    if cleaned_reply_to:
        payload["reply_to"] = {
            "email": cleaned_reply_to,
        }

    return payload


def send_message(
    message: Message | None,
    *,
    api_key: str | None,
    sender: str | None,
    reply_to: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: Any = requests,
) -> SendGridResult:
    """Send one validated plain-text message through SendGrid.

    This function does not update the Revenue Engine database.

    A successful SendGrid submission is HTTP 202 Accepted.
    """

    valid_config, config_reason = _validate_configuration(api_key, sender)

    if not valid_config:
        return SendGridResult(
            success=False,
            status_code=None,
            message_id=None,
            reason=config_reason,
        )

    # Reuse the Revenue Engine's existing outbound message validation.
    from app.outreach import validate_message

    valid_message, message_reason = validate_message(message)

    if not valid_message:
        return SendGridResult(
            success=False,
            status_code=None,
            message_id=None,
            reason=message_reason,
        )

    assert message is not None
    assert sender is not None
    assert api_key is not None

    payload = _build_payload(
        message,
        sender=sender.strip(),
        reply_to=reply_to,
    )

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }

    try:
        response = session.post(
            SENDGRID_MAIL_SEND_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return SendGridResult(
            success=False,
            status_code=None,
            message_id=None,
            reason=f"sendgrid_request_failed:{type(exc).__name__}",
        )

    message_id = response.headers.get("X-Message-Id")

    if response.status_code == 202:
        return SendGridResult(
            success=True,
            status_code=202,
            message_id=message_id,
            reason="accepted",
        )

    return SendGridResult(
        success=False,
        status_code=response.status_code,
        message_id=message_id,
        reason=f"sendgrid_http_{response.status_code}",
    )