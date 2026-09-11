"""Revenue pipeline state transitions."""

from __future__ import annotations

import sqlite3

from app.models import LeadState


TERMINAL_STATES = {
    LeadState.PAID,
    LeadState.BOUNCED,
    LeadState.COMPLAINT,
    LeadState.UNSUBSCRIBED,
    LeadState.NOT_INTERESTED,
    LeadState.DISQUALIFIED,
    LeadState.SUPPRESSED,
}


def transition(
    conn: sqlite3.Connection,
    lead_id: int,
    new_state: LeadState,
    reason: str = "",
) -> None:
    row = conn.execute(
        "SELECT state FROM leads WHERE id = ?",
        (lead_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Lead {lead_id} does not exist")

    old_state = LeadState(row["state"])

    if old_state in TERMINAL_STATES and new_state != old_state:
        raise ValueError(
            f"Cannot transition terminal lead {lead_id} "
            f"from {old_state} to {new_state}"
        )

    conn.execute(
        """
        UPDATE leads
        SET state = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_state.value, lead_id),
    )

    conn.execute(
        """
        INSERT INTO state_events (lead_id, old_state, new_state, reason)
        VALUES (?, ?, ?, ?)
        """,
        (lead_id, old_state.value, new_state.value, reason),
    )

    conn.commit()
