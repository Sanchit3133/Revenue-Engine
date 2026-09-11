"""Reply classification boundary.

Automated reply ingestion/classification will be implemented after the
delivery-event layer is in place.
"""

from __future__ import annotations

from enum import StrEnum


class ReplyIntent(StrEnum):
    UNKNOWN = "UNKNOWN"
    INTERESTED = "INTERESTED"
    BUDGET_DISCLOSED = "BUDGET_DISCLOSED"
    NOT_INTERESTED = "NOT_INTERESTED"
    UNSUBSCRIBE = "UNSUBSCRIBE"


def classify_reply(subject: str, body: str) -> ReplyIntent:
    text = f"{subject}\n{body}".lower()

    if any(x in text for x in ("unsubscribe", "remove me", "do not contact")):
        return ReplyIntent.UNSUBSCRIBE

    if any(x in text for x in ("not interested", "no thanks", "don't need")):
        return ReplyIntent.NOT_INTERESTED

    # Deliberately conservative. Budget extraction belongs in a later phase.
    if any(x in text for x in ("budget", "₹", "rs ", "inr ")):
        return ReplyIntent.BUDGET_DISCLOSED

    if any(x in text for x in ("interested", "tell me more", "sounds good", "details")):
        return ReplyIntent.INTERESTED

    return ReplyIntent.UNKNOWN
