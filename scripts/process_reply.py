"""Manually record a reply after copying it from the business inbox.

Example:
python -m scripts.process_reply --lead-id 2 --from-email owner@example.com --subject "Re: Quick question" --body "Yes, tell me more"
This classifies and records a reply; it never sends an automatic response.
"""
from __future__ import annotations
import argparse
import json
from app.database import connect, init_db
from app.reply_processor import process_reply


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lead-id", type=int, required=True)
    parser.add_argument("--from-email", required=True)
    parser.add_argument("--subject", default="")
    content = parser.add_mutually_exclusive_group(required=True)
    content.add_argument("--body", help="Reply body text")
    content.add_argument("--body-file", help="UTF-8 text file containing reply body")
    args = parser.parse_args()
    if args.lead_id < 1:
        raise SystemExit("lead-id must be positive")
    body = args.body if args.body is not None else open(args.body_file, encoding="utf-8").read()
    init_db()
    with connect() as conn:
        result = process_reply(
            conn,
            lead_id=args.lead_id,
            from_email=args.from_email,
            subject=args.subject,
            body=body,
        )
    print(json.dumps(result, indent=2))
    if not result.get("accepted"):
        raise SystemExit(1)

if __name__ == "__main__":
    main()
