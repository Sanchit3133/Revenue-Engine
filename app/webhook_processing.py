"""Idempotent provider webhook processing; no outbound requests are made here."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from app.models import LeadState
from app.pipeline import TERMINAL_STATES, transition


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_brevo_token(received_token: str | None, expected_token: str | None) -> bool:
    if not received_token or not expected_token:
        return False
    return hmac.compare_digest(received_token.strip(), expected_token.strip())


def _event_key(provider: str, payload: dict[str, Any]) -> str:
    # Hashing the whole payload avoids relying on Brevo's `id` field alone,
    # which can identify a webhook endpoint rather than an individual event.
    return f"{provider}:{_canonical_hash(payload)}"


def _suppress(conn, email: str, reason: str, source: str) -> None:
    normalized = email.strip().lower()
    if not normalized:
        return
    conn.execute(
        """INSERT INTO suppressions(email, reason, source) VALUES (?, ?, ?)
           ON CONFLICT(email) DO UPDATE SET reason = excluded.reason, source = excluded.source""",
        (normalized, reason, source),
    )


def _move_if_nonterminal(conn, lead_id: int, new_state: LeadState, reason: str) -> None:
    row = conn.execute("SELECT state FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        return
    try:
        old_state = LeadState(row["state"])
    except ValueError:
        return
    if old_state in TERMINAL_STATES:
        return
    transition(conn, lead_id, new_state, reason=reason)


def process_brevo_event(conn, payload: dict[str, Any]) -> dict[str, Any]:
    """Store one Brevo transactional event and apply safe state/suppression changes.

    Recognized event types: delivered, hard_bounce, invalid_email, spam,
    unsubscribed. Other events are stored but do not change lead status.
    """
    if not isinstance(payload, dict):
        return {"accepted": False, "reason": "payload_must_be_object"}

    event_type = str(payload.get("event", "")).strip().lower().replace(" ", "_")
    if not event_type:
        return {"accepted": False, "reason": "event_type_missing"}

    email = str(payload.get("email", "")).strip().lower()
    message_id = str(payload.get("message-id", payload.get("messageId", ""))).strip() or None
    provider_event_id = str(payload.get("event_id", "")).strip() or None
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    event_timestamp = payload.get("ts_event") or payload.get("ts_epoch") or payload.get("date")
    timestamp_text = str(event_timestamp) if event_timestamp is not None else None
    reason = str(payload.get("reason", "")).strip() or None
    key = _event_key("brevo", payload)
    resource_id = message_id or email or provider_event_id

    cursor = conn.execute(
        """INSERT OR IGNORE INTO webhook_events
           (provider, idempotency_key, event_type, resource_id, payload_json)
           VALUES ('brevo', ?, ?, ?, ?)""",
        (key, event_type, resource_id, raw),
    )
    if cursor.rowcount == 0:
        conn.commit()
        return {"accepted": True, "duplicate": True, "reason": "already_processed"}

    conn.execute(
        """INSERT OR IGNORE INTO email_events
           (email, event_type, event_timestamp, reason, raw_event, provider,
            provider_event_id, provider_message_id, idempotency_key)
           VALUES (?, ?, ?, ?, ?, 'brevo', ?, ?, ?)""",
        (email or None, event_type, timestamp_text, reason, raw, provider_event_id, message_id, key),
    )

    outreach = None
    if message_id:
        outreach = conn.execute(
            """SELECT id, lead_id, email FROM outreach
               WHERE provider = 'brevo' AND provider_message_id = ?
               ORDER BY id DESC LIMIT 1""",
            (message_id,),
        ).fetchone()
    lead_id = int(outreach["lead_id"]) if outreach is not None else None

    if event_type == "delivered" and lead_id is not None:
        state_row = conn.execute("SELECT state FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if state_row and state_row["state"] == LeadState.CONTACTED.value:
            transition(conn, lead_id, LeadState.DELIVERED, reason="brevo_delivered_event")
    elif event_type in {"hard_bounce", "hard_bounced", "invalid_email"}:
        _suppress(conn, email, event_type, "brevo_webhook")
        if lead_id is not None:
            _move_if_nonterminal(conn, lead_id, LeadState.BOUNCED, f"brevo_{event_type}")
    elif event_type in {"spam", "complaint"}:
        _suppress(conn, email, "complaint", "brevo_webhook")
        if lead_id is not None:
            _move_if_nonterminal(conn, lead_id, LeadState.COMPLAINT, "brevo_complaint")
    elif event_type in {"unsubscribed", "unsubscribe"}:
        _suppress(conn, email, "unsubscribed", "brevo_webhook")
        if lead_id is not None:
            _move_if_nonterminal(conn, lead_id, LeadState.UNSUBSCRIBED, "brevo_unsubscribed")

    conn.execute(
        "UPDATE webhook_events SET processed_at = ? WHERE provider = 'brevo' AND idempotency_key = ?",
        (datetime.now(timezone.utc).isoformat(), key),
    )
    conn.commit()
    return {"accepted": True, "duplicate": False, "reason": "processed", "lead_id": lead_id}


def verify_razorpay_signature(raw_body: bytes, signature: str | None, secret: str | None) -> bool:
    """Verify Razorpay's HMAC-SHA256 over the original, unparsed HTTP body."""
    if not raw_body or not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def _lead_id_from_payment(conn, payment: dict[str, Any], order_id: str | None) -> int | None:
    notes = payment.get("notes") or {}
    raw_lead_id = notes.get("revenue_engine_lead_id", notes.get("lead_id")) if isinstance(notes, dict) else None
    if raw_lead_id not in (None, ""):
        try:
            lead_id = int(raw_lead_id)
        except (TypeError, ValueError):
            return None
        exists = conn.execute("SELECT 1 FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return lead_id if exists else None
    if order_id:
        row = conn.execute(
            "SELECT lead_id FROM payments WHERE lower(provider) = 'razorpay' AND provider_order_id = ? AND lead_id IS NOT NULL ORDER BY id DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        if row is not None:
            return int(row["lead_id"])
    return None


def process_razorpay_event(conn, payload: dict[str, Any], *, lead_id_override: int | None = None) -> dict[str, Any]:
    """Persist and process a Razorpay payment.captured event idempotently.

    Amounts are stored in the provider's smallest currency unit (paise for INR).
    If the event cannot be linked to a lead, the signed payload is retained for
    explicit reconciliation instead of marking any lead paid speculatively.
    """
    if not isinstance(payload, dict):
        return {"accepted": False, "reason": "payload_must_be_object"}
    event_type = str(payload.get("event", "")).strip()
    nested = payload.get("payload") or {}
    payment_box = nested.get("payment") or {} if isinstance(nested, dict) else {}
    payment = payment_box.get("entity") or {} if isinstance(payment_box, dict) else {}
    if not isinstance(payment, dict):
        return {"accepted": False, "reason": "payment_entity_missing"}

    payment_id = str(payment.get("id", "")).strip() or None
    order_id = str(payment.get("order_id", "")).strip() or None
    event_id = str(payload.get("id", "")).strip()
    key = f"razorpay:{event_id}" if event_id else f"razorpay:{event_type}:{payment_id or _canonical_hash(payload)}"
    resource_id = payment_id or order_id
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)

    if event_type != "payment.captured":
        conn.execute(
            """INSERT OR IGNORE INTO webhook_events
               (provider, idempotency_key, event_type, resource_id, payload_json, processed_at)
               VALUES ('razorpay', ?, ?, ?, ?, ?)""",
            (key, event_type or "unknown", resource_id, raw, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"accepted": True, "ignored": True, "reason": "event_not_payment_captured"}

    if not payment_id or str(payment.get("status", "")).lower() != "captured" or payment.get("captured") is False:
        return {"accepted": False, "reason": "payment_not_confirmed_captured"}

    cursor = conn.execute(
        """INSERT OR IGNORE INTO webhook_events
           (provider, idempotency_key, event_type, resource_id, payload_json)
           VALUES ('razorpay', ?, ?, ?, ?)""",
        (key, event_type, resource_id, raw),
    )
    existing_event = conn.execute(
        "SELECT id, processed_at FROM webhook_events WHERE provider = 'razorpay' AND idempotency_key = ?",
        (key,),
    ).fetchone()
    if existing_event is None:
        conn.rollback()
        return {"accepted": False, "reason": "webhook_event_persistence_failed"}
    if cursor.rowcount == 0 and existing_event["processed_at"]:
        conn.commit()
        return {"accepted": True, "duplicate": True, "reason": "already_processed"}

    lead_id = lead_id_override or _lead_id_from_payment(conn, payment, order_id)
    if lead_id is not None:
        lead_exists = conn.execute("SELECT 1 FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if lead_exists is None:
            conn.commit()
            return {"accepted": False, "reason": "lead_id_not_found"}
    amount = payment.get("amount")
    currency = str(payment.get("currency", "INR")).upper()
    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        amount_int = 0
    if amount_int <= 0:
        conn.commit()
        return {"accepted": False, "reason": "payment_amount_invalid"}
    if lead_id is None:
        conn.execute(
            "UPDATE webhook_events SET payload_json = ? WHERE provider = 'razorpay' AND idempotency_key = ?",
            (raw, key),
        )
        conn.commit()
        return {"accepted": True, "reconciliation_required": True, "reason": "payment_not_linked_to_lead"}

    prior = conn.execute(
        "SELECT id, lead_id, provider_payment_id, amount, currency, provider_order_id, status FROM payments WHERE provider_payment_id = ?",
        (payment_id,),
    ).fetchone()
    if prior is None and order_id:
        # A previously registered order (from the checkout service) can be
        # completed in-place, preserving its lead/order mapping and avoiding
        # an orphan pending row beside the captured payment.
        prior = conn.execute(
            """SELECT id, lead_id, provider_payment_id, amount, currency, provider_order_id, status
               FROM payments WHERE lower(provider) = 'razorpay'
               AND provider_order_id = ? AND provider_payment_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (order_id,),
        ).fetchone()
    if prior is not None:
        if prior["lead_id"] not in (None, lead_id):
            conn.commit()
            return {"accepted": False, "reason": "payment_lead_mapping_conflict"}
        if prior["amount"] not in (None, amount_int) or (prior["currency"] and prior["currency"].upper() != currency):
            conn.commit()
            return {"accepted": False, "reason": "payment_amount_or_currency_conflict"}
        if prior["provider_payment_id"] is None:
            conn.execute(
                """UPDATE payments SET lead_id = COALESCE(lead_id, ?), provider = 'razorpay',
                   provider_payment_id = ?, provider_order_id = COALESCE(provider_order_id, ?),
                   amount = ?, currency = ?, status = 'captured' WHERE id = ?""",
                (lead_id, payment_id, order_id, amount_int, currency, prior["id"]),
            )
        else:
            conn.execute(
                """UPDATE payments SET lead_id = COALESCE(lead_id, ?), provider = 'razorpay',
                   provider_order_id = COALESCE(provider_order_id, ?), amount = ?, currency = ?, status = 'captured'
                   WHERE provider_payment_id = ?""",
                (lead_id, order_id, amount_int, currency, payment_id),
            )
    else:
        conn.execute(
            """INSERT INTO payments
               (lead_id, provider, provider_payment_id, provider_order_id, amount, currency, status)
               VALUES (?, 'razorpay', ?, ?, ?, ?, 'captured')""",
            (lead_id, payment_id, order_id, amount_int, currency),
        )

    lead = conn.execute("SELECT state FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if lead is not None and lead["state"] != LeadState.PAID.value:
        transition(
            conn,
            lead_id,
            LeadState.PAID,
            reason=f"razorpay_payment_captured:{payment_id}",
            allow_terminal_payment_override=True,
        )
    conn.execute(
        "UPDATE webhook_events SET processed_at = ? WHERE provider = 'razorpay' AND idempotency_key = ?",
        (datetime.now(timezone.utc).isoformat(), key),
    )
    conn.commit()
    return {"accepted": True, "duplicate": False, "reason": "payment_recorded", "lead_id": lead_id, "payment_id": payment_id}
