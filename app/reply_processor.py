"""Persist and apply manually ingested prospect replies without auto-responding."""

from __future__ import annotations
import hashlib
import json
from typing import Any

from app.models import LeadState
from app.pipeline import TERMINAL_STATES, transition
from app.replies import BudgetSignal, ReplyIntent, classify_reply, extract_budget_signal


def _key(lead_id: int, from_email: str, subject: str, body: str) -> str:
    raw = json.dumps(
        {"lead_id": lead_id, "from": from_email.strip().lower(), "subject": subject.strip(), "body": body.strip()},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def process_reply(
    conn: Any,
    *,
    lead_id: int,
    from_email: str,
    subject: str,
    body: str,
    source: str = "manual",
) -> dict[str, Any]:
    lead = conn.execute("SELECT id, email, state FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if lead is None:
        return {"accepted": False, "reason": "lead_not_found"}
    clean_from = (from_email or lead["email"] or "").strip().lower()
    clean_subject = (subject or "").strip()
    clean_body = (body or "").strip()
    if not clean_body:
        return {"accepted": False, "reason": "reply_body_missing"}

    intent = classify_reply(clean_subject, clean_body)
    budget = extract_budget_signal(clean_subject, clean_body)
    key = _key(lead_id, clean_from, clean_subject, clean_body)
    cursor = conn.execute(
        """INSERT OR IGNORE INTO reply_events
           (lead_id, idempotency_key, from_email, subject, body, intent, budget_signal, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (lead_id, key, clean_from, clean_subject, clean_body, intent.value, budget.value if budget.disclosed else None, source),
    )
    if cursor.rowcount == 0:
        conn.commit()
        return {"accepted": True, "duplicate": True, "intent": intent.value, "reason": "reply_already_recorded"}

    if intent == ReplyIntent.UNSUBSCRIBE:
        conn.execute(
            """INSERT INTO suppressions(email, reason, source) VALUES (?, 'unsubscribe_reply', ?)
               ON CONFLICT(email) DO UPDATE SET reason = 'unsubscribe_reply', source = excluded.source""",
            (clean_from, source),
        )
        _transition_if_allowed(conn, lead_id, LeadState.UNSUBSCRIBED, "prospect_unsubscribed")
    elif intent == ReplyIntent.NOT_INTERESTED:
        _transition_if_allowed(conn, lead_id, LeadState.NOT_INTERESTED, "prospect_not_interested")
    else:
        _transition_if_allowed(conn, lead_id, LeadState.REPLIED, "prospect_replied")
        if intent == ReplyIntent.INTERESTED:
            _transition_if_allowed(conn, lead_id, LeadState.INTERESTED, "reply_indicates_interest")
        elif intent == ReplyIntent.BUDGET_DISCLOSED:
            _transition_if_allowed(conn, lead_id, LeadState.BUDGET_DISCLOSED, "reply_disclosed_budget")

    conn.commit()
    return {
        "accepted": True,
        "duplicate": False,
        "intent": intent.value,
        "budget_disclosed": budget.disclosed,
        "budget_value": budget.value,
        "reason": "reply_processed_no_automatic_response",
    }


def _transition_if_allowed(conn, lead_id: int, new_state: LeadState, reason: str) -> None:
    row = conn.execute("SELECT state FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        return
    try:
        old = LeadState(row["state"])
    except ValueError:
        return
    if old in TERMINAL_STATES or old == new_state:
        return
    transition(conn, lead_id, new_state, reason=reason)
