"""Register a Razorpay order-to-lead mapping before checkout completes.

Prefer adding notes.revenue_engine_lead_id to the Razorpay order itself. Use
this command only when the external order creator cannot attach that note.
Amounts are specified in the smallest currency unit (paise for INR).
"""
from __future__ import annotations
import argparse
from app.database import connect, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lead-id", type=int, required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--amount-minor", type=int, required=True, help="For INR, amount in paise; ₹490 = 49000")
    parser.add_argument("--currency", default="INR")
    args = parser.parse_args()
    if args.lead_id < 1 or args.amount_minor < 1 or not args.order_id.strip():
        raise SystemExit("lead-id, order-id, and amount-minor must be valid positive values")
    init_db()
    with connect() as conn:
        lead = conn.execute("SELECT id FROM leads WHERE id = ?", (args.lead_id,)).fetchone()
        if lead is None:
            raise SystemExit("Lead not found; order mapping was not created")
        existing = conn.execute(
            "SELECT id, lead_id, amount, currency, status FROM payments WHERE lower(provider)='razorpay' AND provider_order_id=? AND provider_payment_id IS NULL ORDER BY id DESC LIMIT 1",
            (args.order_id.strip(),),
        ).fetchone()
        if existing:
            if existing["lead_id"] != args.lead_id or existing["amount"] not in (None, args.amount_minor) or (existing["currency"] and existing["currency"].upper() != args.currency.upper()):
                raise SystemExit("Existing order mapping conflicts with supplied lead/amount/currency")
            print(f"Order mapping already exists: row_id={existing['id']} status={existing['status']}")
            return
        conn.execute(
            "INSERT INTO payments(lead_id,provider,provider_payment_id,provider_order_id,amount,currency,status) VALUES(?,'razorpay',NULL,?,?,?,'created')",
            (args.lead_id, args.order_id.strip(), args.amount_minor, args.currency.strip().upper()),
        )
        conn.commit()
    print("Razorpay order mapping recorded. No payment was initiated or captured by this command.")

if __name__ == "__main__":
    main()
