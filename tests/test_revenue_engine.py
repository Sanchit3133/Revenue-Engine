import unittest

from app.consultation import ConsultationStage
from app.revenue_engine import (
    LeadProfile,
    RevenueEngine,
    RevenueEngineState,
    create_engine,
)


class TestRevenueEngine(unittest.TestCase):

    def make_lead(self, **overrides):
        data = {
            "name": "Example Coaching Institute",
            "email": "owner@example.com",
            "website": "https://example.com",
            "category": "coaching",
            "description": "The website has an outdated enquiry flow.",
            "signals": [
                "website is outdated",
                "students make enquiries",
            ],
            "city": "Nagpur",
        }

        data.update(overrides)
        return LeadProfile(**data)

    # ---------------------------------------------------------------
    # LEAD PROFILE
    # ---------------------------------------------------------------

    def test_lead_profile_builds_evidence_text(self):
        lead = self.make_lead()

        evidence = lead.evidence_text()

        self.assertIn("outdated enquiry flow", evidence)
        self.assertIn("website is outdated", evidence)
        self.assertIn("students make enquiries", evidence)

    def test_empty_evidence_is_allowed(self):
        lead = self.make_lead(
            description="",
            signals=[],
        )

        self.assertEqual(lead.evidence_text(), "")

    # ---------------------------------------------------------------
    # ENGINE CREATION
    # ---------------------------------------------------------------

    def test_create_engine_starts_at_first_contact(self):
        engine = create_engine(self.make_lead())

        self.assertEqual(
            engine.state.stage,
            ConsultationStage.FIRST_CONTACT,
        )

    def test_engine_can_start_with_custom_stage(self):
        engine = create_engine(
            self.make_lead(),
            stage=ConsultationStage.CONSULTATION,
        )

        self.assertEqual(
            engine.state.stage,
            ConsultationStage.CONSULTATION,
        )

    # ---------------------------------------------------------------
    # DIAGNOSIS
    # ---------------------------------------------------------------

    def test_engine_can_analyze_lead(self):
        engine = create_engine(self.make_lead())

        diagnoses = engine.analyze_lead()

        self.assertTrue(diagnoses)

    def test_engine_returns_best_diagnosis(self):
        engine = create_engine(self.make_lead())

        diagnosis = engine.best_lead_diagnosis()

        self.assertIsNotNone(diagnosis)

    def test_engine_does_not_invent_diagnosis_without_evidence(self):
        lead = self.make_lead(
            description="",
            signals=[],
        )

        engine = create_engine(lead)

        self.assertEqual(engine.analyze_lead(), [])
        self.assertIsNone(engine.best_lead_diagnosis())

    # ---------------------------------------------------------------
    # FIRST CONTACT
    # ---------------------------------------------------------------

    def test_first_contact_is_consultation_first(self):
        engine = create_engine(self.make_lead())

        recommendation = engine.first_contact()

        self.assertEqual(
            recommendation.stage,
            ConsultationStage.FIRST_CONTACT,
        )

        self.assertEqual(
            recommendation.action,
            "CONTACT",
        )

        self.assertTrue(recommendation.message)

        self.assertNotIn("₹490", recommendation.message)
        self.assertNotIn("₹790", recommendation.message)
        self.assertNotIn("discount", recommendation.message.lower())

    def test_first_contact_contains_no_proposal(self):
        engine = create_engine(self.make_lead())

        recommendation = engine.first_contact()

        self.assertFalse(
            recommendation.metadata["proposal_included"]
        )

        self.assertFalse(
            recommendation.metadata["price_included"]
        )

    def test_first_contact_without_evidence_does_not_make_claim(self):
        lead = self.make_lead(
            description="",
            signals=[],
        )

        engine = create_engine(lead)

        recommendation = engine.first_contact()

        self.assertTrue(recommendation.message)
        self.assertIsNone(recommendation.diagnosis)

    # ---------------------------------------------------------------
    # REPLY FLOW
    # ---------------------------------------------------------------

    def test_reply_moves_conversation_forward(self):
        engine = create_engine(self.make_lead())

        recommendation = engine.process_reply(
            "Yes, we can discuss this."
        )

        self.assertNotEqual(
            recommendation.stage,
            ConsultationStage.FIRST_CONTACT,
        )

    def test_positive_reply_can_enter_consultation(self):
        engine = create_engine(
            self.make_lead(),
            stage=ConsultationStage.FIRST_CONTACT,
        )

        recommendation = engine.process_reply(
            "Yes, please tell me more."
        )

        self.assertEqual(
            recommendation.stage,
            ConsultationStage.CONSULTATION,
        )

    def test_negative_reply_stops_conversation(self):
        engine = create_engine(
            self.make_lead(),
            stage=ConsultationStage.FIRST_CONTACT,
        )

        recommendation = engine.process_reply(
            "No thanks, not interested."
        )

        self.assertEqual(
            recommendation.action,
            "STOP",
        )

        self.assertTrue(
            recommendation.metadata["conversation_closed"]
        )

    # ---------------------------------------------------------------
    # CONSULTATION
    # ---------------------------------------------------------------

    def test_consultation_questions_are_available(self):
        engine = create_engine(self.make_lead())

        questions = engine.consultation_questions()

        self.assertTrue(questions)
        self.assertIsInstance(questions, list)

    def test_current_diagnosis_is_available(self):
        engine = create_engine(self.make_lead())

        diagnosis = engine.current_diagnosis()

        self.assertIsNotNone(diagnosis)

    def test_current_service_matches_diagnosis(self):
        engine = create_engine(self.make_lead())

        diagnosis = engine.current_diagnosis()
        service = engine.current_service()

        self.assertIsNotNone(diagnosis)
        self.assertIsNotNone(service)

    # ---------------------------------------------------------------
    # FOLLOW-UP LIMIT
    # ---------------------------------------------------------------

    def test_follow_up_limit_is_two(self):
        engine = create_engine(self.make_lead())

        self.assertTrue(engine.can_follow_up())

        self.assertTrue(engine.register_follow_up())
        self.assertEqual(engine.state.follow_up_count, 1)

        self.assertTrue(engine.can_follow_up())

        self.assertTrue(engine.register_follow_up())
        self.assertEqual(engine.state.follow_up_count, 2)

        self.assertFalse(engine.can_follow_up())
        self.assertFalse(engine.register_follow_up())

        self.assertEqual(engine.state.follow_up_count, 2)

    # ---------------------------------------------------------------
    # REQUIREMENTS
    # ---------------------------------------------------------------

    def test_requirements_are_not_complete_initially(self):
        engine = create_engine(self.make_lead())

        self.assertFalse(
            engine.requirements_complete()
        )

    def test_requirements_can_be_recorded(self):
        engine = create_engine(self.make_lead())

        engine.set_requirements(
            {
                "scope": "landing page",
                "deadline": "3 days",
            }
        )

        self.assertTrue(
            engine.requirements_complete()
        )

    def test_empty_requirements_do_not_count(self):
        engine = create_engine(self.make_lead())

        engine.set_requirements({})

        self.assertFalse(
            engine.requirements_complete()
        )

    # ---------------------------------------------------------------
    # BUDGET
    # ---------------------------------------------------------------

    def test_budget_is_not_disclosed_initially(self):
        engine = create_engine(self.make_lead())

        self.assertFalse(
            engine.budget_disclosed()
        )

    def test_budget_can_be_recorded(self):
        engine = create_engine(self.make_lead())

        engine.set_budget("₹2500")

        self.assertTrue(
            engine.budget_disclosed()
        )

        self.assertEqual(
            engine.state.budget,
            "₹2500",
        )

    def test_price_requires_requirements_and_budget(self):
        engine = create_engine(self.make_lead())

        self.assertFalse(
            engine.can_discuss_price()
        )

        engine.set_requirements(
            {
                "scope": "landing page",
            }
        )

        self.assertFalse(
            engine.can_discuss_price()
        )

        engine.set_budget("₹2500")

        self.assertTrue(
            engine.can_discuss_price()
        )

    # ---------------------------------------------------------------
    # PROPOSAL
    # ---------------------------------------------------------------

    def test_proposal_requires_requirements_and_budget(self):
        engine = create_engine(self.make_lead())

        self.assertFalse(
            engine.can_make_proposal()
        )

        engine.set_requirements(
            {
                "scope": "automation",
            }
        )

        self.assertFalse(
            engine.can_make_proposal()
        )

        engine.set_budget("₹5000")

        self.assertTrue(
            engine.can_make_proposal()
        )

    # ---------------------------------------------------------------
    # WORK AUTHORIZATION
    # ---------------------------------------------------------------

    def test_work_requires_written_agreement(self):
        engine = create_engine(self.make_lead())

        engine.set_requirements(
            {
                "scope": "landing page",
            }
        )

        engine.set_budget("₹2500")

        self.assertFalse(
            engine.can_start_work()
        )

        engine.set_written_agreement(True)

        self.assertTrue(
            engine.can_start_work()
        )

    # ---------------------------------------------------------------
    # PROBLEM CONFIRMATION
    # ---------------------------------------------------------------

    def test_confirm_problem_records_prospect_confirmed_problem(self):
        engine = create_engine(self.make_lead())

        engine.confirm_problem(
            "WEAK_CONVERSION_FLOW"
        )

        self.assertEqual(
            engine.state.confirmed_problem,
            "WEAK_CONVERSION_FLOW",
        )

        self.assertEqual(
            engine.state.stage,
            ConsultationStage.PROBLEM_IDENTIFIED,
        )

    # ---------------------------------------------------------------
    # SNAPSHOT
    # ---------------------------------------------------------------

    def test_snapshot_contains_core_state(self):
        engine = create_engine(self.make_lead())

        snapshot = engine.snapshot()

        self.assertEqual(
            snapshot["lead"]["name"],
            "Example Coaching Institute",
        )

        self.assertEqual(
            snapshot["stage"],
            ConsultationStage.FIRST_CONTACT.value,
        )

        self.assertIn(
            "diagnosis",
            snapshot,
        )

        self.assertIn(
            "recommended_service",
            snapshot,
        )

        self.assertIn(
            "can_discuss_price",
            snapshot,
        )

        self.assertIn(
            "can_make_proposal",
            snapshot,
        )

        self.assertIn(
            "can_start_work",
            snapshot,
        )

    def test_snapshot_updates_after_budget_and_requirements(self):
        engine = create_engine(self.make_lead())

        engine.set_requirements(
            {
                "scope": "landing page",
            }
        )

        engine.set_budget("₹2500")

        snapshot = engine.snapshot()

        self.assertTrue(
            snapshot["can_discuss_price"]
        )

        self.assertTrue(
            snapshot["can_make_proposal"]
        )

        self.assertFalse(
            snapshot["can_start_work"]
        )

    # ---------------------------------------------------------------
    # AGREEMENT
    # ---------------------------------------------------------------

    def test_snapshot_allows_work_after_agreement(self):
        engine = create_engine(self.make_lead())

        engine.set_requirements(
            {
                "scope": "landing page",
            }
        )

        engine.set_budget("₹2500")
        engine.set_written_agreement(True)

        snapshot = engine.snapshot()

        self.assertTrue(
            snapshot["can_start_work"]
        )


if __name__ == "__main__":
    unittest.main()