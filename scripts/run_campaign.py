"""Phase 1 campaign runner.

This is intentionally a dry-run-only skeleton. Real discovery, verification,
SendGrid delivery and event ingestion are added only after their safety gates
are implemented.
"""

from __future__ import annotations

import argparse

from app.config import settings
from app.database import init_db
from app.outreach import campaign_capacity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Required in Phase 1; no email is sent.",
    )
    args = parser.parse_args()

    if not args.dry_run:
        raise SystemExit(
            "Phase 1 is dry-run only. Use: "
            "python -m scripts.run_campaign --dry-run"
        )

    init_db()

    print("=" * 60)
    print("REVENUE ENGINE — PHASE 1")
    print("=" * 60)
    print(f"Environment: {settings.environment}")
    print(f"Database: {settings.db_path}")
    print(f"Landing page: {settings.landing_page_url}")
    print(f"Campaign capacity: {campaign_capacity()}")
    print()
    print("DRY RUN — NOTHING WAS SENT.")
    print("Next phase: lead discovery + qualification + verification.")


if __name__ == "__main__":
    main()
