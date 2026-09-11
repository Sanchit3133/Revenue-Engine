"""Deliverability policy gates.

This module does not attempt to bypass spam controls. It prevents our own
system from making avoidable mistakes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliverabilityDecision:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate(
    *,
    email_valid: bool,
    suppressed: bool,
    duplicate: bool,
    body_has_html: bool,
    has_unresolved_placeholder: bool,
) -> DeliverabilityDecision:
    reasons: list[str] = []

    if not email_valid:
        reasons.append("invalid_email")
    if suppressed:
        reasons.append("suppressed_recipient")
    if duplicate:
        reasons.append("already_contacted")
    if body_has_html:
        reasons.append("html_not_allowed")
    if has_unresolved_placeholder:
        reasons.append("unresolved_placeholder")

    return DeliverabilityDecision(
        allowed=not reasons,
        reasons=tuple(reasons),
    )
