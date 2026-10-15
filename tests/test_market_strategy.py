import unittest
from app.market_strategy import get_market, list_markets, market_validation_complete, VALIDATION_CHECK_KEYS


class TestMarketStrategy(unittest.TestCase):
    def test_country_lookup_is_case_insensitive(self):
        self.assertEqual(get_market("UGANDA").country, "Uganda")

    def test_india_is_available_as_domestic_benchmark(self):
        profile = get_market("india")
        self.assertEqual(profile.country, "India")
        self.assertEqual(profile.priority, "benchmark")
        self.assertIn("competition", profile.initial_hypothesis.lower())
        self.assertIn(profile, list_markets())

    def test_cote_divoire_alias(self):
        self.assertEqual(get_market("Côte d’Ivoire").country, "Côte d'Ivoire")
        self.assertEqual(get_market("Ivory Coast").country, "Côte d'Ivoire")

    def test_profiles_require_payment_language_channel_and_competition_checks(self):
        profile = get_market("Ethiopia")
        joined = " ".join(profile.validation_checks).lower()
        for term in ("pay", "language", "google maps", "competitors"):
            self.assertIn(term, joined)

    def test_profiles_are_explicitly_hypotheses(self):
        self.assertIn("hypothesis", get_market("Nigeria").initial_hypothesis.lower())

    def test_market_list_is_not_empty_and_unique(self):
        profiles = list_markets()
        self.assertGreaterEqual(len(profiles), 20)
        self.assertEqual(len({p.country for p in profiles}), len(profiles))

    def test_market_validation_requires_every_check_and_evidence(self):
        checks = {key: True for key in VALIDATION_CHECK_KEYS}
        self.assertTrue(market_validation_complete(checks, "2026-10-15: payment, competitors, buyers, compliance, margin"))
        self.assertFalse(market_validation_complete({**checks, "payment_checked": False}, "2026-10-15: evidence noted"))
        self.assertFalse(market_validation_complete(checks, "short note"))

    def test_unknown_market_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, "not in configured market hypotheses"):
            get_market("Atlantis")


if __name__ == "__main__":
    unittest.main()
