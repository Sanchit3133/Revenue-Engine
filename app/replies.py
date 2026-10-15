"""Conservative inbound-reply classification.

Reply classification is intentionally separate from sending.

The classifier identifies conversation signals; it does not independently
decide pricing, proposals, discounts, or implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ReplyIntent(StrEnum):
    UNKNOWN = "UNKNOWN"
    INTERESTED = "INTERESTED"
    BUDGET_DISCLOSED = "BUDGET_DISCLOSED"
    NOT_INTERESTED = "NOT_INTERESTED"
    UNSUBSCRIBE = "UNSUBSCRIBE"


@dataclass(frozen=True)
class BudgetSignal:
    disclosed: bool
    value: str | None = None


UNSUBSCRIBE_PHRASES = (
    "unsubscribe",
    "remove me",
    "do not contact",
    "don't contact me",
    "stop contacting",
    "stop emailing",
    "stop emailing me",
    "take me off your list",
)

NOT_INTERESTED_PHRASES = (
    "not interested",
    "no thanks",
    "don't need",
    "do not need",
    "not required",
    "no requirement",
)

INTEREST_PHRASES = (
    "interested",
    "tell me more",
    "sounds good",
    "sounds interesting",
    "send details",
    "more details",
    "more information",
    "how does it work",
    "let's discuss",
    "lets discuss",
    "can we discuss",
)

CURRENCY_PATTERN = re.compile(
    r"""
    (?:
        ₹\s*
        |
        \b(?:rs\.?|inr)\s*
    )
    (
        \d+(?:[,\d]*(?:\.\d+)?)?
        |
        \d+(?:\.\d+)?\s*(?:k|thousand|lakh|lac)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_reply_text(subject: str, body: str) -> str:
    return f"{subject or ''}\n{body or ''}".strip().lower()


def extract_budget_signal(
    subject: str,
    body: str,
) -> BudgetSignal:
    """Detect an explicit monetary budget signal.

    Merely mentioning the word 'budget' is not treated as a disclosed amount.
    """

    text = normalize_reply_text(subject, body)

    match = CURRENCY_PATTERN.search(text)

    if match:
        return BudgetSignal(
            disclosed=True,
            value=match.group(0).strip(),
        )

    # Explicit numeric budget language can also count when a number is
    # directly associated with the word budget.
    explicit_budget = re.search(
        r"""
        \bbudget\b
        [^\n]{0,50}?
        (\d+(?:[,\d]*(?:\.\d+)?)?)
        """,
        text,
        re.IGNORECASE | re.VERBOSE,
    )

    if explicit_budget:
        return BudgetSignal(
            disclosed=True,
            value=explicit_budget.group(1),
        )

    return BudgetSignal(disclosed=False)


def classify_reply(
    subject: str,
    body: str,
) -> ReplyIntent:
    """Classify a reply using conservative precedence rules."""

    text = normalize_reply_text(subject, body)

    # The outbound message explicitly asks recipients to reply STOP. Treat a
    # bare STOP (including quoted/replied punctuation) as an opt-out, without
    # treating unrelated sentences that merely contain the word "stop" as one.
    if re.fullmatch(r"[\s>*'\"]*stop[\s.!?]*", text, flags=re.IGNORECASE):
        return ReplyIntent.UNSUBSCRIBE

    if any(phrase in text for phrase in UNSUBSCRIBE_PHRASES):
        return ReplyIntent.UNSUBSCRIBE

    if any(phrase in text for phrase in NOT_INTERESTED_PHRASES):
        return ReplyIntent.NOT_INTERESTED

    budget = extract_budget_signal(subject, body)

    if budget.disclosed:
        return ReplyIntent.BUDGET_DISCLOSED

    if any(phrase in text for phrase in INTEREST_PHRASES):
        return ReplyIntent.INTERESTED

    return ReplyIntent.UNKNOWN
