"""Manually reconcile a verified Razorpay capture that could not map to a lead.

Use only after independently confirming that the captured payment belongs to
that Revenue Engine lead. The command does not call Razorpay or send email.
"""
from __future__ import annotations
import argparse
import json
from app.config import settings
from app.database import connect, init_db
from app.webhook_processing import process_razorpay_event


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", type=int, required=True, help="Local webhook_events row id")
    parser.add_argument("--lead-id", type=int, required=True)
    parser.add_argument("--confirm-payment-captured", action="store_true")
    args = parser.parse_args()
    if args.event_id < 1 or args.lead_id < 1:
        raise SystemExit("event-id and lead-id must be positive integers")
    if not args.confirm_payment_captured:
        raise SystemExit("Safety stop: confirm the payment in Razorpay Dashboard, then rerun with --confirm-payment-captured")
    init_db()
    with connect() as conn:
        event = conn.execute(
            "SELECT id, provider, event_type, processed_at, payload_json FROM webhook_events WHERE id = ?",
            (args.event_id,),
        ).fetchone()
        if event is None or event["provider"] != "razorpay":
            raise SystemExit("Razorpay webhook event was not found")
        if event["processed_at"]:
            raise SystemExit("This webhook event has already been processed")
        try:
            payload = json.loads(event["payload_json"] or "")
        except json.JSONDecodeError as exc:
            raise SystemExit("Stored webhook payload is invalid; do not mark this payment PAID manually") from exc
        result = process_razorpay_event(conn, payload, lead_id_override=args.lead_id)
        print(json.dumps(result, indent=2))
        if not result.get("accepted") or result.get("reconciliation_required"):
            raise SystemExit(1)

if __name__ == "__main__":
    main()
