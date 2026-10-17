import sqlite3
import unittest
from unittest.mock import Mock

from app.database import SCHEMA
from app.models import LeadState
from app.outreach import (
    PREPARED_STATUS,
    SENT_STATUS,
    FAILED_STATUS,
)
from app.sendgrid_client import SendGridResult
from app.campaign_sender import (
    send_prepared,
    send_prepared_batch,
)


class TestCampaignSender(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

        self.conn.execute(
            """
            INSERT INTO leads (
                place_id,
                business_name,
                email,
                state
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "place-1",
                "Test Coaching",
                "hello@example.test",
                LeadState.QUALIFIED.value,
            ),
        )
        self.conn.commit()

        self.lead_id = self.conn.execute(
            "SELECT id FROM leads"
        ).fetchone()[0]

    def tearDown(self):
        self.conn.close()

    def add_outreach(
        self,
        status=PREPARED_STATUS,
        attempt=1,
    ):
        cur = self.conn.execute(
            """
            INSERT INTO outreach (
                lead_id,
                email,
                subject,
                body,
                attempt_number,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self.lead_id,
                "hello@example.test",
                "Quick question",
                "How do you handle enquiries?",
                attempt,
                status,
            ),
        )
        self.conn.commit()

        return cur.lastrowid

    def accepted_sender(self, *args, **kwargs):
        return SendGridResult(
            True,
            202,
            "sg-123",
            "accepted",
        )

    def rejected_sender(self, *args, **kwargs):
        return SendGridResult(
            False,
            401,
            None,
            "sendgrid_http_401",
        )

    def test_dry_run_makes_zero_external_requests(self):
        outreach_id = self.add_outreach()
        sender = Mock()

        result = send_prepared(
            self.conn,
            outreach_id,
            dry_run=True,
            sender_function=sender,
        )

        self.assertFalse(result.success)
        self.assertTrue(result.dry_run)
        self.assertEqual(
            result.reason,
            "dry_run_no_external_request",
        )

        sender.assert_not_called()

        row = self.conn.execute(
            "SELECT status FROM outreach WHERE id = ?",
            (outreach_id,),
        ).fetchone()

        self.assertEqual(
            row[0],
            PREPARED_STATUS,
        )

    def test_sendgrid_success_marks_sent_and_contacts_lead(self):
        outreach_id = self.add_outreach()

        result = send_prepared(
            self.conn,
            outreach_id,
            dry_run=False,
            sender_function=self.accepted_sender,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.message_id,
            "sg-123",
        )

        outreach = self.conn.execute(
            "SELECT status FROM outreach WHERE id = ?",
            (outreach_id,),
        ).fetchone()

        lead = self.conn.execute(
            "SELECT state FROM leads WHERE id = ?",
            (self.lead_id,),
        ).fetchone()

        self.assertEqual(
            outreach[0],
            SENT_STATUS,
        )

        self.assertEqual(
            lead[0],
            LeadState.CONTACTED.value,
        )

    def test_sendgrid_failure_marks_failed_and_does_not_contact_lead(
        self,
    ):
        outreach_id = self.add_outreach()

        result = send_prepared(
            self.conn,
            outreach_id,
            dry_run=False,
            sender_function=self.rejected_sender,
        )

        self.assertFalse(result.success)

        outreach = self.conn.execute(
            "SELECT status FROM outreach WHERE id = ?",
            (outreach_id,),
        ).fetchone()

        lead = self.conn.execute(
            "SELECT state FROM leads WHERE id = ?",
            (self.lead_id,),
        ).fetchone()

        self.assertEqual(
            outreach[0],
            FAILED_STATUS,
        )

        self.assertEqual(
            lead[0],
            LeadState.QUALIFIED.value,
        )

    def test_sent_record_cannot_be_sent_again(self):
        outreach_id = self.add_outreach(
            status=SENT_STATUS
        )
        sender = Mock()

        result = send_prepared(
            self.conn,
            outreach_id,
            dry_run=False,
            sender_function=sender,
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.reason,
            "outreach_not_prepared",
        )

        sender.assert_not_called()

    def test_failed_record_cannot_be_resent_by_this_operation(self):
        outreach_id = self.add_outreach(
            status=FAILED_STATUS
        )
        sender = Mock()

        result = send_prepared(
            self.conn,
            outreach_id,
            dry_run=False,
            sender_function=sender,
        )

        self.assertFalse(result.success)
        self.assertEqual(
            result.reason,
            "outreach_not_prepared",
        )

        sender.assert_not_called()

    def test_batch_is_capped_by_explicit_limit(self):
        # Each prepared first-contact row represents a different recipient.
        first = self.add_outreach()
        second_lead = self.conn.execute(
            "INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)",
            ("place-2", "Second Coaching", "second@example.test", LeadState.QUALIFIED.value),
        ).lastrowid
        second = self.conn.execute(
            """INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status)
               VALUES(?,?,?,?,1,?)""",
            (second_lead, "second@example.test", "Quick question", "How do you handle enquiries?", PREPARED_STATUS),
        ).lastrowid
        third_lead = self.conn.execute(
            "INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)",
            ("place-3", "Third Coaching", "third@example.test", LeadState.QUALIFIED.value),
        ).lastrowid
        third = self.conn.execute(
            """INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status)
               VALUES(?,?,?,?,1,?)""",
            (third_lead, "third@example.test", "Quick question", "How do you handle enquiries?", PREPARED_STATUS),
        ).lastrowid
        self.conn.commit()

        sender = Mock(
            side_effect=self.accepted_sender
        )

        results = send_prepared_batch(
            self.conn,
            dry_run=False,
            limit=2,
            sender_function=sender,
        )

        self.assertEqual(
            len(results),
            2,
        )

        self.assertEqual(
            sender.call_count,
            2,
        )

        statuses = {
            row["id"]: row["status"]
            for row in self.conn.execute(
                """
                SELECT id, status
                FROM outreach
                ORDER BY id
                """
            ).fetchall()
        }

        self.assertEqual(
            statuses[first],
            SENT_STATUS,
        )

        self.assertEqual(
            statuses[second],
            SENT_STATUS,
        )

        self.assertEqual(
            statuses[third],
            PREPARED_STATUS,
        )


if __name__ == "__main__":
    unittest.main()