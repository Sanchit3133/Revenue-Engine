"""Regression tests for the automated acquisition cycle's live safety gate."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_automated_cycle as worker


class FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, market_status):
        self.market_status = market_status
        self.executed = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT status FROM market_validations" in sql:
            row = {"status": self.market_status} if self.market_status else None
            return FakeResult(row)
        if "SELECT COUNT(*) FROM follow_up_schedule" in sql:
            return FakeResult([0])
        return FakeResult()

    def commit(self):
        self.committed = True


def configured_settings():
    return SimpleNamespace(
        database_backend="postgresql",
        email_provider="brevo",
        brevo_api_key="test-key",
        brevo_sender="owner@example.com",
        public_app_url="https://revenue-engine.example",
        market_validation_required=True,
        campaign_limit=5,
        daily_limit=10,
        domain_cooldown=3600,
        max_consecutive_failures=3,
        min_send_delay=0,
        max_send_delay=0,
        target_market_country="India",
    )


class TestAutomatedCycleMarketGate(unittest.TestCase):
    def test_unvalidated_market_blocks_before_discovery_or_lead_writes(self):
        conn = FakeConnection("IN_REVIEW")

        with (
            patch.dict(os.environ, {"REVENUE_ENGINE_AUTOMATION_ENABLED": "true"}),
            patch.object(worker, "settings", configured_settings()),
            patch.object(worker, "connect", return_value=conn),
            patch.object(worker, "discover_and_persist") as discovery,
        ):
            result = worker.run_cycle(country="India", max_new=5, live=True)

        discovery.assert_not_called()
        self.assertEqual(result["live_send_block"], "market_validation_not_complete")
        self.assertEqual(result["saved"], 0)
        self.assertEqual(result["prepared"], 0)
        self.assertEqual(result["accepted_by_provider"], 0)
        self.assertFalse(
            any("INSERT INTO leads" in sql.upper() for sql, _ in conn.executed),
            "An unvalidated market must not trigger lead persistence.",
        )
        self.assertTrue(conn.committed, "The aggregate blocked-run status should be recorded.")

    def test_validated_market_allows_discovery_to_run(self):
        conn = FakeConnection("VALIDATED")
        discovery_result = {
            "country": "India",
            "cities_attempted": 1,
            "city_data_failures": 0,
            "city": "Nagpur",
            "segment": "language_training",
            "places_seen": 10,
            "site_checks": 2,
            "published_emails_found": 1,
            "qualified": 1,
            "saved": 1,
        }

        with (
            patch.dict(os.environ, {"REVENUE_ENGINE_AUTOMATION_ENABLED": "true"}),
            patch.object(worker, "settings", configured_settings()),
            patch.object(worker, "connect", return_value=conn),
            patch.object(worker, "discover_and_persist", return_value=discovery_result) as discovery,
            patch.object(worker, "_process_due_reminders", return_value=(0, 0, 0, False)),
            patch.object(worker, "_prepare_and_send_first_contacts", return_value=(0, 0, 0, 0, False)),
        ):
            result = worker.run_cycle(country="India", max_new=5, live=True)

        discovery.assert_called_once_with(conn, country="India", max_new=5, persist=True)
        self.assertEqual(result["live_send_block"], "")
        self.assertEqual(result["saved"], 1)


if __name__ == "__main__":
    unittest.main()
