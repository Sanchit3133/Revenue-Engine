import sqlite3
import unittest

from app.brevo_inbound import process_brevo_inbound
from app.database import SCHEMA
from app.followup_scheduler import SCHEDULED, schedule_next_follow_up
from app.models import LeadState


class TestBrevoInbound(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            """INSERT INTO leads(place_id,business_name,email,website,state)
               VALUES(?,?,?,?,?)""",
            ("overture:reply-1", "Example Coaching", "owner@example.in",
             "https://example.in", LeadState.CONTACTED.value),
        )
        self.conn.commit()
        self.lead_id = self.conn.execute("SELECT id FROM leads").fetchone()[0]
        self.conn.execute(
            """INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status,provider,provider_message_id)
               VALUES(?,?,?,?,1,'SENT','brevo','brevo-message-123')""",
            (self.lead_id, "owner@example.in", "Quick question", "Initial email"),
        )
        self.conn.commit()
        self.outreach_id = self.conn.execute("SELECT id FROM outreach").fetchone()[0]

    def tearDown(self):
        self.conn.close()

    def _payload(self, body="STOP", subject="Re: Quick question"):
        return {
            "items": [{
                "From": {"Name": "Business Owner", "Address": "owner@example.in"},
                "Subject": subject,
                "InReplyTo": "<brevo-message-123>",
                "TextBody": body,
            }]
        }

    def test_unsubscribe_reply_is_matched_suppressed_and_noted(self):
        schedule_next_follow_up(
            self.conn, lead_id=self.lead_id, source_outreach_id=self.outreach_id,
            accepted_attempt_number=1,
        )
        stats = process_brevo_inbound(self.conn, self._payload())
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["processed"], 1)
        self.assertEqual(
            self.conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0],
            LeadState.UNSUBSCRIBED.value,
        )
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM suppressions WHERE lower(email)=?", ("owner@example.in",)).fetchone())
        self.assertEqual(
            self.conn.execute("SELECT status FROM follow_up_schedule").fetchone()[0],
            "CANCELLED",
        )

    def test_same_inbound_reply_is_idempotent(self):
        first = process_brevo_inbound(self.conn, self._payload("Could you tell me more?"))
        second = process_brevo_inbound(self.conn, self._payload("Could you tell me more?"))
        self.assertEqual(first["processed"], 1)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_events").fetchone()[0], 1)

    def test_ambiguous_or_unknown_sender_is_not_attached(self):
        payload = self._payload("Can we discuss?")
        payload["items"][0]["From"]["Address"] = "unknown@example.in"
        stats = process_brevo_inbound(self.conn, payload)
        self.assertEqual(stats["unmatched"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_events").fetchone()[0], 0)

    def test_no_auto_reply_is_sent_by_inbound_processor(self):
        stats = process_brevo_inbound(self.conn, self._payload("Interested, please share more details."))
        self.assertEqual(stats["processed"], 1)
        # Processing only writes the reply/state; no outgoing outreach row is created.
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
