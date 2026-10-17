"""Server-side consultation state helpers.

Flask's default session cookie is client-side and has a small practical size
limit. Store the potentially growing consultation JSON in SQLite and keep only
its random public ID plus CSRF/rate-limit metadata in the signed cookie.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import session

from app.consultation import ConsultationStage
from app.database import connect
from app.followup_scheduler import cancel_follow_ups


def default_consultation() -> dict[str, Any]:
    return {
        "stage": ConsultationStage.FIRST_CONTACT.value,
        "business_name": "",
        "contact_email": "",
        "contact_consent": False,
        "submission_public_id": None,
        "lead_id": None,
        "handoff_token_hash": None,
        "consent_at": None,
        "business_type": "",
        "website": "",
        "customer_process": "",
        "biggest_problem": "",
        "current_tools": "",
        "answers": [],
        "question_index": 0,
        "service_key": None,
        "problem_identified": False,
        "solution_explained": False,
        "implementation_interest": False,
        "requirements_collected": False,
        "budget_disclosed": False,
        "written_agreement": False,
        "follow_up_count": 0,
    }


def get_current_consultation() -> dict[str, Any]:
    """Load this browser session's consultation record, or return a new state."""
    public_id = session.get("consultation_public_id")
    if not isinstance(public_id, str) or not public_id:
        return default_consultation()

    with connect() as conn:
        row = conn.execute(
            "SELECT data_json FROM consultation_submissions WHERE public_id = ?",
            (public_id,),
        ).fetchone()
    if row is None:
        session.pop("consultation_public_id", None)
        return default_consultation()
    try:
        data = json.loads(row["data_json"] or "{}")
    except (ValueError, TypeError):
        session.pop("consultation_public_id", None)
        return default_consultation()
    if not isinstance(data, dict):
        session.pop("consultation_public_id", None)
        return default_consultation()
    return data


def save_consultation(data: dict[str, Any]) -> None:
    """Persist consented consultation state and bind one-time prospect handoffs."""
    email = (data.get("contact_email") or "").strip().lower()
    if not email or not data.get("contact_consent"):
        # Do not persist pre-consent information or put it in a browser cookie.
        session.pop("consultation_public_id", None)
        session.pop("consultation", None)
        session.modified = True
        return

    public_id = data.get("submission_public_id")
    if not isinstance(public_id, str) or not public_id:
        public_id = uuid.uuid4().hex
        data["submission_public_id"] = public_id
    if not data.get("consent_at"):
        data["consent_at"] = datetime.now(timezone.utc).isoformat()

    lead_id = data.get("lead_id")
    token_hash = (data.get("handoff_token_hash") or "").strip()
    now = datetime.now(timezone.utc).isoformat()

    with connect() as conn:
        if token_hash:
            token_row = conn.execute(
                """SELECT lead_id, used_at, consultation_public_id, expires_at
                   FROM consultation_handoff_tokens WHERE token_hash = ? LIMIT 1""",
                (token_hash,),
            ).fetchone()
            if token_row is None:
                raise ValueError("consultation_handoff_invalid")
            if token_row["used_at"] is None:
                if str(token_row["expires_at"]) <= now:
                    raise ValueError("consultation_handoff_expired")
                cursor = conn.execute(
                    """UPDATE consultation_handoff_tokens
                       SET used_at = ?, consultation_public_id = ?
                       WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?""",
                    (now, public_id, token_hash, now),
                )
                if cursor.rowcount != 1:
                    raise ValueError("consultation_handoff_already_used")
            elif token_row["consultation_public_id"] != public_id:
                raise ValueError("consultation_handoff_already_used")
            token_lead_id = int(token_row["lead_id"])
            if lead_id not in (None, "", token_lead_id):
                raise ValueError("consultation_handoff_mismatch")
            lead_id = token_lead_id
            data["lead_id"] = token_lead_id

        conn.execute(
            """INSERT INTO consultation_submissions
               (public_id, lead_id, business_name, email, website, stage, consent_at, data_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(public_id) DO UPDATE SET
                   lead_id=excluded.lead_id,
                   business_name=excluded.business_name,
                   email=excluded.email,
                   website=excluded.website,
                   stage=excluded.stage,
                   data_json=excluded.data_json,
                   updated_at=CURRENT_TIMESTAMP""",
            (
                public_id,
                int(lead_id) if lead_id not in (None, "") else None,
                (data.get("business_name") or "").strip() or "Consultation",
                email,
                (data.get("website") or "").strip() or None,
                (data.get("stage") or ConsultationStage.FIRST_CONTACT.value),
                data["consent_at"],
                json.dumps(data, ensure_ascii=False, sort_keys=True),
            ),
        )

        if lead_id not in (None, ""):
            lead_id = int(lead_id)
            lead = conn.execute("SELECT state FROM leads WHERE id = ?", (lead_id,)).fetchone()
            if lead is not None and str(lead["state"]) in {"CONTACTED", "DELIVERED", "REPLIED"}:
                old_state = str(lead["state"])
                conn.execute(
                    "UPDATE leads SET state = 'INTERESTED', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (lead_id,),
                )
                conn.execute(
                    """INSERT INTO state_events(lead_id,old_state,new_state,reason)
                       VALUES (?,?,'INTERESTED','prospect_started_consultation')""",
                    (lead_id, old_state),
                )
            cancel_follow_ups(conn, lead_id, reason="prospect_started_consultation", commit=False)
        conn.commit()

    session.pop("consultation", None)
    session["consultation_public_id"] = public_id
    session.modified = True
