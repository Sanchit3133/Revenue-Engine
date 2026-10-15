"""Prepare Revenue Engine outreach messages without sending them.

This script only creates PREPARED outreach records.

It does not:
    - contact SendGrid
    - send email
    - move leads to CONTACTED
    - create proposals
    - disclose pricing

Live delivery is handled separately by:
    python -m scripts.run_campaign --send
"""

from __future__ import annotations

import argparse

from app.config import settings
from app.database import connect, init_db
from app.models import LeadState
from app.outreach import prepare_first_contact


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare first-contact outreach for eligible "
            "Revenue Engine leads."
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Maximum number of QUALIFIED leads to prepare. "
            "If omitted, the configured campaign capacity is used."
        ),
    )

    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    init_db()

    preparation_limit = (
        args.limit
        if args.limit is not None
        else settings.campaign_limit
    )

    with connect() as conn:
        leads = conn.execute(
            """
            SELECT
                id,
                business_name,
                email,
                state,
                score,
                signal
            FROM leads
            WHERE state = ?
            ORDER BY score DESC, id ASC
            LIMIT ?
            """,
            (
                LeadState.QUALIFIED.value,
                preparation_limit,
            ),
        ).fetchall()

        print("=" * 60)
        print("REVENUE ENGINE — CAMPAIGN PREPARATION")
        print("=" * 60)
        print(f"Database: {settings.db_path}")
        print(f"Preparation limit: {preparation_limit}")
        print(f"Qualified leads found: {len(leads)}")
        print()

        if not leads:
            print("No QUALIFIED leads are waiting for preparation.")
            return

        prepared_count = 0
        skipped_count = 0

        for lead in leads:
            result = prepare_first_contact(
                conn,
                int(lead["id"]),
                daily_limit=settings.daily_limit,
                domain_cooldown=settings.domain_cooldown,
            )

            if result.allowed:
                prepared_count += 1

                print(
                    f"[PREPARED] "
                    f"lead_id={lead['id']} "
                    f"business={lead['business_name']} "
                    f"email={lead['email']} "
                    f"outreach_id={result.outreach_id}"
                )

                if result.message is not None:
                    print(
                        f"  Subject: {result.message.subject}"
                    )

            else:
                skipped_count += 1

                print(
                    f"[SKIPPED] "
                    f"lead_id={lead['id']} "
                    f"business={lead['business_name']} "
                    f"reason={result.reason}"
                )

        print()
        print("=" * 60)
        print("CAMPAIGN PREPARATION COMPLETE")
        print("=" * 60)
        print(f"Prepared: {prepared_count}")
        print(f"Skipped:  {skipped_count}")
        print()
        print(
            "No emails were sent. "
            "All created records remain PREPARED."
        )


if __name__ == "__main__":
    main()