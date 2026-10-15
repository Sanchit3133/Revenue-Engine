"""Annotate a failed outreach row without retrying or sending it."""
from __future__ import annotations

import argparse

from app.database import connect, init_db
from app.outreach_admin import SUPPORTED_PROVIDERS, annotate_failed_outreach


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outreach-id", type=int, required=True)
    parser.add_argument("--provider", choices=sorted(SUPPORTED_PROVIDERS), required=True)
    parser.add_argument("--reason", required=True, help="Short, non-secret diagnostic for the failed attempt")
    parser.add_argument(
        "--confirm-recorded-failure",
        action="store_true",
        help="Confirm this annotation reflects an already-observed failure; this does not retry or send.",
    )
    args = parser.parse_args()

    init_db()
    with connect() as conn:
        result = annotate_failed_outreach(
            conn,
            outreach_id=args.outreach_id,
            provider=args.provider,
            reason=args.reason,
            confirmed=args.confirm_recorded_failure,
        )

    if not result["updated"]:
        raise SystemExit(f"Annotation refused: {result['reason']}")
    print("FAILED ROW ANNOTATED — NO EMAIL SENT")
    print(f"Outreach ID: {args.outreach_id}")
    print(f"Provider recorded: {args.provider}")
    print(f"Reason recorded: {args.reason.strip()}")
    print("Status remains FAILED; message content and lead state were not changed.")


if __name__ == "__main__":
    main()
