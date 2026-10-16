import sqlite3
import unittest
from app.database import SCHEMA
from app.models import LeadState
from app.reply_processor import process_reply


class TestReplyProcessor(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)",
            ("place-one", "Test Coaching", "owner@example.com", LeadState.CONTACTED.value),
        )
        self.conn.commit()
        self.lead_id = self.conn.execute("SELECT id FROM leads").fetchone()[0]

    def tearDown(self):
        self.conn.close()

    def test_interested_reply_updates_pipeline_without_sending(self):
        result = process_reply(self.conn, lead_id=self.lead_id, from_email="owner@example.com", subject="Re", body="Yes, tell me more")
        state = self.conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0]
        self.assertEqual(result["intent"], "INTERESTED")
        self.assertEqual(state, LeadState.INTERESTED.value)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_events").fetchone()[0], 1)

    def test_unsubscribe_reply_suppresses_email(self):
        result = process_reply(self.conn, lead_id=self.lead_id, from_email="owner@example.com", subject="", body="STOP")
        state = self.conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0]
        suppression = self.conn.execute("SELECT reason FROM suppressions WHERE email='owner@example.com'").fetchone()
        self.assertEqual(result["intent"], "UNSUBSCRIBE")
        self.assertEqual(state, LeadState.UNSUBSCRIBED.value)
        self.assertEqual(suppression[0], "unsubscribe_reply")

    def test_budget_reply_is_recorded_but_does_not_propose(self):
        result = process_reply(self.conn, lead_id=self.lead_id, from_email="owner@example.com", subject="Re", body="Our budget is ₹10,000")
        state = self.conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0]
        self.assertEqual(result["intent"], "BUDGET_DISCLOSED")
        self.assertTrue(result["budget_disclosed"])
        self.assertEqual(state, LeadState.BUDGET_DISCLOSED.value)

    def test_duplicate_reply_is_idempotent(self):
        args = dict(lead_id=self.lead_id, from_email="owner@example.com", subject="Re", body="No thanks")
        first = process_reply(self.conn, **args)
        second = process_reply(self.conn, **args)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reply_events").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
