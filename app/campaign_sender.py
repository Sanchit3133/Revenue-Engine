"""Provider-neutral, auditable delivery for PREPARED outreach rows."""

from __future__ import annotations

from dataclasses import dataclass
import random
import time
from typing import Any, Callable, Protocol

from app.config import settings
from app.outreach import (
    PREPARED_STATUS,
    Message,
    campaign_capacity,
    mark_failed,
    mark_sent,
)
from app.sendgrid_client import send_message as send_sendgrid_message


class ProviderResult(Protocol):
    success: bool
    status_code: int | None
    message_id: str | None
    reason: str


SenderFunction = Callable[..., ProviderResult]


@dataclass(frozen=True)
class DeliveryResult:
    outreach_id: int
    success: bool
    dry_run: bool
    reason: str
    message_id: str | None = None
    provider: str | None = None


def _provider_details(provider: str | None) -> tuple[str, SenderFunction, str | None, str | None, str | None]:
    selected = (provider or settings.email_provider).strip().lower()
    if selected == "brevo":
        from app.brevo_client import send_message as send_brevo_message

        return (
            selected,
            send_brevo_message,
            settings.brevo_api_key,
            settings.brevo_sender,
            settings.brevo_reply_to,
        )
    if selected == "sendgrid":
        return (
            selected,
            send_sendgrid_message,
            settings.sendgrid_api_key,
            settings.sendgrid_sender,
            settings.sendgrid_reply_to,
        )
    raise ValueError(f"Unsupported email provider: {selected}")


def _prepared_row(conn: Any, outreach_id: int) -> Any | None:
    return conn.execute(
        """
        SELECT id, lead_id, email, subject, body, attempt_number, status,
               provider, retry_of_outreach_id
        FROM outreach
        WHERE id = ?
        """,
        (outreach_id,),
    ).fetchone()


def send_prepared(
    conn: Any,
    outreach_id: int,
    *,
    dry_run: bool = True,
    api_key: str | None = None,
    sender: str | None = None,
    reply_to: str | None = None,
    provider: str | None = None,
    sender_function: SenderFunction | None = None,
) -> DeliveryResult:
    """Send one PREPARED record through the selected delivery provider.

    A provider-accepted email is never marked FAILED merely because a database
    update fails; that ambiguous case is returned separately to avoid duplicate
    sends. Dry-run makes no provider calls and changes no database state.
    """
    row = _prepared_row(conn, outreach_id)
    selected_provider = (provider or settings.email_provider).strip().lower()

    if row is None:
        return DeliveryResult(outreach_id, False, dry_run, "outreach_not_found", provider=selected_provider)
    if row["status"] != PREPARED_STATUS:
        return DeliveryResult(outreach_id, False, dry_run, "outreach_not_prepared", provider=selected_provider)

    message = Message(email=row["email"], subject=row["subject"], body=row["body"])
    if dry_run:
        return DeliveryResult(
            outreach_id,
            False,
            True,
            "dry_run_no_external_request",
            provider=selected_provider,
        )

    try:
        resolved_provider, resolved_sender_function, default_key, default_sender, default_reply = _provider_details(selected_provider)
    except ValueError as exc:
        return DeliveryResult(outreach_id, False, False, str(exc), provider=selected_provider)

    function = sender_function or resolved_sender_function
    # Explicit parameters are convenient for tests and one-off controlled calls.
    # Otherwise use only the credentials/settings for the selected provider.
    effective_key = api_key if api_key is not None else default_key
    effective_sender = sender if sender is not None else default_sender
    effective_reply = reply_to if reply_to is not None else default_reply

    result = function(
        message,
        api_key=effective_key,
        sender=effective_sender,
        reply_to=effective_reply,
    )

    if result.success:
        state_updated = mark_sent(
            conn,
            outreach_id,
            provider=resolved_provider,
            message_id=result.message_id,
        )
        if not state_updated:
            return DeliveryResult(
                outreach_id,
                False,
                False,
                "accepted_but_state_update_failed",
                message_id=result.message_id,
                provider=resolved_provider,
            )
        return DeliveryResult(
            outreach_id,
            True,
            False,
            result.reason,
            message_id=result.message_id,
            provider=resolved_provider,
        )

    state_updated = mark_failed(
        conn,
        outreach_id,
        provider=resolved_provider,
        reason=result.reason,
        message_id=result.message_id,
    )
    return DeliveryResult(
        outreach_id,
        False,
        False,
        result.reason if state_updated else "send_failed_but_state_update_failed",
        message_id=result.message_id,
        provider=resolved_provider,
    )


def send_prepared_batch(
    conn: Any,
    *,
    dry_run: bool = True,
    limit: int | None = None,
    api_key: str | None = None,
    sender: str | None = None,
    reply_to: str | None = None,
    provider: str | None = None,
    sender_function: SenderFunction | None = None,
    respect_rate_limits: bool = False,
    sleep_function: Callable[[float], Any] = time.sleep,
    random_integer: Callable[[int, int], int] = random.randint,
) -> list[DeliveryResult]:
    """Process a bounded batch, optionally enforcing configured delay/backoff.

    The live CLI enables ``respect_rate_limits``; it is injectable/disabled by
    default for fast deterministic unit tests.
    """
    remaining = campaign_capacity(conn, settings.daily_limit)
    if limit is not None:
        if limit < 1:
            return []
        remaining = min(remaining, limit)
    if remaining <= 0:
        return []

    rows = conn.execute(
        "SELECT id FROM outreach WHERE status = ? ORDER BY id ASC LIMIT ?",
        (PREPARED_STATUS, remaining),
    ).fetchall()

    results: list[DeliveryResult] = []
    consecutive_failures = 0
    for index, row in enumerate(rows):
        result = send_prepared(
            conn,
            int(row["id"]),
            dry_run=dry_run,
            api_key=api_key,
            sender=sender,
            reply_to=reply_to,
            provider=provider,
            sender_function=sender_function,
        )
        results.append(result)

        if not dry_run:
            if result.success:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if settings.max_consecutive_failures > 0 and consecutive_failures >= settings.max_consecutive_failures:
                    break

        if respect_rate_limits and not dry_run and index < len(rows) - 1:
            delay = random_integer(settings.min_send_delay, settings.max_send_delay)
            sleep_function(float(delay))

    return results
