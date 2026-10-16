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


def default_consultation() -> dict[str, Any]:
    return {
        "stage": ConsultationStage.FIRST_CONTACT.value,
        "business_name": "",
        "contact_email": "",
        "contact_consent": False,
        "submission_public_id": None,
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
    """Persist consented consultation state and retain only its ID in cookie."""
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

    with connect() as conn:
        conn.execute(
            """INSERT INTO consultation_submissions
               (public_id, business_name, email, website, stage, consent_at, data_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(public_id) DO UPDATE SET
                   business_name=excluded.business_name,
                   email=excluded.email,
                   website=excluded.website,
                   stage=excluded.stage,
                   data_json=excluded.data_json,
                   updated_at=CURRENT_TIMESTAMP""",
            (
                public_id,
                (data.get("business_name") or "").strip() or "Consultation",
                email,
                (data.get("website") or "").strip() or None,
                (data.get("stage") or ConsultationStage.FIRST_CONTACT.value),
                data["consent_at"],
                json.dumps(data, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()

    session.pop("consultation", None)
    session["consultation_public_id"] = public_id
    session.modified = True
