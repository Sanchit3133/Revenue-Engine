"""Persistent follow-up reminders for Revenue Engine outreach.

This module only manages the reminder queue. It never bypasses the sender's
existing message validation, suppression checks, campaign limits, or the
maximum of two follow-ups after the first contact.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

FOLLOW_UP_DELAYS_DAYS = {1: 4, 2: 5}
SCHEDULED = "SCHEDULED"
CLAIMED = "CLAIMED"
PREPARED = "PREPARED"
CANCELLED = "CANCELLED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"


def _utc_iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def schedule_next_follow_up(
    conn: Any,
    *,
    lead_id: int,
    source_outreach_id: int,
    accepted_attempt_number: int,
    sent_at: datetime | None = None,
    commit: bool = True,
) -> bool:
    """Schedule a reminder after a provider-accepted message.

    accepted_attempt_number=1 schedules follow-up #1 in four days.
    accepted_attempt_number=2 schedules follow-up #2 five days later.
    A third follow-up is never scheduled.
    """
    next_attempt = int(accepted_attempt_number) + 1
    delay = FOLLOW_UP_DELAYS_DAYS.get(int(accepted_attempt_number))
    if delay is None or next_attempt > 3:
        return False
    due_at = _utc_iso((sent_at or datetime.now(timezone.utc)) + timedelta(days=delay))
    cursor = conn.execute(
        """INSERT INTO follow_up_schedule
           (lead_id, source_outreach_id, attempt_number, due_at, status)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(lead_id, attempt_number) DO NOTHING""",
        (int(lead_id), int(source_outreach_id), next_attempt, due_at, SCHEDULED),
    )
    if commit:
        conn.commit()
    return cursor.rowcount == 1


def cancel_follow_ups(conn: Any, lead_id: int, *, reason: str, commit: bool = True) -> int:
    """Cancel any reminders not already completed for a lead."""
    cursor = conn.execute(
        """UPDATE follow_up_schedule
           SET status = ?, cancellation_reason = ?, updated_at = CURRENT_TIMESTAMP
           WHERE lead_id = ? AND status IN (?, ?, ?)""",
        (CANCELLED, str(reason)[:200], int(lead_id), SCHEDULED, CLAIMED, PREPARED),
    )
    if commit:
        conn.commit()
    return int(cursor.rowcount or 0)


def claim_due_follow_ups(conn: Any, *, limit: int = 5, now: datetime | None = None) -> list[dict[str, Any]]:
    """Atomically claim a bounded number of overdue reminders.

    Conditional updates make overlapping workers idempotent; only a worker
    that changes SCHEDULED to CLAIMED receives that record.
    """
    if not 1 <= int(limit) <= 25:
        raise ValueError("limit must be between 1 and 25")
    cutoff = _utc_iso(now)
    rows = conn.execute(
        """SELECT id, lead_id, source_outreach_id, attempt_number, due_at, prepared_outreach_id
           FROM follow_up_schedule
           WHERE status = ? AND due_at <= ?
           ORDER BY due_at ASC, id ASC
           LIMIT ?""",
        (SCHEDULED, cutoff, int(limit)),
    ).fetchall()
    claimed: list[dict[str, Any]] = []
    for row in rows:
        cursor = conn.execute(
            """UPDATE follow_up_schedule SET status = ?, claimed_at = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = ? AND due_at <= ?""",
            (CLAIMED, _utc_iso(now), int(row["id"]), SCHEDULED, cutoff),
        )
        if cursor.rowcount == 1:
            claimed.append(dict(row))
    conn.commit()
    return claimed


def mark_follow_up_prepared(conn: Any, schedule_id: int, outreach_id: int) -> bool:
    cursor = conn.execute(
        """UPDATE follow_up_schedule
           SET status = ?, prepared_outreach_id = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND status = ?""",
        (PREPARED, int(outreach_id), int(schedule_id), CLAIMED),
    )
    conn.commit()
    return cursor.rowcount == 1


def release_stale_claims(conn: Any, *, now: datetime | None = None, age_minutes: int = 60) -> int:
    """Recover abandoned claims without retrying an ambiguous provider submission."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    threshold = _utc_iso(current - timedelta(minutes=max(1, int(age_minutes))))
    now_text = _utc_iso(current)

    # A CLAIMED row has not yet created a reminder message, so it is safe to
    # release and prepare it again.
    unprepared = conn.execute(
        """UPDATE follow_up_schedule
           SET status = ?, due_at = ?, claimed_at = NULL,
               cancellation_reason = 'recovered_stale_claim', updated_at = CURRENT_TIMESTAMP
           WHERE status = ? AND claimed_at IS NOT NULL AND claimed_at < ?
             AND prepared_outreach_id IS NULL""",
        (SCHEDULED, now_text, CLAIMED, threshold),
    )

    # PREPARED reminders keep their original outreach row. If it remains
    # PREPARED, resume that exact row. If it reached SENDING, the provider
    # outcome may be ambiguous, so never resubmit automatically.
    prepared = conn.execute(
        """UPDATE follow_up_schedule AS s
           SET status = CASE
                 WHEN o.status = 'PREPARED' THEN ?
                 WHEN o.status = 'SENT' THEN ?
                 ELSE ?
               END,
               due_at = CASE WHEN o.status = 'PREPARED' THEN ? ELSE s.due_at END,
               cancellation_reason = CASE
                 WHEN o.status = 'PREPARED' THEN 'recovered_stale_prepared'
                 WHEN o.status = 'SENT' THEN 'reconciled_already_sent'
                 WHEN o.status = 'SENDING' THEN 'ambiguous_provider_state_after_crash'
                 ELSE 'linked_outreach_not_sendable_after_crash'
               END,
               claimed_at = NULL,
               updated_at = CURRENT_TIMESTAMP
           FROM outreach AS o
           WHERE s.prepared_outreach_id = o.id
             AND s.status = ?
             AND s.claimed_at IS NOT NULL
             AND s.claimed_at < ?""",
        (SCHEDULED, COMPLETED, FAILED, now_text, PREPARED, threshold),
    )
    conn.commit()
    return int(unprepared.rowcount or 0) + int(prepared.rowcount or 0)


def finish_follow_up(conn: Any, schedule_id: int, *, accepted: bool, reason: str = "") -> bool:
    status = COMPLETED if accepted else FAILED
    cursor = conn.execute(
        """UPDATE follow_up_schedule
           SET status = ?, cancellation_reason = ?, claimed_at = NULL, updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND status IN (?, ?)""",
        (status, str(reason)[:200] or None, int(schedule_id), CLAIMED, PREPARED),
    )
    conn.commit()
    return cursor.rowcount == 1


def release_claim(conn: Any, schedule_id: int, *, retry_at: datetime | None = None, reason: str) -> bool:
    """Release a claim after a transient preparation failure, without sending."""
    if retry_at is None:
        retry_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    cursor = conn.execute(
        """UPDATE follow_up_schedule
           SET status = ?, due_at = ?, cancellation_reason = ?, claimed_at = NULL, updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND status = ?""",
        (SCHEDULED, _utc_iso(retry_at), str(reason)[:200], int(schedule_id), CLAIMED),
    )
    conn.commit()
    return cursor.rowcount == 1


def cancel_due_to_terminal_state(conn: Any, lead_id: int, state: str) -> int:
    terminal = {"REPLIED", "INTERESTED", "BUDGET_DISCLOSED", "PAID",
                "BOUNCED", "COMPLAINT", "UNSUBSCRIBED", "NOT_INTERESTED",
                "DISQUALIFIED", "SUPPRESSED"}
    if str(state).upper() not in terminal:
        return 0
    return cancel_follow_ups(conn, lead_id, reason="lead_state:" + str(state).upper())
