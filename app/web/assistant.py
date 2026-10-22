"""Session-bound Hugging Face consultation assistant. It cannot approve work or set prices."""
from __future__ import annotations

import secrets
import time
from typing import Any

from flask import Blueprint, jsonify, request, session

from app.config import settings
from app.database import connect
from app.web.session_state import get_current_consultation
from app.web.consultation_ai import build_consultation_reply

assistant_bp = Blueprint("assistant", __name__)


@assistant_bp.get("/api/assistant/history")
def consultation_assistant_history():
    data = get_current_consultation()
    if not isinstance(data, dict) or not data.get("contact_consent") or not data.get("submission_public_id"):
        return jsonify({"messages": []}), 200
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM consultation_messages WHERE consultation_public_id=? ORDER BY id DESC LIMIT 12",
            (data["submission_public_id"],),
        ).fetchall()
    return jsonify({"messages": [{"role": row["role"], "content": row["content"], "created_at": row["created_at"]} for row in reversed(rows)]}), 200


@assistant_bp.post("/api/assistant")
def consultation_assistant():
    data = get_current_consultation()
    if not isinstance(data, dict) or not data.get("contact_consent") or not data.get("submission_public_id"):
        return jsonify({"error": "start_consultation_first"}), 409
    payload = request.get_json(silent=True) or {}
    supplied_csrf = str(payload.get("csrf_token") or "")
    expected_csrf = str(session.get("assistant_csrf_token") or "")
    if not expected_csrf or not supplied_csrf or not secrets.compare_digest(expected_csrf, supplied_csrf):
        return jsonify({"error": "invalid_csrf_token"}), 400
    message = payload.get("message")
    if not isinstance(message, str):
        return jsonify({"error": "message_required"}), 400
    message = " ".join(message.replace("\x00", "").split())[:1200]
    if len(message) < 2:
        return jsonify({"error": "message_too_short"}), 400
    now = time.time()
    recent = [float(item) for item in session.get("assistant_request_timestamps", [])
              if isinstance(item, (int, float)) and now - float(item) < 3600]
    if len(recent) >= 20:
        return jsonify({"error": "chat_rate_limit", "message": "You've reached the consultation chat limit for this hour. Please continue with the form fields or try again later."}), 429
    recent.append(now)
    session["assistant_request_timestamps"] = recent[-20:]
    public_id = data["submission_public_id"]
    with connect() as conn:
        history_rows = conn.execute(
            "SELECT role, content FROM consultation_messages WHERE consultation_public_id=? ORDER BY id DESC LIMIT 8",
            (public_id,),
        ).fetchall()
        history = [{"role": row["role"], "content": row["content"]} for row in reversed(history_rows)]
        conn.execute(
            "INSERT INTO consultation_messages(consultation_public_id, role, content) VALUES (?, 'user', ?)",
            (public_id, message),
        )
        conn.commit()
    reply, using_ai = build_consultation_reply(message, data, history)
    with connect() as conn:
        conn.execute(
            "INSERT INTO consultation_messages(consultation_public_id, role, content) VALUES (?, 'assistant', ?)",
            (public_id, reply),
        )
        conn.commit()
    return jsonify({"reply": reply, "using_ai": using_ai}), 200
