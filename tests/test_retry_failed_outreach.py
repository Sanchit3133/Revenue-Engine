import sqlite3
import unittest
from app.database import SCHEMA
from app.models import LeadState
from app.outreach import FAILED_STATUS, PREPARED_STATUS, prepare_failed_retry


class TestFailedOutreachRetry(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)",
            ("retry-place", "Test Coaching", "contact@coachinginstitute.in", LeadState.QUALIFIED.value),
        )
        self.conn.commit()
        self.lead_id = self.conn.execute("SELECT id FROM leads").fetchone()[0]
        cur = self.conn.execute(
            """INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status,provider,last_error)
               VALUES(?,?,?,?,1,'FAILED','sendgrid','sendgrid_http_401')""",
            (self.lead_id, "contact@coachinginstitute.in", "Quick question", "How do you handle enquiries?"),
        )
        self.conn.commit()
        self.outreach_id = cur.lastrowid

    def tearDown(self):
        self.conn.close()

    def test_retry_requires_explicit_confirmation(self):
        result = prepare_failed_retry(self.conn, self.outreach_id, confirmed_not_delivered=False)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "confirmation_required")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0], 1)

    def test_confirmed_retry_preserves_failed_row_and_links_new_prepared_row(self):
        result = prepare_failed_retry(self.conn, self.outreach_id, confirmed_not_delivered=True, daily_limit=25)
        self.assertTrue(result.allowed)
        original = self.conn.execute("SELECT status,last_error FROM outreach WHERE id=?", (self.outreach_id,)).fetchone()
        retry = self.conn.execute("SELECT status,attempt_number,retry_of_outreach_id FROM outreach WHERE id=?", (result.outreach_id,)).fetchone()
        self.assertEqual(tuple(original), (FAILED_STATUS, "sendgrid_http_401"))
        self.assertEqual(tuple(retry), (PREPARED_STATUS, 1, self.outreach_id))

    def test_duplicate_retry_is_blocked(self):
        first = prepare_failed_retry(self.conn, self.outreach_id, confirmed_not_delivered=True, daily_limit=25)
        second = prepare_failed_retry(self.conn, self.outreach_id, confirmed_not_delivered=True, daily_limit=25)
        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual(second.reason, "retry_already_prepared")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0], 2)

    def test_suppressed_email_cannot_be_retried(self):
        self.conn.execute("INSERT INTO suppressions(email,reason,source) VALUES(?,?,?)", ("contact@coachinginstitute.in", "unsubscribe", "test"))
        self.conn.commit()
        result = prepare_failed_retry(self.conn, self.outreach_id, confirmed_not_delivered=True, daily_limit=25)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "email_suppressed")


if __name__ == "__main__":
    unittest.main()
