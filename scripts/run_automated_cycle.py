"""Run one bounded Revenue Engine discovery/outreach cycle.

Preview mode discovers and classifies but writes nothing and sends nothing.
Live mode requires an explicit automation switch, configured provider credentials,
and a validated target-market record. All contact details stay out of stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import datetime, timezone

from app.campaign_sender import send_prepared
from app.config import settings
from app.database import connect
from app.followup_scheduler import (
    cancel_follow_ups,
    claim_due_follow_ups,
    finish_follow_up,
    mark_follow_up_prepared,
    release_claim,
    release_stale_claims,
)
from app.outreach import prepare_first_contact, prepare_follow_up
from scripts.overture_discovery import DiscoveryError, discover_and_persist

TRANSIENT_PREPARATION_REASONS = {"campaign_capacity_reached", "domain_cooldown_active"}


def _provider_ready() -> bool:
    provider = settings.email_provider
    if provider == "brevo":
        return bool(settings.brevo_api_key and settings.brevo_sender)
    if provider == "sendgrid":
        return bool(settings.sendgrid_api_key and settings.sendgrid_sender)
    return False


def _market_ready(conn, country: str) -> bool:
    if not settings.market_validation_required:
        return True
    row = conn.execute(
        "SELECT status FROM market_validations WHERE lower(country) = lower(?) LIMIT 1",
        (country,),
    ).fetchone()
    return bool(row and row["status"] == "VALIDATED")


def _daily_capacity(conn) -> int:
    """Use a conservative cap counting all outreach created today, including failures."""
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM outreach
           WHERE created_at LIKE ? AND status IN ('PREPARED','SENDING','SENT','FAILED')""",
        (today + "%",),
    ).fetchone()
    used = int(row["n"] or 0) if row else 0
    return max(0, int(settings.daily_limit) - used)


def _pace_before_send(sent_this_run: int) -> None:
    if sent_this_run <= 0:
        return
    delay = random.randint(int(settings.min_send_delay), int(settings.max_send_delay))
    time.sleep(delay)


def _send_one(conn, outreach_id: int, sent_this_run: int) -> tuple[bool, str]:
    _pace_before_send(sent_this_run)
    result = send_prepared(
        conn,
        int(outreach_id),
        dry_run=False,
        provider=settings.email_provider,
    )
    # Never print recipient, body, or provider payload.
    return bool(result.success), str(result.reason)[:160]


def _process_due_reminders(conn, *, max_sends: int, already_sent: int) -> tuple[int, int]:
    """Prepare and deliver overdue reminders before any new first contacts."""
    release_stale_claims(conn, age_minutes=60)
    capacity = min(max_sends - already_sent, _daily_capacity(conn))
    if capacity <= 0:
        return 0, 0
    due = claim_due_follow_ups(conn, limit=min(25, capacity))
    accepted = 0
    failed = 0
    for record in due:
        schedule_id = int(record["id"])
        lead_id = int(record["lead_id"])
        existing_outreach_id = record.get("prepared_outreach_id")

        if existing_outreach_id is not None:
            # Recover the exact previously prepared message instead of creating
            # a new sales attempt. Anything other than PREPARED is never resent.
            prior = conn.execute(
                "SELECT status FROM outreach WHERE id = ?",
                (int(existing_outreach_id),),
            ).fetchone()
            if prior is None:
                finish_follow_up(conn, schedule_id, accepted=False, reason="prepared_outreach_missing")
                failed += 1
                continue
            prior_status = str(prior["status"])
            if prior_status == "SENT":
                finish_follow_up(conn, schedule_id, accepted=True, reason="reconciled_already_sent")
                continue
            if prior_status != "PREPARED":
                reason = (
                    "ambiguous_provider_state_after_crash"
                    if prior_status == "SENDING"
                    else "prepared_outreach_not_sendable"
                )
                finish_follow_up(conn, schedule_id, accepted=False, reason=reason)
                failed += 1
                continue
            outreach_id = int(existing_outreach_id)
        else:
            result = prepare_follow_up(
                conn,
                lead_id,
                daily_limit=int(settings.daily_limit),
                domain_cooldown=int(settings.domain_cooldown),
            )
            if not result.allowed or result.outreach_id is None:
                if result.reason in TRANSIENT_PREPARATION_REASONS:
                    release_claim(conn, schedule_id, reason=result.reason)
                else:
                    cancel_follow_ups(conn, lead_id, reason=result.reason or "follow_up_ineligible")
                continue
            outreach_id = int(result.outreach_id)

        if not mark_follow_up_prepared(conn, schedule_id, outreach_id):
            # The row no longer belongs to this worker. Do not send it.
            continue
        success, reason = _send_one(conn, outreach_id, already_sent + accepted + failed)
        finish_follow_up(conn, schedule_id, accepted=success, reason=reason)
        if success:
            accepted += 1
        else:
            failed += 1
    return accepted, failed


def _prepare_and_send_first_contacts(conn, *, max_sends: int, already_sent: int) -> tuple[int, int, int]:
    remaining = min(max_sends - already_sent, _daily_capacity(conn))
    if remaining <= 0:
        return 0, 0, 0
    rows = conn.execute(
        """SELECT l.id FROM leads l
           WHERE l.state = 'QUALIFIED'
             AND NOT EXISTS (SELECT 1 FROM outreach o WHERE o.lead_id = l.id)
           ORDER BY l.score DESC, l.id ASC
           LIMIT ?""",
        (min(remaining, 25),),
    ).fetchall()
    accepted = failed = prepared = 0
    for row in rows:
        if accepted + failed >= remaining or _daily_capacity(conn) <= 0:
            break
        result = prepare_first_contact(
            conn,
            int(row["id"]),
            daily_limit=int(settings.daily_limit),
            domain_cooldown=int(settings.domain_cooldown),
        )
        if not result.allowed or result.outreach_id is None:
            continue
        prepared += 1
        success, reason = _send_one(conn, result.outreach_id, already_sent + accepted + failed)
        if success:
            accepted += 1
        else:
            failed += 1
    return prepared, accepted, failed


def _record_worker_run(conn, *, status: str, summary: dict) -> None:
    """Store aggregate-only worker status for the authenticated owner dashboard."""
    safe_summary = {
        key: value for key, value in summary.items()
        if key in {
            "mode", "country", "city", "segment", "places_seen", "site_checks",
            "published_emails_found", "qualified", "saved", "reminders_claimed",
            "prepared", "accepted_by_provider", "failed_or_blocked", "live_send_block",
        }
    }
    conn.execute(
        """INSERT INTO module_runs(module, status, summary, log_text)
           VALUES (?, ?, ?, NULL)""",
        ("automated_acquisition", status[:40], json.dumps(safe_summary, sort_keys=True)[:1000]),
    )
    conn.commit()


def run_cycle(*, country: str = "India", max_new: int = 5, live: bool = False) -> dict:
    if not 1 <= max_new <= 5:
        raise ValueError("max_new must be between 1 and 5")
    if live:
        enabled = os.getenv("REVENUE_ENGINE_AUTOMATION_ENABLED", "").strip().lower() == "true"
        if not enabled:
            raise RuntimeError("Live automation is disabled. Enable REVENUE_ENGINE_AUTOMATION_ENABLED only after acceptance checks.")
        if not _provider_ready():
            raise RuntimeError("The configured delivery provider credentials or sender are missing.")
        if settings.database_backend == "sqlite":
            raise RuntimeError("Live automation requires the shared PostgreSQL database.")
        app_url = (getattr(settings, "public_app_url", None) or "").strip()
        if not app_url.lower().startswith("https://"):
            raise RuntimeError("PUBLIC_APP_URL must be a verified HTTPS app URL before live outreach so prospects can enter the consultation.")
    with connect() as conn:
        if not live:
            # Preview runs must be read-only even if the supplied credentials can write.
            if settings.database_backend == "postgresql":
                conn.execute("SET TRANSACTION READ ONLY")
            else:
                conn.execute("PRAGMA query_only = ON")
        discovery = discover_and_persist(conn, country=country, max_new=max_new, persist=live)
        summary = {
            "mode": "LIVE" if live else "PREVIEW",
            "country": discovery["country"],
            "city": discovery["city"],
            "segment": discovery["segment"],
            "places_seen": discovery["places_seen"],
            "site_checks": discovery["site_checks"],
            "published_emails_found": discovery["published_emails_found"],
            "qualified": discovery["qualified"],
            "saved": discovery["saved"],
            "reminders_claimed": 0,
            "prepared": 0,
            "accepted_by_provider": 0,
            "failed_or_blocked": 0,
            "live_send_block": "",
        }
        if not live:
            return summary
        if not _market_ready(conn, discovery["country"]):
            summary["live_send_block"] = "market_validation_not_complete"
            _record_worker_run(conn, status="blocked", summary=summary)
            return summary

        run_limit = min(int(settings.campaign_limit), int(settings.daily_limit))
        reminders_ok, reminders_failed = _process_due_reminders(
            conn, max_sends=run_limit, already_sent=0
        )
        summary["accepted_by_provider"] += reminders_ok
        summary["failed_or_blocked"] += reminders_failed
        prepared, first_ok, first_failed = _prepare_and_send_first_contacts(
            conn,
            max_sends=run_limit,
            already_sent=reminders_ok + reminders_failed,
        )
        summary["prepared"] = prepared
        summary["accepted_by_provider"] += first_ok
        summary["failed_or_blocked"] += first_failed
        summary["reminders_claimed"] = int(conn.execute(
            "SELECT COUNT(*) FROM follow_up_schedule WHERE status IN ('CLAIMED','PREPARED')"
        ).fetchone()[0])
        cycle_status = "success" if summary["failed_or_blocked"] == 0 else "partial"
        _record_worker_run(conn, status=cycle_status, summary=summary)
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Revenue Engine automated discovery and bounded outreach cycle")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Inspect prospects without saving or sending")
    mode.add_argument("--send", action="store_true", help="Persist qualified prospects and run the approved automated campaign")
    parser.add_argument("--country", default=settings.target_market_country or "India")
    parser.add_argument("--max-new", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.max_new <= 5:
        raise SystemExit("--max-new must be from 1 to 5")
    try:
        result = run_cycle(country=args.country, max_new=args.max_new, live=args.send)
    except (DiscoveryError, RuntimeError, ValueError) as exc:
        # Safe messages only; never save exception text, URLs, recipients or provider payloads.
        print("Cycle stopped safely: " + str(exc)[:220])
        if args.send:
            try:
                from app.database import connect
                with connect() as conn:
                    _record_worker_run(
                        conn,
                        status="blocked" if isinstance(exc, RuntimeError) else "failed",
                        summary={
                            "mode": "LIVE",
                            "country": args.country,
                            "failure_class": type(exc).__name__,
                            "failed_or_blocked": 1,
                        },
                    )
            except Exception:
                # Status logging must never obscure the original safe error.
                pass
        raise SystemExit(1)
    print("Revenue Engine automated cycle")
    for key, value in result.items():
        if key in {"city", "segment", "country", "mode", "live_send_block"}:
            print(f"{key}={value}")
        else:
            print(f"{key}={value}")
    if result["mode"] == "PREVIEW":
        print("Preview only: no leads were saved and no email was sent.")
    elif result["live_send_block"]:
        print("No outreach sent because the market-validation gate is incomplete.")


if __name__ == "__main__":
    main()
