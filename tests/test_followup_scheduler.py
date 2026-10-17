import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from app.database import SCHEMA
from app.followup_scheduler import (
    CANCELLED, CLAIMED, COMPLETED, FAILED, SCHEDULED, cancel_follow_ups,
    claim_due_follow_ups, finish_follow_up, mark_follow_up_prepared, release_stale_claims,
    schedule_next_follow_up,
)


class TestFollowUpScheduler(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        cur = self.conn.execute(
            "INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)",
            ("test-overture-1", "Test Coaching Centre", "hello@training.example", "CONTACTED"),
        )
        self.lead_id = cur.lastrowid
        cur = self.conn.execute(
            """INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status,provider)
               VALUES(?,?,?,?,1,'SENT','brevo')""",
            (self.lead_id, "hello@training.example", "A question", "Test body"),
        )
        self.outreach_id = cur.lastrowid
        self.conn.commit()
        self.sent_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()

    def test_first_touch_schedules_one_followup_four_days_later(self):
        self.assertTrue(schedule_next_follow_up(
            self.conn, lead_id=self.lead_id, source_outreach_id=self.outreach_id,
            accepted_attempt_number=1, sent_at=self.sent_at,
        ))
        row = self.conn.execute("SELECT * FROM follow_up_schedule").fetchone()
        self.assertEqual(row["status"], SCHEDULED)
        self.assertEqual(row["attempt_number"], 2)
        due = datetime.fromisoformat(row["due_at"])
        self.assertEqual(due, self.sent_at + timedelta(days=4))

    def test_unique_schedule_prevents_duplicate_reminder(self):
        kwargs = dict(lead_id=self.lead_id, source_outreach_id=self.outreach_id,
                      accepted_attempt_number=1, sent_at=self.sent_at)
        self.assertTrue(schedule_next_follow_up(self.conn, **kwargs))
        self.assertFalse(schedule_next_follow_up(self.conn, **kwargs))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM follow_up_schedule").fetchone()[0], 1)

    def test_due_reminder_is_claimed_only_once(self):
        schedule_next_follow_up(
            self.conn, lead_id=self.lead_id, source_outreach_id=self.outreach_id,
            accepted_attempt_number=1, sent_at=self.sent_at,
        )
        due = self.sent_at + timedelta(days=4, seconds=1)
        claims = claim_due_follow_ups(self.conn, limit=5, now=due)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["lead_id"], self.lead_id)
        self.assertEqual(claim_due_follow_ups(self.conn, limit=5, now=due), [])
        row = self.conn.execute("SELECT status,claimed_at FROM follow_up_schedule").fetchone()
        self.assertEqual(row["status"], CLAIMED)
        self.assertTrue(row["claimed_at"])

    def test_reply_cancels_pending_followups(self):
        schedule_next_follow_up(
            self.conn, lead_id=self.lead_id, source_outreach_id=self.outreach_id,
            accepted_attempt_number=1, sent_at=self.sent_at,
        )
        count = cancel_follow_ups(self.conn, self.lead_id, reason="prospect_replied")
        self.assertEqual(count, 1)
        self.assertEqual(self.conn.execute("SELECT status FROM follow_up_schedule").fetchone()[0], CANCELLED)

    def test_third_touch_never_schedules_a_fourth_touch(self):
        self.assertFalse(schedule_next_follow_up(
            self.conn, lead_id=self.lead_id, source_outreach_id=self.outreach_id,
            accepted_attempt_number=3, sent_at=self.sent_at,
        ))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM follow_up_schedule").fetchone()[0], 0)

    def test_stale_claim_is_released_for_retry(self):
        schedule_next_follow_up(
            self.conn, lead_id=self.lead_id, source_outreach_id=self.outreach_id,
            accepted_attempt_number=1, sent_at=self.sent_at,
        )
        due = self.sent_at + timedelta(days=4, seconds=1)
        claim_due_follow_ups(self.conn, limit=1, now=due)
        recovered = release_stale_claims(self.conn, now=due + timedelta(hours=2), age_minutes=60)
        self.assertEqual(recovered, 1)
        row = self.conn.execute("SELECT status,claimed_at FROM follow_up_schedule").fetchone()
        self.assertEqual(row["status"], SCHEDULED)
        self.assertIsNone(row["claimed_at"])

    def test_stale_prepared_message_is_rescheduled_for_same_outreach_row(self):
        schedule_next_follow_up(
            self.conn, lead_id=self.lead_id, source_outreach_id=self.outreach_id,
            accepted_attempt_number=1, sent_at=self.sent_at,
        )
        due = self.sent_at + timedelta(days=4, seconds=1)
        schedule = claim_due_follow_ups(self.conn, limit=1, now=due)[0]
        extra_id = self.conn.execute(
            """INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status)
               VALUES(?,?,?,?,2,'PREPARED')""",
            (self.lead_id, "hello@training.example", "Follow-up", "Same prepared email"),
        ).lastrowid
        self.conn.commit()
        self.assertTrue(mark_follow_up_prepared(self.conn, schedule["id"], extra_id))
        recovered = release_stale_claims(self.conn, now=due + timedelta(hours=2), age_minutes=60)
        self.assertEqual(recovered, 1)
        row = self.conn.execute("SELECT status,prepared_outreach_id FROM follow_up_schedule").fetchone()
        self.assertEqual(row["status"], SCHEDULED)
        self.assertEqual(row["prepared_outreach_id"], extra_id)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM outreach WHERE lead_id=? AND attempt_number=2", (self.lead_id,)).fetchone()[0],
            1,
        )

    def test_stale_sending_message_is_not_resent_automatically(self):
        schedule_next_follow_up(
            self.conn, lead_id=self.lead_id, source_outreach_id=self.outreach_id,
            accepted_attempt_number=1, sent_at=self.sent_at,
        )
        due = self.sent_at + timedelta(days=4, seconds=1)
        schedule = claim_due_follow_ups(self.conn, limit=1, now=due)[0]
        extra_id = self.conn.execute(
            """INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status)
               VALUES(?,?,?,?,2,'SENDING')""",
            (self.lead_id, "hello@training.example", "Follow-up", "Outcome unknown"),
        ).lastrowid
        self.conn.commit()
        self.assertTrue(mark_follow_up_prepared(self.conn, schedule["id"], extra_id))
        recovered = release_stale_claims(self.conn, now=due + timedelta(hours=2), age_minutes=60)
        self.assertEqual(recovered, 1)
        row = self.conn.execute("SELECT status,cancellation_reason FROM follow_up_schedule").fetchone()
        self.assertEqual(row["status"], FAILED)
        self.assertIn("ambiguous_provider_state_after_crash", row["cancellation_reason"])

    def test_rejects_unbounded_claim_request(self):
        with self.assertRaises(ValueError):
            claim_due_follow_ups(self.conn, limit=100)


if __name__ == "__main__":
    unittest.main()
