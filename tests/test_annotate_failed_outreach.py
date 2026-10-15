import sqlite3
import unittest

from app.database import SCHEMA
from app.models import LeadState
from app.outreach import FAILED_STATUS, PREPARED_STATUS
from app.outreach_admin import annotate_failed_outreach


class TestFailedOutreachAnnotation(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO leads (place_id, business_name, email, state) VALUES (?, ?, ?, ?)",
            ("place-annotate", "Test Coaching", "contact@coaching.example", LeadState.QUALIFIED.value),
        )
        lead_id = self.conn.execute("SELECT id FROM leads").fetchone()[0]
        self.conn.execute(
            "INSERT INTO outreach (lead_id, email, subject, body, attempt_number, status) VALUES (?, ?, ?, ?, ?, ?)",
            (lead_id, "contact@coaching.example", "Subject", "Body", 1, FAILED_STATUS),
        )
        self.failed_id = self.conn.execute("SELECT id FROM outreach").fetchone()[0]
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_annotations_change_only_provider_diagnostic(self):
        result = annotate_failed_outreach(
            self.conn,
            outreach_id=self.failed_id,
            provider="sendgrid",
            reason="sendgrid_http_401: Maximum credits exceeded",
            confirmed=True,
        )
        self.assertTrue(result["updated"])
        row = self.conn.execute(
            "SELECT status, provider, last_error, subject, body FROM outreach WHERE id = ?",
            (self.failed_id,),
        ).fetchone()
        self.assertEqual(row["status"], FAILED_STATUS)
        self.assertEqual(row["provider"], "sendgrid")
        self.assertEqual(row["last_error"], "sendgrid_http_401: Maximum credits exceeded")
        self.assertEqual(row["subject"], "Subject")
        self.assertEqual(row["body"], "Body")

    def test_confirmation_is_required(self):
        result = annotate_failed_outreach(
            self.conn, outreach_id=self.failed_id, provider="sendgrid", reason="known failure"
        )
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "confirmation_required")

    def test_prepared_row_cannot_be_annotated_as_failed(self):
        self.conn.execute("UPDATE outreach SET status = ? WHERE id = ?", (PREPARED_STATUS, self.failed_id))
        self.conn.commit()
        result = annotate_failed_outreach(
            self.conn,
            outreach_id=self.failed_id,
            provider="sendgrid",
            reason="known failure",
            confirmed=True,
        )
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "outreach_not_failed")


if __name__ == "__main__":
    unittest.main()
