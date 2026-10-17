import sqlite3
import unittest
from unittest.mock import patch

from app.database import SCHEMA
from app.models import LeadState
from app.outreach import (
    FAILED_STATUS,
    PREPARED_STATUS,
    SENT_STATUS,
    campaign_capacity,
    mark_failed,
    mark_sent,
    prepare_first_contact,
    prepare_follow_up,
    validate_message,
    Message,
)
from app.replies import (
    ReplyIntent,
    classify_reply,
    extract_budget_signal,
)


TEST_EMAIL = "contact@coachinginstitute.in"


class TestOutreach(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def add_lead(
        self,
        *,
        email=TEST_EMAIL,
        state="QUALIFIED",
        business_name="Example Coaching Institute",
    ):
        cursor = self.conn.execute(
            """
            INSERT INTO leads (
                place_id,
                business_name,
                email,
                state,
                qualification,
                score,
                signal
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"place-{business_name}-{email}",
                business_name,
                email,
                state,
                "HIGH",
                100,
                "coaching",
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    # ---------------------------------------------------------------
    # MESSAGE VALIDATION
    # ---------------------------------------------------------------

    def test_valid_message_passes(self):
        valid, reason = validate_message(
            Message(
                email=TEST_EMAIL,
                subject="Quick question",
                body="How do you currently handle enquiries?",
            )
        )

        self.assertTrue(valid)
        self.assertEqual(reason, "")

    def test_buy_link_is_rejected(self):
        valid, reason = validate_message(
            Message(
                email=TEST_EMAIL,
                subject="Question",
                body="Please visit [BUY_LINK]",
            )
        )

        self.assertFalse(valid)
        self.assertEqual(reason, "unresolved_buy_link")

    def test_html_is_rejected(self):
        valid, reason = validate_message(
            Message(
                email=TEST_EMAIL,
                subject="Question",
                body="<a href='https://example.com'>Click</a>",
            )
        )

        self.assertFalse(valid)
        self.assertEqual(reason, "html_not_allowed")

    # ---------------------------------------------------------------
    # FIRST CONTACT
    # ---------------------------------------------------------------

    def test_first_contact_is_prepared_not_sent(self):
        lead_id = self.add_lead()

        result = prepare_first_contact(
            self.conn,
            lead_id,
        )

        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "prepared")
        self.assertEqual(result.attempt_number, 1)
        self.assertIsNotNone(result.outreach_id)

        row = self.conn.execute(
            """
            SELECT status, attempt_number
            FROM outreach
            WHERE id = ?
            """,
            (result.outreach_id,),
        ).fetchone()

        self.assertEqual(row["status"], PREPARED_STATUS)
        self.assertEqual(row["attempt_number"], 1)

        lead = self.conn.execute(
            "SELECT state FROM leads WHERE id = ?",
            (lead_id,),
        ).fetchone()

        self.assertEqual(
            lead["state"],
            LeadState.QUALIFIED.value,
        )

    def test_first_contact_contains_no_price(self):
        lead_id = self.add_lead()

        result = prepare_first_contact(
            self.conn,
            lead_id,
        )

        self.assertIsNotNone(result.message)
        self.assertNotIn("₹490", result.message.body)
        self.assertNotIn("₹790", result.message.body)
        self.assertNotIn("discount", result.message.body.lower())

    def test_first_contact_cannot_be_prepared_twice(self):
        lead_id = self.add_lead()

        first = prepare_first_contact(
            self.conn,
            lead_id,
        )

        second = prepare_first_contact(
            self.conn,
            lead_id,
        )

        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual(
            second.reason,
            "outreach_already_exists",
        )

    # ---------------------------------------------------------------
    # SUPPRESSION
    # ---------------------------------------------------------------

    def test_suppressed_email_cannot_be_contacted(self):
        lead_id = self.add_lead()

        self.conn.execute(
            """
            INSERT INTO suppressions (
                email,
                reason,
                source
            )
            VALUES (?, ?, ?)
            """,
            (
                TEST_EMAIL,
                "unsubscribe",
                "reply",
            ),
        )
        self.conn.commit()

        result = prepare_first_contact(
            self.conn,
            lead_id,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(
            result.reason,
            "email_suppressed",
        )

    # ---------------------------------------------------------------
    # STATE TRANSITION
    # ---------------------------------------------------------------

    def test_mark_sent_moves_qualified_to_contacted(self):
        lead_id = self.add_lead()

        prepared = prepare_first_contact(
            self.conn,
            lead_id,
        )

        mark_sent(
            self.conn,
            prepared.outreach_id,
        )

        outreach = self.conn.execute(
            """
            SELECT status, sent_at
            FROM outreach
            WHERE id = ?
            """,
            (prepared.outreach_id,),
        ).fetchone()

        self.assertEqual(
            outreach["status"],
            SENT_STATUS,
        )
        self.assertIsNotNone(
            outreach["sent_at"],
        )

        lead = self.conn.execute(
            "SELECT state FROM leads WHERE id = ?",
            (lead_id,),
        ).fetchone()

        self.assertEqual(
            lead["state"],
            LeadState.CONTACTED.value,
        )

    def test_mark_sent_schedules_first_follow_up(self):
        lead_id = self.add_lead()
        prepared = prepare_first_contact(self.conn, lead_id)
        self.assertTrue(mark_sent(self.conn, prepared.outreach_id))
        schedule = self.conn.execute(
            "SELECT lead_id,source_outreach_id,attempt_number,status FROM follow_up_schedule"
        ).fetchone()
        self.assertEqual(schedule["lead_id"], lead_id)
        self.assertEqual(schedule["source_outreach_id"], prepared.outreach_id)
        self.assertEqual(schedule["attempt_number"], 2)
        self.assertEqual(schedule["status"], "SCHEDULED")

    def test_mark_sent_never_regresses_replied_lead_or_schedules_reminder(self):
        lead_id = self.add_lead()
        prepared = prepare_first_contact(self.conn, lead_id)
        self.conn.execute(
            "UPDATE leads SET state=? WHERE id=?",
            (LeadState.REPLIED.value, lead_id),
        )
        self.conn.commit()
        self.assertTrue(mark_sent(self.conn, prepared.outreach_id))
        state = self.conn.execute("SELECT state FROM leads WHERE id=?", (lead_id,)).fetchone()[0]
        self.assertEqual(state, LeadState.REPLIED.value)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM follow_up_schedule").fetchone()[0], 0)

    def test_failed_outreach_does_not_contact_lead(self):
        lead_id = self.add_lead()

        prepared = prepare_first_contact(
            self.conn,
            lead_id,
        )

        mark_failed(
            self.conn,
            prepared.outreach_id,
        )

        outreach = self.conn.execute(
            """
            SELECT status
            FROM outreach
            WHERE id = ?
            """,
            (prepared.outreach_id,),
        ).fetchone()

        self.assertEqual(
            outreach["status"],
            FAILED_STATUS,
        )

        lead = self.conn.execute(
            "SELECT state FROM leads WHERE id = ?",
            (lead_id,),
        ).fetchone()

        self.assertEqual(
            lead["state"],
            LeadState.QUALIFIED.value,
        )

    # ---------------------------------------------------------------
    # FOLLOW-UP LIMIT
    # ---------------------------------------------------------------

    def test_follow_up_requires_first_contact(self):
        lead_id = self.add_lead(
            state=LeadState.CONTACTED.value,
        )

        result = prepare_follow_up(
            self.conn,
            lead_id,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(
            result.reason,
            "first_contact_required",
        )

    def test_two_follow_ups_are_allowed(self):
        lead_id = self.add_lead()

        first = prepare_first_contact(
            self.conn,
            lead_id,
        )

        self.assertTrue(first.allowed)

        # Emulate a previously sent first contact.
        self.conn.execute(
            """
            UPDATE outreach
            SET sent_at = datetime('now', '-2 hours'),
                status = ?
            WHERE id = ?
            """,
            (SENT_STATUS, first.outreach_id),
        )

        self.conn.execute(
            """
            UPDATE leads
            SET state = ?
            WHERE id = ?
            """,
            (LeadState.CONTACTED.value, lead_id),
        )

        self.conn.commit()

        with patch(
            "app.outreach._has_recent_domain_contact",
            return_value=False,
        ):
            second = prepare_follow_up(
                self.conn,
                lead_id,
            )

            self.assertTrue(second.allowed)
            self.assertEqual(
                second.attempt_number,
                2,
            )

            self.conn.execute(
                """
                UPDATE outreach
                SET sent_at = datetime('now', '-2 hours'),
                    status = ?
                WHERE id = ?
                """,
                (SENT_STATUS, second.outreach_id),
            )

            self.conn.commit()

            third = prepare_follow_up(
                self.conn,
                lead_id,
            )

            self.assertTrue(third.allowed)
            self.assertEqual(
                third.attempt_number,
                3,
            )

            self.conn.execute(
                """
                UPDATE outreach
                SET sent_at = datetime('now', '-2 hours'),
                    status = ?
                WHERE id = ?
                """,
                (SENT_STATUS, third.outreach_id),
            )

            self.conn.commit()

            fourth = prepare_follow_up(
                self.conn,
                lead_id,
            )

        self.assertFalse(fourth.allowed)
        self.assertEqual(
            fourth.reason,
            "follow_up_limit_reached",
        )

    # ---------------------------------------------------------------
    # REPLY CLASSIFICATION
    # ---------------------------------------------------------------

    def test_unsubscribe_has_priority(self):
        result = classify_reply(
            "Re: Question",
            "I am interested, but please unsubscribe me.",
        )

        self.assertEqual(
            result,
            ReplyIntent.UNSUBSCRIBE,
        )

    def test_not_interested_is_detected(self):
        result = classify_reply(
            "Re: Question",
            "No thanks, not interested.",
        )

        self.assertEqual(
            result,
            ReplyIntent.NOT_INTERESTED,
        )

    def test_interest_is_detected(self):
        result = classify_reply(
            "Re: Question",
            "Yes, please tell me more.",
        )

        self.assertEqual(
            result,
            ReplyIntent.INTERESTED,
        )

    def test_budget_word_alone_is_not_disclosure(self):
        signal = extract_budget_signal(
            "Re: Question",
            "We review our budget next month.",
        )

        self.assertFalse(signal.disclosed)

    def test_explicit_rupee_amount_is_budget_signal(self):
        signal = extract_budget_signal(
            "Re: Question",
            "Our budget is ₹5000.",
        )

        self.assertTrue(signal.disclosed)
        self.assertIsNotNone(signal.value)

        result = classify_reply(
            "Re: Question",
            "Our budget is ₹5000.",
        )

        self.assertEqual(
            result,
            ReplyIntent.BUDGET_DISCLOSED,
        )

    def test_campaign_capacity_is_positive(self):
        self.assertGreaterEqual(
            campaign_capacity(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
