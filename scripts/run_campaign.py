"""Revenue Engine campaign runner with provider selection.

Safe defaults:
    python -m scripts.run_campaign --dry-run

Live sending requires --send and valid credentials for the selected provider.
"""

from __future__ import annotations

import argparse

from app.campaign_sender import send_prepared_batch
from app.config import settings, SUPPORTED_EMAIL_PROVIDERS
from app.database import connect, init_db


def _check_provider_configuration(provider: str) -> None:
    if provider == "brevo":
        if not settings.brevo_api_key:
            raise SystemExit("ERROR: BREVO_API_KEY is not configured.")
        if not settings.brevo_sender:
            raise SystemExit("ERROR: BREVO_SENDER is not configured.")
    elif provider == "sendgrid":
        if not settings.sendgrid_api_key:
            raise SystemExit("ERROR: SENDGRID_API_KEY is not configured.")
        if not settings.sendgrid_sender:
            raise SystemExit("ERROR: SENDGRID_SENDER is not configured.")
    else:
        raise SystemExit(f"ERROR: Unsupported email provider: {provider}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare or deliver outreach using the configured email provider."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect PREPARED records without external requests or state changes.",
    )
    mode.add_argument(
        "--send",
        action="store_true",
        help="Actually submit PREPARED records to the chosen provider.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of PREPARED records to process.",
    )
    parser.add_argument(
        "--provider",
        choices=sorted(SUPPORTED_EMAIL_PROVIDERS),
        default=None,
        help="Override EMAIL_PROVIDER for this run.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    provider = args.provider or settings.email_provider
    if args.send:
        _check_provider_configuration(provider)

    init_db()
    with connect() as conn:
        prepared = conn.execute(
            """
            SELECT id, lead_id, email, subject, attempt_number
            FROM outreach
            WHERE status = 'PREPARED'
            ORDER BY id ASC
            """
        ).fetchall()

        print("=" * 60)
        print("REVENUE ENGINE — OUTREACH DELIVERY")
        print("=" * 60)
        print(f"Mode: {'SEND' if args.send else 'DRY RUN'}")
        print(f"Provider: {provider}")
        print(f"Database: {settings.db_path}")
        print(f"Prepared records available: {len(prepared)}")
        print()

        if not prepared:
            print("No PREPARED outreach records are waiting.")
            return

        if args.send and getattr(settings, "market_validation_required", True):
            requested_limit = args.limit if args.limit is not None else settings.campaign_limit
            prepared_countries = conn.execute(
                """SELECT l.country
                   FROM outreach o JOIN leads l ON l.id=o.lead_id
                   WHERE o.status='PREPARED'
                   ORDER BY o.id ASC
                   LIMIT ?""",
                (requested_limit,),
            ).fetchall()
            unvalidated = []
            checked_countries = set()
            for item in prepared_countries:
                country = (item["country"] or "India").strip() or "India"
                if country.casefold() in checked_countries:
                    continue
                checked_countries.add(country.casefold())
                validation = conn.execute(
                    "SELECT status FROM market_validations WHERE lower(country)=lower(?) LIMIT 1",
                    (country,),
                ).fetchone()
                if validation is None or validation["status"] != "VALIDATED":
                    unvalidated.append(country)
            if unvalidated:
                names = ", ".join(sorted(set(unvalidated)))
                raise SystemExit(
                    "LIVE SEND BLOCKED: market validation is required for: " + names
                    + ". Record all seven gates and dated evidence in the owner dashboard first."
                )

        results = send_prepared_batch(
            conn,
            dry_run=not args.send,
            limit=args.limit if args.limit is not None else settings.campaign_limit,
            provider=provider,
            respect_rate_limits=args.send,
        )
        for result in results:
            if result.success:
                label = "ACCEPTED"
            elif result.dry_run:
                label = "DRY-RUN"
            else:
                label = "FAILED"
            print(
                f"[{label}] outreach_id={result.outreach_id} "
                f"provider={result.provider or provider} reason={result.reason}"
            )

        print()
        print(f"Processed: {len(results)}")
        if args.dry_run:
            print("DRY RUN — NO EXTERNAL SEND REQUESTS WERE MADE.")
        elif len(results) < min(len(prepared), args.limit or settings.campaign_limit):
            print("Delivery stopped early, possibly because the consecutive-failure guard activated.")


if __name__ == "__main__":
    main()
