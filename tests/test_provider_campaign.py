import sqlite3
import unittest
from unittest.mock import Mock
from app.database import SCHEMA
from app.models import LeadState
from app.brevo_client import BrevoResult
from app.campaign_sender import send_prepared, send_prepared_batch


class TestProviderCampaign(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)", ("provider-place", "Test Coaching", "contact@coachinginstitute.in", LeadState.QUALIFIED.value))
        self.conn.commit()
        self.lead_id = self.conn.execute("SELECT id FROM leads").fetchone()[0]

    def tearDown(self):
        self.conn.close()

    def add_prepared(self, n=1):
        ids = []
        for index in range(n):
            cur = self.conn.execute(
                "INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status) VALUES(?,?,?,?,1,'PREPARED')",
                (self.lead_id, "contact@coachinginstitute.in", f"Question {index}", "How do you handle enquiries?"),
            )
            ids.append(cur.lastrowid)
        self.conn.commit()
        return ids

    def test_brevo_success_updates_provider_and_lead_state(self):
        outreach_id = self.add_prepared()[0]
        sender = Mock(return_value=BrevoResult(True, 201, "brevo-message-1", "accepted"))
        result = send_prepared(
            self.conn, outreach_id, dry_run=False, provider="brevo",
            api_key="test-key", sender="hello@sanchit-agarwal.me",
            reply_to="noreply.sanchitagarwal@gmail.com", sender_function=sender,
        )
        row = self.conn.execute("SELECT status,provider,provider_message_id FROM outreach WHERE id=?", (outreach_id,)).fetchone()
        lead_state = self.conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0]
        self.assertTrue(result.success)
        self.assertEqual(tuple(row), ("SENT", "brevo", "brevo-message-1"))
        self.assertEqual(lead_state, LeadState.CONTACTED.value)

    def test_brevo_dry_run_never_calls_provider(self):
        outreach_id = self.add_prepared()[0]
        sender = Mock()
        result = send_prepared(self.conn, outreach_id, dry_run=True, provider="brevo", sender_function=sender)
        self.assertTrue(result.dry_run)
        sender.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT status FROM outreach WHERE id=?", (outreach_id,)).fetchone()[0], "PREPARED")

    def test_consecutive_failure_guard_stops_batch(self):
        ids = self.add_prepared(5)
        sender = Mock(return_value=BrevoResult(False, 401, None, "brevo_http_401"))
        sleep = Mock()
        result = send_prepared_batch(
            self.conn, dry_run=False, limit=5, provider="brevo",
            api_key="key", sender="hello@sanchit-agarwal.me", sender_function=sender,
            respect_rate_limits=True, sleep_function=sleep, random_integer=lambda _a, _b: 8,
        )
        self.assertEqual(len(result), 3)
        self.assertEqual(sender.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        states = [self.conn.execute("SELECT status FROM outreach WHERE id=?", (i,)).fetchone()[0] for i in ids]
        self.assertEqual(states, ["FAILED", "FAILED", "FAILED", "PREPARED", "PREPARED"])


if __name__ == "__main__":
    unittest.main()
