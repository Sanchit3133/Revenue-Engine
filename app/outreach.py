"""Outreach preparation and safety controls for the Revenue Engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Any

from app.lead_engine import is_valid_public_email, normalize_email
from app.config import settings
from app.consultation_handoff import new_handoff_material
from app.models import LeadState
from app.pipeline import transition
from app.followup_scheduler import schedule_next_follow_up


MAX_OUTREACH_ATTEMPTS = 3  # first contact + 2 follow-ups

PREPARED_STATUS = "PREPARED"
SENT_STATUS = "SENT"
FAILED_STATUS = "FAILED"


@dataclass(frozen=True)
class Message:
    email: str
    subject: str
    body: str


@dataclass(frozen=True)
class OutreachResult:
    allowed: bool
    reason: str
    outreach_id: int | None = None
    message: Message | None = None
    attempt_number: int | None = None


def validate_message(message: Message | None) -> tuple[bool, str]:
    """Validate an outbound plain-text message."""
    if message is None:
        return False, "message_missing"

    if "[BUY_LINK]" in message.body.upper():
        return False, "unresolved_buy_link"

    email = normalize_email(message.email)

    if not email or not is_valid_public_email(email):
        return False, "invalid_or_blocked_email"

    _, parsed_email = parseaddr(email)

    if not parsed_email or parsed_email.lower() != email.lower():
        return False, "invalid_email"

    if not message.subject.strip():
        return False, "subject_missing"

    if not message.body.strip():
        return False, "body_missing"

    if "<" in message.subject or ">" in message.subject:
        return False, "html_not_allowed"

    if "<" in message.body or ">" in message.body:
        return False, "html_not_allowed"

    lowered = f"{message.subject}\n{message.body}".lower()

    forbidden_patterns = (
        "buy now",
        "purchase now",
        "order now",
        "checkout",
        "payment link",
        "pay now",
        "click here to buy",
        "click here to purchase",
        "/buy",
        "/purchase",
        "/checkout",
        "/payment",
        "buy=",
        "purchase=",
        "checkout=",
        "payment=",
    )

    if any(pattern in lowered for pattern in forbidden_patterns):
        return False, "sales_call_to_action_not_allowed"

    return True, ""


def campaign_capacity(
    conn: Any | None = None,
    daily_limit: int = 25,
) -> int:
    """Return remaining campaign capacity."""
    if daily_limit <= 0:
        return 0

    if conn is None:
        return daily_limit

    today_prefix = datetime.now(timezone.utc).date().isoformat()

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM outreach
        WHERE created_at LIKE ?
          AND status IN (?, ?)
        """,
        (
            f"{today_prefix}%",
            PREPARED_STATUS,
            SENT_STATUS,
        ),
    ).fetchone()

    used = int(row[0] or 0)

    return max(0, daily_limit - used)


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower().strip()


def _is_suppressed(conn: Any, email: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM suppressions
        WHERE lower(email) = lower(?)
        LIMIT 1
        """,
        (email,),
    ).fetchone()

    return row is not None


def _existing_attempts(conn: Any, lead_id: int) -> int:
    # Provider retry rows may share the same attempt_number. Count distinct
    # sales touches so a transport retry does not consume a follow-up slot.
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT attempt_number)
        FROM outreach
        WHERE lead_id = ?
        """,
        (lead_id,),
    ).fetchone()

    return int(row[0] or 0)


def _next_attempt_number(conn: Any, lead_id: int) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(attempt_number), 0)
        FROM outreach
        WHERE lead_id = ?
        """,
        (lead_id,),
    ).fetchone()

    return int(row[0] or 0) + 1


def _has_recent_domain_contact(
    conn: Any,
    email: str,
    domain_cooldown: int,
    *,
    lead_id: int | None = None,
) -> bool:
    """Prevent rapid contact with different leads at the same domain."""
    if domain_cooldown <= 0:
        return False

    domain = _email_domain(email)

    rows = conn.execute(
        """
        SELECT email, lead_id, sent_at, created_at
        FROM outreach
        WHERE status = ?
        ORDER BY id DESC
        LIMIT 100
        """,
        (SENT_STATUS,),
    ).fetchall()

    now = datetime.now(timezone.utc)

    for row in rows:
        if lead_id is not None and int(row["lead_id"]) == lead_id:
            continue

        other_email = normalize_email(row["email"] or "")

        if _email_domain(other_email) != domain:
            continue

        raw_timestamp = row["sent_at"] or row["created_at"]

        if not raw_timestamp:
            continue

        try:
            timestamp = datetime.fromisoformat(str(raw_timestamp))
        except ValueError:
            continue

        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        elapsed = (now - timestamp).total_seconds()

        if elapsed < domain_cooldown:
            return True

    return False


def _lead_row(conn: Any, lead_id: int) -> Any | None:
    return conn.execute(
        """
        SELECT *
        FROM leads
        WHERE id = ?
        """,
        (lead_id,),
    ).fetchone()


def _build_first_contact_message(lead: Any, consultation_url: str | None = None) -> Message:
    email = normalize_email(lead["email"])
    business_name = (lead["business_name"] or "your institute").strip()

    return Message(
        email=email,
        subject="Quick question about your enquiries",
        body=(
            f"Hi,\n\n"
            f"I was looking at {business_name} and had a quick question "
            f"about your course/admission enquiries.\n\n"
            f"When someone makes an enquiry but does not respond after "
            f"the first conversation, how do you currently track and "
            f"follow up with them?\n\n"
            f"If you already have it handled, no problem.\n\n"
            + (f"If it is easier, you can also share your current process here:\\n{consultation_url}\\n\\n" if consultation_url else "")
            + f"Best,\\n"
            f"Sanchit\n\n"
            f"If you would rather not receive messages from me, just "
            f"reply STOP."
        ),
    )


def _build_follow_up_message(
    lead: Any,
    attempt_number: int,
) -> Message | None:
    business_name = (lead["business_name"] or "your institute").strip()
    email = normalize_email(lead["email"])

    if attempt_number == 2:
        return Message(
            email=email,
            subject="Following up on my question",
            body=(
                f"Hi,\n\n"
                f"Just following up on my earlier question about how "
                f"{business_name} handles enquiries that do not respond "
                f"after the first conversation.\n\n"
                f"If this is already handled, no problem.\n\n"
                f"Best,\n"
                f"Sanchit\n\n"
                f"Reply STOP if you would rather not hear from me."
            ),
        )

    if attempt_number == 3:
        return Message(
            email=email,
            subject="One last follow-up",
            body=(
                f"Hi,\n\n"
                f"One last follow-up on my earlier question about your "
                f"enquiry follow-up process.\n\n"
                f"If it is not relevant, no problem at all.\n\n"
                f"Best,\n"
                f"Sanchit\n\n"
                f"Reply STOP if you would rather not hear from me."
            ),
        )

    return None


def _eligibility(
    conn: Any,
    lead: Any,
    daily_limit: int,
    domain_cooldown: int,
    *,
    allow_contacted: bool = False,
) -> tuple[bool, str]:
    if lead is None:
        return False, "lead_not_found"

    allowed_states = {LeadState.QUALIFIED.value}

    if allow_contacted:
        # A provider delivery webhook may advance CONTACTED to DELIVERED;
        # that does not make a no-response reminder ineligible.
        allowed_states.update({LeadState.CONTACTED.value, LeadState.DELIVERED.value})

    if lead["state"] not in allowed_states:
        return False, "lead_not_qualified"

    email = normalize_email(lead["email"] or "")

    if not email or not is_valid_public_email(email):
        return False, "invalid_or_blocked_email"

    if _is_suppressed(conn, email):
        return False, "email_suppressed"

    if campaign_capacity(conn, daily_limit) <= 0:
        return False, "campaign_capacity_reached"

    if _has_recent_domain_contact(
        conn,
        email,
        domain_cooldown,
        lead_id=int(lead["id"]),
    ):
        return False, "domain_cooldown_active"

    return True, "eligible"


def prepare_first_contact(
    conn: Any,
    lead_id: int,
    *,
    daily_limit: int = 25,
    domain_cooldown: int = 60,
) -> OutreachResult:
    """Prepare first-contact outreach without sending it."""
    lead = _lead_row(conn, lead_id)

    if lead is None:
        return OutreachResult(
            allowed=False,
            reason="lead_not_found",
        )

    if _existing_attempts(conn, lead_id) > 0:
        return OutreachResult(
            allowed=False,
            reason="outreach_already_exists",
        )

    allowed, reason = _eligibility(
        conn,
        lead,
        daily_limit,
        domain_cooldown,
    )

    if not allowed:
        return OutreachResult(
            allowed=False,
            reason=reason,
        )

    handoff = new_handoff_material(getattr(settings, "public_app_url", None))
    message = _build_first_contact_message(
        lead,
        consultation_url=handoff["url"] if handoff else None,
    )

    valid, validation_reason = validate_message(message)

    if not valid:
        return OutreachResult(
            allowed=False,
            reason=validation_reason,
            message=message,
        )

    if handoff is not None:
        # Only a one-way hash is stored. The raw token exists only in the
        # prepared message and expires automatically.
        conn.execute(
            """INSERT INTO consultation_handoff_tokens(token_hash, lead_id, expires_at)
               VALUES (?, ?, ?)""",
            (handoff["token_hash"], int(lead_id), handoff["expires_at"]),
        )

    cursor = conn.execute(
        """
        INSERT INTO outreach (
            lead_id,
            email,
            subject,
            body,
            attempt_number,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            lead_id,
            message.email,
            message.subject,
            message.body,
            1,
            PREPARED_STATUS,
        ),
    )

    outreach_id = cursor.fetchone()[0]
    conn.commit()

    return OutreachResult(
        allowed=True,
        reason="prepared",
        outreach_id=outreach_id,
        message=message,
        attempt_number=1,
    )


def prepare_follow_up(
    conn: Any,
    lead_id: int,
    *,
    daily_limit: int = 25,
    domain_cooldown: int = 60,
) -> OutreachResult:
    """Prepare follow-up #1 or #2, never a third follow-up."""
    lead = _lead_row(conn, lead_id)

    if lead is None:
        return OutreachResult(
            allowed=False,
            reason="lead_not_found",
        )

    attempts = _existing_attempts(conn, lead_id)

    if attempts == 0:
        return OutreachResult(
            allowed=False,
            reason="first_contact_required",
        )

    successful_touch = conn.execute(
        "SELECT 1 FROM outreach WHERE lead_id = ? AND status = ? LIMIT 1",
        (lead_id, SENT_STATUS),
    ).fetchone()
    if successful_touch is None:
        return OutreachResult(
            allowed=False,
            reason="first_contact_not_sent",
        )
    if lead["state"] not in {LeadState.CONTACTED.value, LeadState.DELIVERED.value}:
        return OutreachResult(
            allowed=False,
            reason="lead_not_contacted",
        )

    if attempts >= MAX_OUTREACH_ATTEMPTS:
        return OutreachResult(
            allowed=False,
            reason="follow_up_limit_reached",
            attempt_number=attempts + 1,
        )

    allowed, reason = _eligibility(
        conn,
        lead,
        daily_limit,
        domain_cooldown,
        allow_contacted=True,
    )

    if not allowed:
        return OutreachResult(
            allowed=False,
            reason=reason,
        )

    attempt_number = _next_attempt_number(conn, lead_id)

    message = _build_follow_up_message(
        lead,
        attempt_number,
    )

    if message is None:
        return OutreachResult(
            allowed=False,
            reason="follow_up_limit_reached",
            attempt_number=attempt_number,
        )

    valid, validation_reason = validate_message(message)

    if not valid:
        return OutreachResult(
            allowed=False,
            reason=validation_reason,
            message=message,
            attempt_number=attempt_number,
        )

    cursor = conn.execute(
        """
        INSERT INTO outreach (
            lead_id,
            email,
            subject,
            body,
            attempt_number,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            lead_id,
            message.email,
            message.subject,
            message.body,
            attempt_number,
            PREPARED_STATUS,
        ),
    )

    outreach_id = cursor.fetchone()[0]
    conn.commit()

    return OutreachResult(
        allowed=True,
        reason="prepared",
        outreach_id=outreach_id,
        message=message,
        attempt_number=attempt_number,
    )


def _record_outreach_event(
    conn: Any,
    outreach_id: int,
    event_type: str,
    *,
    provider: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    import json

    conn.execute(
        """
        INSERT INTO outreach_events (
            outreach_id, event_type, provider, reason, details_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            outreach_id,
            event_type,
            provider,
            reason,
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
        ),
    )


def prepare_failed_retry(
    conn: Any,
    outreach_id: int,
    *,
    confirmed_not_delivered: bool = False,
    daily_limit: int = 25,
) -> OutreachResult:
    """Create a new PREPARED row for a confirmed non-delivery.

    The original FAILED row remains untouched. Retry rows retain the original
    sales ``attempt_number`` so transport retries do not consume a follow-up.
    Callers must explicitly confirm non-delivery because network timeouts can
    be ambiguous and blindly retrying could duplicate a message.
    """
    if not confirmed_not_delivered:
        return OutreachResult(False, "confirmation_required")

    source = conn.execute(
        "SELECT * FROM outreach WHERE id = ?",
        (outreach_id,),
    ).fetchone()
    if source is None:
        return OutreachResult(False, "outreach_not_found")
    if source["status"] != FAILED_STATUS:
        return OutreachResult(False, "outreach_not_failed")

    lead = _lead_row(conn, int(source["lead_id"]))
    if lead is None:
        return OutreachResult(False, "lead_not_found")
    if lead["state"] not in (LeadState.QUALIFIED.value, LeadState.CONTACTED.value):
        return OutreachResult(False, "lead_not_eligible_for_retry")

    email = normalize_email(source["email"] or "")
    if not email or not is_valid_public_email(email):
        return OutreachResult(False, "invalid_or_blocked_email")
    if _is_suppressed(conn, email):
        return OutreachResult(False, "email_suppressed")

    duplicate_prepared = conn.execute(
        """
        SELECT 1 FROM outreach
        WHERE retry_of_outreach_id = ? AND status = ?
        LIMIT 1
        """,
        (outreach_id, PREPARED_STATUS),
    ).fetchone()
    if duplicate_prepared is not None:
        return OutreachResult(False, "retry_already_prepared")

    success_for_touch = conn.execute(
        """
        SELECT 1 FROM outreach
        WHERE lead_id = ? AND attempt_number = ? AND status = ?
        LIMIT 1
        """,
        (int(source["lead_id"]), int(source["attempt_number"]), SENT_STATUS),
    ).fetchone()
    if success_for_touch is not None:
        return OutreachResult(False, "attempt_already_accepted")

    if campaign_capacity(conn, daily_limit) <= 0:
        return OutreachResult(False, "campaign_capacity_reached")

    message = Message(
        email=email,
        subject=source["subject"],
        body=source["body"],
    )
    valid, reason = validate_message(message)
    if not valid:
        return OutreachResult(False, reason, message=message)

    cursor = conn.execute(
        """
        INSERT INTO outreach (
            lead_id, email, subject, body, attempt_number, status,
            retry_of_outreach_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            int(source["lead_id"]),
            email,
            source["subject"],
            source["body"],
            int(source["attempt_number"]),
            PREPARED_STATUS,
            outreach_id,
        ),
    )
    new_id = int(cursor.fetchone()[0])
    _record_outreach_event(
        conn,
        outreach_id,
        "retry_created",
        reason="confirmed_not_delivered",
        details={"retry_outreach_id": new_id},
    )
    _record_outreach_event(
        conn,
        new_id,
        "retry_prepared",
        reason="confirmed_not_delivered",
        details={"retry_of_outreach_id": outreach_id},
    )
    conn.commit()
    return OutreachResult(
        True,
        "retry_prepared",
        outreach_id=new_id,
        message=message,
        attempt_number=int(source["attempt_number"]),
    )


def mark_sent(
    conn: Any,
    outreach_id: int,
    *,
    provider: str | None = None,
    message_id: str | None = None,
) -> bool:
    """Mark prepared outreach accepted by a provider and advance lead state."""
    row = conn.execute(
        "SELECT * FROM outreach WHERE id = ?",
        (outreach_id,),
    ).fetchone()
    if row is None or row["status"] not in (PREPARED_STATUS, "SENDING"):
        return False

    sent_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE outreach
        SET status = ?, sent_at = ?, provider = ?, provider_message_id = ?,
            last_error = NULL
        WHERE id = ?
        """,
        (SENT_STATUS, sent_at, provider, message_id, outreach_id),
    )

    lead = _lead_row(conn, int(row["lead_id"]))
    if lead is not None and lead["state"] == LeadState.QUALIFIED.value:
        transition(
            conn,
            int(row["lead_id"]),
            LeadState.CONTACTED,
            reason="outreach_sent",
        )

    _record_outreach_event(
        conn,
        outreach_id,
        "provider_accepted",
        provider=provider,
        reason="accepted",
        details={"provider_message_id": message_id},
    )
    # Schedule reminders from the same transaction as the provider-accepted state.
    # Attempts 1 and 2 schedule follow-ups; attempt 3 schedules nothing further.
    schedule_next_follow_up(
        conn,
        lead_id=int(row["lead_id"]),
        source_outreach_id=int(row["id"]),
        accepted_attempt_number=int(row["attempt_number"]),
        sent_at=datetime.fromisoformat(sent_at),
        commit=False,
    )
    conn.commit()
    return True


def mark_failed(
    conn: Any,
    outreach_id: int | None,
    *,
    provider: str | None = None,
    reason: str | None = None,
    message_id: str | None = None,
) -> bool:
    """Mark prepared/sent outreach failed while keeping failure details."""
    if outreach_id is None:
        return False

    row = conn.execute(
        "SELECT * FROM outreach WHERE id = ?",
        (outreach_id,),
    ).fetchone()
    if row is None or row["status"] not in (PREPARED_STATUS, "SENDING", SENT_STATUS):
        return False

    conn.execute(
        """
        UPDATE outreach
        SET status = ?, provider = ?, provider_message_id = ?, last_error = ?
        WHERE id = ?
        """,
        (FAILED_STATUS, provider, message_id, reason, outreach_id),
    )
    _record_outreach_event(
        conn,
        outreach_id,
        "provider_rejected",
        provider=provider,
        reason=reason or "unknown_failure",
        details={"provider_message_id": message_id},
    )
    conn.commit()
    return True
