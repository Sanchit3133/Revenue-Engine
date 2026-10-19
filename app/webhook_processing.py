"""Idempotent provider webhook processing; no outbound requests are made here."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from app.models import LeadState
from app.pipeline import TERMINAL_STATES, transition
from app.followup_scheduler import cancel_follow_ups


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


def _cancel_follow_ups_for_email(conn, email: str, reason: str) -> None:
    """Stop all outstanding reminders for a normalized recipient address."""
    clean = (email or "").strip().lower()
    if not clean:
        return
    rows = conn.execute(
        "SELECT id FROM leads WHERE lower(trim(email)) = ?",
        (clean,),
    ).fetchall()
    for row in rows:
        cancel_follow_ups(conn, int(row["id"]), reason=reason, commit=False)


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
        _cancel_follow_ups_for_email(conn, email, f"brevo_{event_type}")
        if lead_id is not None:
            _move_if_nonterminal(conn, lead_id, LeadState.BOUNCED, f"brevo_{event_type}")
    elif event_type in {"spam", "complaint"}:
        _suppress(conn, email, "complaint", "brevo_webhook")
        _cancel_follow_ups_for_email(conn, email, "brevo_complaint")
        if lead_id is not None:
            _move_if_nonterminal(conn, lead_id, LeadState.COMPLAINT, "brevo_complaint")
    elif event_type in {"unsubscribed", "unsubscribe"}:
        _suppress(conn, email, "unsubscribed", "brevo_webhook")
        _cancel_follow_ups_for_email(conn, email, "brevo_unsubscribed")
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
    """Persist a captured payment only after matching a signed provider event to its order."""
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

    if not payment_id or not order_id or str(payment.get("status", "")).lower() != "captured" or payment.get("captured") is False:
        return {"accepted": False, "reason": "payment_not_confirmed_captured"}

    try:
        amount_int = int(payment.get("amount"))
    except (TypeError, ValueError):
        amount_int = 0
    currency = str(payment.get("currency", "")).upper()
    if amount_int <= 0 or not currency:
        return {"accepted": False, "reason": "payment_amount_or_currency_invalid"}

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

    # A website checkout order must already be present in our database. Its
    # expected amount/currency and client-request mapping are authoritative.
    order_row = None
    if order_id:
        order_row = conn.execute(
            """SELECT id,lead_id,provider_payment_id,amount,currency,status,client_request_public_id
               FROM payments WHERE lower(provider)='razorpay' AND provider_order_id=?
               ORDER BY id DESC LIMIT 1""",
            (order_id,),
        ).fetchone()

    if order_row is not None:
        if int(order_row["amount"] or 0) != amount_int:
            conn.commit()
            return {"accepted": False, "reason": "payment_amount_mismatch"}
        if str(order_row["currency"] or "").upper() != currency:
            conn.commit()
            return {"accepted": False, "reason": "payment_currency_mismatch"}
        if order_row["provider_payment_id"] and str(order_row["provider_payment_id"]) != payment_id:
            conn.commit()
            return {"accepted": False, "reason": "order_already_linked_to_another_payment"}

        prior_payment = conn.execute(
            """SELECT id,provider_order_id,client_request_public_id
               FROM payments WHERE provider='razorpay' AND provider_payment_id=? LIMIT 1""",
            (payment_id,),
        ).fetchone()
        if prior_payment is not None and int(prior_payment["id"]) != int(order_row["id"]):
            conn.commit()
            return {"accepted": False, "reason": "payment_id_already_mapped_to_another_order"}

        request_public_id = order_row["client_request_public_id"]
        if request_public_id:
            request_row = conn.execute(
                "SELECT public_id,email,status FROM client_requests WHERE public_id=?",
                (request_public_id,),
            ).fetchone()
            if request_row is None:
                conn.commit()
                return {"accepted": False, "reason": "client_request_mapping_missing"}
            payer_email = str(payment.get("email") or "").strip().lower()
            expected_email = str(request_row["email"] or "").strip().lower()
            if payer_email and expected_email and payer_email != expected_email:
                conn.commit()
                return {"accepted": False, "reason": "payer_email_mismatch"}
            if str(request_row["status"] or "").upper() in {"REJECTED", "DELIVERED", "SENDING", "DELIVERY_UNCERTAIN"}:
                conn.commit()
                return {"accepted": False, "reason": "client_request_not_payable"}

            conn.execute(
                """UPDATE payments SET provider_payment_id=?,status='captured',amount=?,currency=?
                   WHERE id=?""",
                (payment_id, amount_int, currency, order_row["id"]),
            )
            conn.execute(
                """UPDATE client_requests
                   SET status='PAID',payment_reference=?,updated_at=CURRENT_TIMESTAMP
                   WHERE public_id=? AND status NOT IN ('DELIVERED','REJECTED')""",
                (payment_id, request_public_id),
            )
            conn.execute(
                "UPDATE webhook_events SET processed_at=? WHERE provider='razorpay' AND idempotency_key=?",
                (datetime.now(timezone.utc).isoformat(), key),
            )
            conn.commit()
            return {
                "accepted": True,
                "duplicate": False,
                "reason": "client_request_payment_recorded",
                "payment_id": payment_id,
                "client_request_public_id": request_public_id,
            }

        # Preserve the existing lead-bound order flow, but enforce its stored
        # amount and currency before the lead can be moved to PAID.
        mapped_lead_id = int(order_row["lead_id"]) if order_row["lead_id"] is not None else None
        event_lead_id = _lead_id_from_payment(conn, payment, order_id)
        if mapped_lead_id is not None and event_lead_id is not None and mapped_lead_id != event_lead_id:
            conn.commit()
            return {"accepted": False, "reason": "payment_lead_mapping_conflict"}
        lead_id = lead_id_override or mapped_lead_id or event_lead_id
        if lead_id is None:
            conn.commit()
            return {"accepted": True, "reconciliation_required": True, "reason": "payment_not_linked_to_lead"}
        lead_exists = conn.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone()
        if lead_exists is None:
            conn.commit()
            return {"accepted": False, "reason": "lead_id_not_found"}
        conn.execute(
            "UPDATE payments SET provider_payment_id=?,status='captured',amount=?,currency=? WHERE id=?",
            (payment_id, amount_int, currency, order_row["id"]),
        )
        lead = conn.execute("SELECT state FROM leads WHERE id=?", (lead_id,)).fetchone()
        if lead is not None and lead["state"] != LeadState.PAID.value:
            transition(conn, lead_id, LeadState.PAID, reason=f"razorpay_payment_captured:{payment_id}", allow_terminal_payment_override=True)
        conn.execute(
            "UPDATE webhook_events SET processed_at=? WHERE provider='razorpay' AND idempotency_key=?",
            (datetime.now(timezone.utc).isoformat(), key),
        )
        conn.commit()
        return {"accepted": True, "duplicate": False, "reason": "payment_recorded", "lead_id": lead_id, "payment_id": payment_id}

    # Backwards-compatible path for previously registered legacy lead orders or
    # events carrying a known lead note. Newly generated client checkout links
    # never use this fallback because they always have a client-request mapping.
    lead_id = lead_id_override or _lead_id_from_payment(conn, payment, order_id)
    prior = conn.execute(
        """SELECT id,lead_id,provider_payment_id,amount,currency,provider_order_id
           FROM payments WHERE provider='razorpay' AND provider_payment_id=? LIMIT 1""",
        (payment_id,),
    ).fetchone()
    if prior is not None:
        if prior["provider_order_id"] and str(prior["provider_order_id"]) != order_id:
            conn.commit()
            return {"accepted": False, "reason": "payment_id_already_mapped_to_another_order"}
        if prior["amount"] not in (None, amount_int) or (prior["currency"] and str(prior["currency"]).upper() != currency):
            conn.commit()
            return {"accepted": False, "reason": "payment_amount_or_currency_conflict"}
        conn.commit()
        return {"accepted": True, "duplicate": True, "reason": "already_processed"}

    if lead_id is None:
        conn.commit()
        return {"accepted": True, "reconciliation_required": True, "reason": "payment_not_linked_to_known_order_or_lead"}

    lead_exists = conn.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone()
    if lead_exists is None:
        conn.commit()
        return {"accepted": False, "reason": "lead_id_not_found"}
    conn.execute(
        """INSERT INTO payments
           (lead_id,provider,provider_payment_id,amount,currency,status,provider_order_id)
           VALUES (?, 'razorpay', ?, ?, ?, 'captured', ?)""",
        (lead_id, payment_id, amount_int, currency, order_id),
    )
    lead = conn.execute("SELECT state FROM leads WHERE id=?", (lead_id,)).fetchone()
    if lead is not None and lead["state"] != LeadState.PAID.value:
        transition(conn, lead_id, LeadState.PAID, reason=f"razorpay_payment_captured:{payment_id}", allow_terminal_payment_override=True)
    conn.execute(
        "UPDATE webhook_events SET processed_at=? WHERE provider='razorpay' AND idempotency_key=?",
        (datetime.now(timezone.utc).isoformat(), key),
    )
    conn.commit()
    return {"accepted": True, "duplicate": False, "reason": "payment_recorded", "lead_id": lead_id, "payment_id": payment_id}
