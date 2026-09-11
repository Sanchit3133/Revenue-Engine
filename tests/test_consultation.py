import unittest

from app.consultation import (
    ConsultationStage,
    MAX_FOLLOW_UPS,
    SALES_RULES,
    can_follow_up,
    consultation_questions,
    educational_explanation,
    first_contact_message,
    follow_up_message,
    next_step,
    price_discussion_allowed,
    proposal_allowed,
    summarize_rules,
    work_allowed,
)


class TestConsultationEngine(unittest.TestCase):

    def test_sales_rules_are_enabled(self):
        self.assertTrue(
            SALES_RULES["consultation_first"]
        )

        self.assertTrue(
            SALES_RULES["ask_before_recommending"]
        )

        self.assertTrue(
            SALES_RULES["no_unsolicited_discount"]
        )

        self.assertTrue(
            SALES_RULES["no_work_without_agreement"]
        )

        self.assertEqual(
            SALES_RULES["maximum_follow_ups"],
            2,
        )

    def test_first_contact_is_consultation_first(self):
        message = first_contact_message(
            "Example Coaching Institute"
        )

        text = message.lower()

        self.assertIn(
            "how do you currently",
            text,
        )

        self.assertNotIn(
            "₹490",
            message,
        )

        self.assertNotIn(
            "discount",
            text,
        )

        self.assertNotIn(
            "buy now",
            text,
        )

    def test_first_contact_can_use_observation(self):
        message = first_contact_message(
            "Example Institute",
            "you have an enquiry form on your website",
        )

        self.assertIn(
            "enquiry form",
            message.lower(),
        )

        self.assertIn(
            "how do you currently",
            message.lower(),
        )

    def test_first_contact_does_not_make_unverified_claim(self):
        message = first_contact_message(
            "Example Institute"
        )

        self.assertNotIn(
            "you're losing leads",
            message.lower(),
        )

        self.assertNotIn(
            "your follow-up is poor",
            message.lower(),
        )

        self.assertNotIn(
            "your website is broken",
            message.lower(),
        )

    def test_reply_moves_to_consultation(self):
        result = next_step(
            ConsultationStage.REPLIED
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.CONSULTATION,
        )

        self.assertIsNotNone(
            result.question
        )

        self.assertIsNotNone(
            result.next_stage
        )

    def test_consultation_without_problem_keeps_asking_questions(self):
        result = next_step(
            ConsultationStage.CONSULTATION,
            problem_identified=False,
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.CONSULTATION,
        )

        self.assertIsNotNone(
            result.question
        )

        self.assertNotEqual(
            result.next_stage,
            ConsultationStage.PROPOSAL,
        )

    def test_consultation_with_problem_moves_to_problem_identified(self):
        result = next_step(
            ConsultationStage.CONSULTATION,
            problem_identified=True,
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.PROBLEM_IDENTIFIED,
        )

        self.assertIsNotNone(
            result.question
        )

    def test_problem_must_be_confirmed_before_solution(self):
        result = next_step(
            ConsultationStage.PROBLEM_IDENTIFIED,
            solution_explained=False,
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.PROBLEM_IDENTIFIED,
        )

        self.assertIsNotNone(
            result.question
        )

    def test_solution_stage_asks_permission(self):
        result = next_step(
            ConsultationStage.SOLUTION_EXPLAINED
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.SOLUTION_EXPLAINED,
        )

        self.assertIsNotNone(
            result.question
        )

        self.assertIn(
            "would",
            result.question.lower(),
        )

    def test_implementation_interest_leads_to_requirements(self):
        result = next_step(
            ConsultationStage.IMPLEMENTATION_INTEREST,
            implementation_interest=True,
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.REQUIREMENTS,
        )

        self.assertIsNotNone(
            result.question
        )

    def test_requirements_are_needed_before_budget_stage(self):
        result = next_step(
            ConsultationStage.REQUIREMENTS,
            requirements_collected=False,
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.REQUIREMENTS,
        )

        self.assertNotEqual(
            result.next_stage,
            ConsultationStage.PROPOSAL,
        )

    def test_requirements_collected_moves_to_budget_discussion(self):
        result = next_step(
            ConsultationStage.REQUIREMENTS,
            requirements_collected=True,
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.BUDGET_DISCLOSED,
        )

        self.assertIsNotNone(
            result.question
        )

    def test_budget_is_required_before_price_discussion(self):
        self.assertFalse(
            price_discussion_allowed(
                budget_disclosed=False,
                requirements_collected=True,
            )
        )

        self.assertFalse(
            price_discussion_allowed(
                budget_disclosed=True,
                requirements_collected=False,
            )
        )

        self.assertTrue(
            price_discussion_allowed(
                budget_disclosed=True,
                requirements_collected=True,
            )
        )

    def test_budget_is_required_before_proposal(self):
        self.assertFalse(
            proposal_allowed(
                budget_disclosed=False,
                requirements_collected=True,
            )
        )

        self.assertFalse(
            proposal_allowed(
                budget_disclosed=True,
                requirements_collected=False,
            )
        )

        self.assertTrue(
            proposal_allowed(
                budget_disclosed=True,
                requirements_collected=True,
            )
        )

    def test_proposal_stage_cannot_skip_budget(self):
        result = next_step(
            ConsultationStage.PROPOSAL,
            budget_disclosed=False,
            requirements_collected=True,
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.BUDGET_DISCLOSED,
        )

    def test_work_requires_written_agreement(self):
        self.assertFalse(
            work_allowed(
                written_agreement=False
            )
        )

        self.assertTrue(
            work_allowed(
                written_agreement=True
            )
        )

    def test_follow_up_limit_is_two(self):
        self.assertEqual(
            MAX_FOLLOW_UPS,
            2,
        )

        self.assertTrue(
            can_follow_up(0)
        )

        self.assertTrue(
            can_follow_up(1)
        )

        self.assertFalse(
            can_follow_up(2)
        )

        self.assertFalse(
            can_follow_up(3)
        )

    def test_follow_up_message_exists_for_first_follow_up(self):
        message = follow_up_message(
            "Example Institute",
            0,
        )

        self.assertIsNotNone(
            message
        )

        self.assertIn(
            "following up",
            message.lower(),
        )

    def test_follow_up_message_exists_for_second_follow_up(self):
        message = follow_up_message(
            "Example Institute",
            1,
        )

        self.assertIsNotNone(
            message
        )

        self.assertIn(
            "final follow-up",
            message.lower(),
        )

    def test_no_third_follow_up_is_generated(self):
        message = follow_up_message(
            "Example Institute",
            2,
        )

        self.assertIsNone(
            message
        )

    def test_negative_reply_can_end_sales_conversation(self):
        result = next_step(
            ConsultationStage.CONSULTATION,
            reply="Not interested, thank you.",
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.NOT_INTERESTED,
        )

    def test_unsubscribe_reply_suppresses_contact(self):
        result = next_step(
            ConsultationStage.CONSULTATION,
            reply="Please unsubscribe me.",
        )

        self.assertEqual(
            result.stage,
            ConsultationStage.UNSUBSCRIBED,
        )

    def test_educational_explanation_is_available(self):
        explanation = educational_explanation(
            service_key="FOLLOW_UP_SYSTEM"
        )

        self.assertTrue(
            explanation
        )

        self.assertGreater(
            len(explanation),
            50,
        )

        self.assertNotIn(
            "buy now",
            explanation.lower(),
        )

    def test_consultation_questions_are_available(self):
        questions = consultation_questions(
            "FOLLOW_UP_SYSTEM"
        )

        self.assertIsInstance(
            questions,
            list,
        )

        self.assertGreater(
            len(questions),
            0,
        )

    def test_rules_can_be_summarized(self):
        rules = summarize_rules()

        self.assertIsInstance(
            rules,
            dict,
        )

        self.assertEqual(
            rules["maximum_follow_ups"],
            2,
        )


if __name__ == "__main__":
    unittest.main()