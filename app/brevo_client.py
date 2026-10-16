"""Brevo transactional-email HTTP client.

This module never changes Revenue Engine database state. It only submits one
validated plain-text message and reports the provider's response.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

import requests

from app.outreach import Message, validate_message

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
DEFAULT_TIMEOUT = 20


@dataclass(frozen=True)
class BrevoResult:
    success: bool
    status_code: int | None
    message_id: str | None
    reason: str


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _validate_configuration(
    api_key: str | None,
    sender: str | None,
) -> tuple[bool, str]:
    if not _clean(api_key):
        return False, "brevo_api_key_missing"
    if not _clean(sender):
        return False, "brevo_sender_missing"
    _, address = parseaddr(_clean(sender))
    if not address or "@" not in address:
        return False, "brevo_sender_invalid"
    return True, ""


def _sender_fields(sender: str) -> dict[str, str]:
    display_name, address = parseaddr(sender.strip())
    # Accept a raw email or the familiar "Name <email@example.com>" form.
    if not address:
        address = sender.strip()
    fields = {"email": address}
    if display_name:
        fields["name"] = display_name
    return fields


def _build_payload(
    message: Message,
    sender: str,
    reply_to: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sender": _sender_fields(sender),
        "to": [{"email": message.email}],
        "subject": message.subject,
        "textContent": message.body,
    }
    reply = _clean(reply_to)
    if reply:
        _, reply_email = parseaddr(reply)
        payload["replyTo"] = {"email": reply_email or reply}
    return payload


def send_message(
    message: Message | None,
    *,
    api_key: str | None,
    sender: str | None,
    reply_to: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: Any = requests,
) -> BrevoResult:
    """Submit a validated message via Brevo; HTTP 201 means accepted."""
    valid_config, config_reason = _validate_configuration(api_key, sender)
    if not valid_config:
        return BrevoResult(False, None, None, config_reason)

    valid_message, message_reason = validate_message(message)
    if not valid_message:
        return BrevoResult(False, None, None, message_reason)

    assert message is not None
    assert api_key is not None
    assert sender is not None
    payload = _build_payload(message, sender, reply_to)
    try:
        response = session.post(
            BREVO_SEND_URL,
            headers={
                "api-key": api_key.strip(),
                "accept": "application/json",
                "content-type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return BrevoResult(
            False,
            None,
            None,
            f"brevo_request_failed:{type(exc).__name__}",
        )

    message_id = None
    try:
        response_data = response.json()
        message_id = response_data.get("messageId") if isinstance(response_data, dict) else None
    except (ValueError, AttributeError):
        response_data = {}

    if response.status_code == 201:
        return BrevoResult(True, 201, message_id, "accepted")

    # Keep the result machine-readable without persisting arbitrary response
    # bodies, which may contain details not needed in our audit trail.
    return BrevoResult(False, response.status_code, message_id, f"brevo_http_{response.status_code}")
