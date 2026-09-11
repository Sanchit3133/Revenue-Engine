"""Outreach orchestration boundary.

Phase 1 deliberately does not send email. It prepares and validates outreach
records so later SendGrid integration cannot accidentally be coupled to lead
discovery.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.lead_engine import is_valid_public_email, normalize_email


@dataclass(frozen=True)
class Message:
    email: str
    subject: str
    body: str


def validate_message(message: Message) -> tuple[bool, str]:
    email = normalize_email(message.email)

    if not is_valid_public_email(email):
        return False, "invalid_or_blocked_email"

    if not message.subject.strip():
        return False, "missing_subject"

    if not message.body.strip():
        return False, "missing_body"

    if len(message.subject) > 120:
        return False, "subject_too_long"

    if len(message.body) > 5000:
        return False, "body_too_long"

    # First-sale campaign: no unresolved purchase-link placeholder.
    if "[BUY_LINK]" in message.body:
        return False, "unresolved_buy_link"

    # Do not allow HTML in the first plain-text outreach layer.
    if "<html" in message.body.lower() or "<a " in message.body.lower():
        return False, "html_not_allowed"

    return True, ""


def campaign_capacity() -> int:
    return min(settings.campaign_limit, settings.daily_limit)
