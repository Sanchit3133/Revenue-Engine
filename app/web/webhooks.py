"""HTTP routes for authenticated provider webhooks."""

from __future__ import annotations

import json
from flask import Blueprint, current_app, jsonify, request

from app.config import settings
from app.database import connect, init_db
from app.webhook_processing import (
    process_brevo_event,
    process_razorpay_event,
    verify_brevo_token,
    verify_razorpay_signature,
)

webhooks_bp = Blueprint("webhooks", __name__)


def _brevo_received_token() -> str | None:
    custom = request.headers.get("X-Revenue-Webhook-Token")
    if custom:
        return custom
    authorization = request.headers.get("Authorization", "").strip()
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() in {"bearer", "token"}:
        return parts[1]
    return None


@webhooks_bp.get("/healthz")
def healthz():
    """Readiness check confirms the configured database can be opened."""
    try:
        init_db()
        with connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            return jsonify({"status": "error", "service": "revenue-engine", "check": "database_integrity"}), 503
    except Exception:
        current_app.logger.exception("Revenue Engine health check failed")
        return jsonify({"status": "error", "service": "revenue-engine", "check": "database_unavailable"}), 503
    return jsonify({"status": "ok", "service": "revenue-engine", "database": "ok"}), 200


@webhooks_bp.post("/webhooks/brevo")
def brevo_webhook():
    if not settings.brevo_webhook_token:
        return jsonify({"error": "brevo_webhook_auth_not_configured"}), 503
    if not verify_brevo_token(_brevo_received_token(), settings.brevo_webhook_token):
        return jsonify({"error": "unauthorized"}), 401
    if not request.is_json:
        return jsonify({"error": "json_required"}), 415
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "invalid_json"}), 400
    events = payload if isinstance(payload, list) else [payload]
    if any(not isinstance(event, dict) for event in events):
        return jsonify({"error": "each_event_must_be_object"}), 400

    init_db()
    results = []
    with connect() as conn:
        for event in events:
            results.append(process_brevo_event(conn, event))
    if any(not item.get("accepted") for item in results):
        return jsonify({"results": results}), 400
    return jsonify({"accepted": True, "count": len(results), "results": results}), 200


@webhooks_bp.post("/webhooks/razorpay")
def razorpay_webhook():
    if not settings.razorpay_webhook_secret:
        return jsonify({"error": "razorpay_webhook_secret_not_configured"}), 503
    raw_body = request.get_data(cache=True, as_text=False)
    signature = request.headers.get("X-Razorpay-Signature")
    if not verify_razorpay_signature(raw_body, signature, settings.razorpay_webhook_secret):
        return jsonify({"error": "invalid_signature"}), 401
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify({"error": "invalid_json"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "payload_must_be_object"}), 400

    init_db()
    with connect() as conn:
        result = process_razorpay_event(conn, payload)
    if result.get("reconciliation_required"):
        return jsonify(result), 202
    if not result.get("accepted"):
        return jsonify(result), 400
    return jsonify(result), 200
