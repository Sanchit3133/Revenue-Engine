"""Create a new PREPARED record for a confirmed non-delivery.

This command does not send email. It preserves the original FAILED row and
creates a separate linked retry row with the same sales-touch number.
"""

from __future__ import annotations

import argparse

from app.config import settings
from app.database import connect, init_db
from app.outreach import prepare_failed_retry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outreach-id", type=int, required=True)
    parser.add_argument(
        "--confirm-not-delivered",
        action="store_true",
        help="Confirm that the provider definitely did not accept/deliver the source message.",
    )
    args = parser.parse_args()
    if args.outreach_id < 1:
        raise SystemExit("--outreach-id must be at least 1")
    if not args.confirm_not_delivered:
        raise SystemExit(
            "Safety stop: review the provider result, then rerun with "
            "--confirm-not-delivered only if non-delivery is confirmed."
        )

    init_db()
    with connect() as conn:
        result = prepare_failed_retry(
            conn,
            args.outreach_id,
            confirmed_not_delivered=True,
            daily_limit=settings.daily_limit,
        )
        if not result.allowed:
            print(f"[NOT PREPARED] source_outreach_id={args.outreach_id} reason={result.reason}")
            raise SystemExit(1)
        print("RETRY PREPARED — NO EMAIL SENT")
        print(f"Original failed record: {args.outreach_id} (preserved)")
        print(f"New prepared record: {result.outreach_id}")
        print(f"Sales-touch attempt number: {result.attempt_number}")
        print("Next command to inspect only: python -m scripts.run_campaign --dry-run --limit 1 --provider brevo")


if __name__ == "__main__":
    main()
