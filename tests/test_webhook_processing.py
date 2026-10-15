import hashlib
import hmac
import json
import sqlite3
import unittest
from app.database import SCHEMA
from app.models import LeadState
from app.webhook_processing import (
    process_brevo_event,
    process_razorpay_event,
    verify_brevo_token,
    verify_razorpay_signature,
)


class TestWebhookProcessing(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)",
            ("place-one", "Test Coaching", "contact@coachinginstitute.in", LeadState.CONTACTED.value),
        )
        self.conn.commit()
        self.lead_id = self.conn.execute("SELECT id FROM leads").fetchone()[0]
        self.conn.execute(
            """INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status,provider,provider_message_id)
               VALUES(?,?,?,?,1,'SENT','brevo','brevo-message-123')""",
            (self.lead_id, "contact@coachinginstitute.in", "Question", "Body"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_brevo_delivered_event_moves_contacted_to_delivered(self):
        result = process_brevo_event(self.conn, {
            "event": "delivered", "email": "contact@coachinginstitute.in",
            "message-id": "brevo-message-123", "ts_event": 1234,
        })
        state = self.conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0]
        self.assertTrue(result["accepted"])
        self.assertEqual(state, LeadState.DELIVERED.value)

    def test_duplicate_brevo_event_is_idempotent(self):
        payload = {"event": "delivered", "email": "contact@coachinginstitute.in", "message-id": "brevo-message-123", "ts_event": 1234}
        first = process_brevo_event(self.conn, payload)
        second = process_brevo_event(self.conn, payload)
        count = self.conn.execute("SELECT COUNT(*) FROM email_events").fetchone()[0]
        self.assertFalse(first.get("duplicate", False))
        self.assertTrue(second["duplicate"])
        self.assertEqual(count, 1)

    def test_hard_bounce_suppresses_recipient_and_updates_state(self):
        result = process_brevo_event(self.conn, {
            "event": "hard_bounce", "email": "contact@coachinginstitute.in",
            "message-id": "brevo-message-123", "ts_event": 1235, "reason": "mailbox not found",
        })
        state = self.conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0]
        suppression = self.conn.execute("SELECT reason FROM suppressions WHERE email=?", ("contact@coachinginstitute.in",)).fetchone()
        self.assertTrue(result["accepted"])
        self.assertEqual(state, LeadState.BOUNCED.value)
        self.assertEqual(suppression[0], "hard_bounce")

    def test_complaint_suppresses_recipient(self):
        result = process_brevo_event(self.conn, {
            "event": "spam", "email": "contact@coachinginstitute.in",
            "message-id": "brevo-message-123", "ts_event": 1236,
        })
        self.assertTrue(result["accepted"])
        self.assertEqual(self.conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0], LeadState.COMPLAINT.value)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM suppressions WHERE email=?", ("contact@coachinginstitute.in",)).fetchone())

    def test_brevo_token_comparison(self):
        self.assertTrue(verify_brevo_token("abc-token", "abc-token"))
        self.assertFalse(verify_brevo_token("wrong", "abc-token"))
        self.assertFalse(verify_brevo_token("abc-token", None))

    def test_razorpay_signature_uses_raw_body_hmac(self):
        body = b'{"event":"payment.captured"}'
        secret = "test-webhook-secret"
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_razorpay_signature(body, signature, secret))
        self.assertFalse(verify_razorpay_signature(body, "0" * 64, secret))

    def _payment_payload(self, lead_id=None):
        notes = {"lead_id": lead_id} if lead_id is not None else {}
        return {
            "id": "evt_test_capture_01",
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": "pay_test_01", "order_id": "order_test_01", "status": "captured",
                "captured": True, "amount": 49000, "currency": "INR", "notes": notes,
            }}}
        }

    def test_captured_payment_records_payment_and_marks_paid(self):
        payload = self._payment_payload(self.lead_id)
        result = process_razorpay_event(self.conn, payload)
        state = self.conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0]
        payment = self.conn.execute("SELECT amount,currency,status FROM payments WHERE provider_payment_id='pay_test_01'").fetchone()
        self.assertTrue(result["accepted"])
        self.assertEqual(state, LeadState.PAID.value)
        self.assertEqual(tuple(payment), (49000, "INR", "captured"))

    def test_duplicate_captured_payment_does_not_duplicate_payment_row(self):
        payload = self._payment_payload(self.lead_id)
        process_razorpay_event(self.conn, payload)
        result = process_razorpay_event(self.conn, payload)
        count = self.conn.execute("SELECT COUNT(*) FROM payments WHERE provider_payment_id='pay_test_01'").fetchone()[0]
        self.assertTrue(result["duplicate"])
        self.assertEqual(count, 1)

    def test_unmapped_capture_is_retained_for_reconciliation(self):
        result = process_razorpay_event(self.conn, self._payment_payload())
        row = self.conn.execute("SELECT id,processed_at,payload_json FROM webhook_events WHERE provider='razorpay'").fetchone()
        self.assertTrue(result["reconciliation_required"])
        self.assertIsNone(row["processed_at"])
        self.assertIn("pay_test_01", row["payload_json"])

    def test_registered_order_mapping_is_completed_in_place(self):
        self.conn.execute(
            "INSERT INTO payments(lead_id,provider,provider_payment_id,provider_order_id,amount,currency,status) VALUES(?,'razorpay',NULL,'order_test_01',49000,'INR','created')",
            (self.lead_id,),
        )
        self.conn.commit()
        result = process_razorpay_event(self.conn, self._payment_payload())
        rows = self.conn.execute("SELECT provider_payment_id,status FROM payments WHERE provider_order_id='order_test_01'").fetchall()
        self.assertTrue(result["accepted"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(tuple(rows[0]), ("pay_test_01", "captured"))

    def test_invalid_non_captured_payment_is_not_processed(self):
        payload = self._payment_payload(self.lead_id)
        payload["payload"]["payment"]["entity"]["status"] = "authorized"
        result = process_razorpay_event(self.conn, payload)
        self.assertFalse(result["accepted"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
