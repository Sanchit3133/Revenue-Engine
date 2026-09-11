import unittest

from app.diagnosis import (
    best_diagnosis,
    diagnose_business,
    recommendation_for_diagnosis,
)


class TestDiagnosisEngine(unittest.TestCase):

    def test_no_evidence_returns_no_diagnosis(self):
        result = diagnose_business()

        self.assertEqual(result, [])

    def test_unstructured_leads_can_be_detected(self):
        result = diagnose_business(
            notes="The business has no structured enquiry form."
        )

        self.assertTrue(result)

        diagnosis = result[0]

        self.assertEqual(
            diagnosis.problem_key,
            "UNSTRUCTURED_LEADS",
        )

        self.assertEqual(
            diagnosis.service_key,
            "LEAD_CAPTURE",
        )

    def test_follow_up_gap_can_be_detected(self):
        result = diagnose_business(
            notes=(
                "The staff manually remember follow-ups and "
                "there is no follow-up schedule."
            )
        )

        self.assertTrue(result)

        diagnosis = result[0]

        self.assertEqual(
            diagnosis.problem_key,
            "FOLLOW_UP_GAP",
        )

        self.assertEqual(
            diagnosis.service_key,
            "FOLLOW_UP_SYSTEM",
        )

        self.assertEqual(
            diagnosis.confidence,
            "medium",
        )

    def test_payment_problem_can_be_detected(self):
        result = diagnose_business(
            notes=(
                "Customers currently use manual payment collection "
                "and there are unclear payment instructions."
            )
        )

        self.assertTrue(result)

        diagnosis = result[0]

        self.assertEqual(
            diagnosis.problem_key,
            "PAYMENT_FRICTION",
        )

        self.assertEqual(
            diagnosis.service_key,
            "PAYMENT_PAGE",
        )

    def test_website_problem_can_be_detected(self):
        result = diagnose_business(
            notes=(
                "The website is outdated and has no clear "
                "call to action."
            )
        )

        self.assertTrue(result)

        diagnosis = result[0]

        self.assertEqual(
            diagnosis.problem_key,
            "WEAK_CONVERSION_FLOW",
        )

        self.assertEqual(
            diagnosis.service_key,
            "LANDING_PAGE",
        )

        self.assertEqual(
            diagnosis.confidence,
            "medium",
        )

    def test_multiple_signals_increase_confidence(self):
        result = diagnose_business(
            notes=(
                "The website is outdated, has no clear call to action, "
                "and the main course has no dedicated page."
            )
        )

        self.assertTrue(result)

        diagnosis = result[0]

        self.assertEqual(
            diagnosis.problem_key,
            "WEAK_CONVERSION_FLOW",
        )

        self.assertEqual(
            diagnosis.confidence,
            "high",
        )

    def test_diagnosis_contains_questions(self):
        result = diagnose_business(
            notes="There is no structured enquiry form."
        )

        self.assertTrue(result)

        diagnosis = result[0]

        self.assertGreater(
            len(diagnosis.questions),
            0,
        )

        self.assertTrue(
            any(
                "enquiry" in question.lower()
                for question in diagnosis.questions
            )
        )

    def test_best_diagnosis_returns_none_without_evidence(self):
        result = best_diagnosis(
            business={
                "name": "Example Business",
            }
        )

        self.assertIsNone(result)

    def test_best_diagnosis_returns_strongest_match(self):
        result = best_diagnosis(
            notes=(
                "The website is outdated, has no clear call to action, "
                "and the main course has no dedicated page."
            )
        )

        self.assertIsNotNone(result)

        self.assertEqual(
            result.problem_key,
            "WEAK_CONVERSION_FLOW",
        )

    def test_recommendation_is_consultation_safe(self):
        diagnosis = best_diagnosis(
            notes="There is no structured enquiry form."
        )

        recommendation = recommendation_for_diagnosis(
            diagnosis
        )

        self.assertIsNotNone(recommendation)

        self.assertEqual(
            recommendation["service_key"],
            "LEAD_CAPTURE",
        )

        self.assertIn(
            "questions",
            recommendation,
        )

        # The diagnosis engine must not automatically create
        # a sales price or proposal.
        self.assertNotIn(
            "price",
            recommendation,
        )

        self.assertNotIn(
            "proposal",
            recommendation,
        )

    def test_recommendation_for_no_diagnosis_is_none(self):
        recommendation = recommendation_for_diagnosis(
            None
        )

        self.assertIsNone(recommendation)


if __name__ == "__main__":
    unittest.main()