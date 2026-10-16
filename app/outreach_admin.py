"""Operator-only actions for safe outreach audit maintenance."""
from __future__ import annotations

from typing import Any

SUPPORTED_PROVIDERS = frozenset({"brevo", "sendgrid", "unknown"})


def annotate_failed_outreach(
    conn: Any,
    *,
    outreach_id: int,
    provider: str,
    reason: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Record diagnostics on an already FAILED row without retrying it."""
    if outreach_id < 1:
        return {"updated": False, "reason": "invalid_outreach_id"}
    if provider not in SUPPORTED_PROVIDERS:
        return {"updated": False, "reason": "unsupported_provider"}
    clean_reason = (reason or "").strip()
    if not clean_reason:
        return {"updated": False, "reason": "diagnostic_reason_missing"}
    if len(clean_reason) > 500:
        return {"updated": False, "reason": "diagnostic_reason_too_long"}
    if not confirmed:
        return {"updated": False, "reason": "confirmation_required"}

    row = conn.execute(
        "SELECT id, status FROM outreach WHERE id = ?", (outreach_id,)
    ).fetchone()
    if row is None:
        return {"updated": False, "reason": "outreach_not_found"}
    if row["status"] != "FAILED":
        return {"updated": False, "reason": "outreach_not_failed"}

    conn.execute(
        "UPDATE outreach SET provider = ?, last_error = ? WHERE id = ? AND status = 'FAILED'",
        (provider, clean_reason, outreach_id),
    )
    conn.commit()
    return {"updated": True, "reason": "annotated", "outreach_id": outreach_id}
