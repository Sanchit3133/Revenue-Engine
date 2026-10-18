import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    import flask  # noqa: F401
    from app.web import create_app
    FLASK_AVAILABLE = True
except ModuleNotFoundError:
    create_app = None
    FLASK_AVAILABLE = False

import app.database as database


def _csrf(response):
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    if not match:
        raise AssertionError("Expected CSRF token in form")
    return match.group(1).decode()


@unittest.skipUnless(FLASK_AVAILABLE, "Install application requirements to run Flask route tests")
class TestMarketValidationRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "market.db"
        self.db_patch = patch.object(database, "settings", SimpleNamespace(db_path=db_path))
        self.db_patch.start()
        self.app_settings = SimpleNamespace(
            environment="development",
            flask_secret_key="test-secret",
            owner_dashboard_password="test-owner-password",
            owner_notification_email=None,
            public_app_url="https://test.example",
            email_provider="brevo",
            brevo_api_key="test-key",
            brevo_sender="test@example.com",
            brevo_reply_to=None,
            brevo_webhook_token="test-token",
            sendgrid_api_key=None,
            sendgrid_sender=None,
            sendgrid_reply_to=None,
            hf_token=None,
            hf_api_key=None,
            hf_api_url="https://router.huggingface.co/v1/chat/completions",
            hf_model="test-model",
            google_api_key=None,
            target_market_country="India",
            target_market_language="en",
            market_validation_required=True,
            campaign_limit=5,
            daily_limit=10,
            min_send_delay=0,
            max_send_delay=0,
            domain_cooldown=3600,
            max_consecutive_failures=3,
        )
        self.admin_settings_patch = patch("app.web.admin.settings", self.app_settings)
        self.admin_settings_patch.start()
        database.init_db()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()
        login = self.client.get("/owner/login")
        self.client.post("/owner/login", data={"csrf_token": _csrf(login), "password": "test-owner-password"})

    def tearDown(self):
        self.admin_settings_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_owner_dashboard_renders_successfully(self):
        response = self.client.get("/owner")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Market review", response.data)

    def test_market_validation_requires_all_checks_and_evidence(self):
        page = self.client.get("/owner")
        csrf = _csrf(page)
        response = self.client.post("/owner/market-validation", data={
            "csrf_token": csrf,
            "country": "Uganda",
            "payment_checked": "yes",
            "language_checked": "yes",
            "reachability_checked": "yes",
            "competition_checked": "yes",
            "demand_checked": "yes",
            "compliance_checked": "yes",
            "unit_economics_checked": "yes",
            "evidence_notes": "2026-10-15: payment, language, ten competitors, three interviews, compliance and margin reviewed.",
        })
        self.assertEqual(response.status_code, 302)
        with database.connect() as conn:
            row = conn.execute("SELECT status FROM market_validations WHERE country='Uganda'").fetchone()
        self.assertEqual(row["status"], "VALIDATED")

    def test_incomplete_market_validation_stays_research_required(self):
        page = self.client.get("/owner")
        response = self.client.post("/owner/market-validation", data={
            "csrf_token": _csrf(page),
            "country": "India",
            "payment_checked": "yes",
            "evidence_notes": "Only payment has been checked; other validation steps are incomplete.",
        })
        self.assertEqual(response.status_code, 302)
        with database.connect() as conn:
            row = conn.execute("SELECT status FROM market_validations WHERE country='India'").fetchone()
        self.assertEqual(row["status"], "RESEARCH_REQUIRED")

    def test_outreach_send_is_blocked_for_unvalidated_country(self):
        with database.connect() as conn:
            conn.execute("INSERT INTO leads(place_id,business_name,email,country,state) VALUES(?,?,?,?,?)",
                         ("uganda-test", "Example Training", "owner@example.ug", "Uganda", "QUALIFIED"))
            lead_id = conn.execute("SELECT id FROM leads WHERE place_id='uganda-test'").fetchone()["id"]
            conn.execute("INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status) VALUES(?,?,?,?,?,?)",
                         (lead_id, "owner@example.ug", "Test subject", "Test outreach body", 1, "PREPARED"))
            conn.commit()
        page = self.client.get("/owner")
        response = self.client.post("/owner/outreach/send-one", data={"csrf_token": _csrf(page), "confirm_send": "yes"})
        self.assertEqual(response.status_code, 302)
        with database.connect() as conn:
            status = conn.execute("SELECT status FROM outreach WHERE lead_id=?", (lead_id,)).fetchone()["status"]
        self.assertEqual(status, "PREPARED")


if __name__ == "__main__":
    unittest.main()
