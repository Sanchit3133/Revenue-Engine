"""State constants and lightweight domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LeadState(StrEnum):
    DISCOVERED = "DISCOVERED"
    QUALIFIED = "QUALIFIED"
    VERIFIED = "VERIFIED"
    CONTACTED = "CONTACTED"
    DELIVERED = "DELIVERED"
    REPLIED = "REPLIED"
    INTERESTED = "INTERESTED"
    BUDGET_DISCLOSED = "BUDGET_DISCLOSED"
    PROPOSAL = "PROPOSAL"
    PAID = "PAID"

    BOUNCED = "BOUNCED"
    COMPLAINT = "COMPLAINT"
    UNSUBSCRIBED = "UNSUBSCRIBED"
    NOT_INTERESTED = "NOT_INTERESTED"
    DISQUALIFIED = "DISQUALIFIED"
    SUPPRESSED = "SUPPRESSED"


@dataclass(frozen=True)
class Lead:
    id: int | None
    business_name: str
    email: str | None
    website: str | None
    qualification: str | None
    score: int
    signal: str | None
    state: LeadState
