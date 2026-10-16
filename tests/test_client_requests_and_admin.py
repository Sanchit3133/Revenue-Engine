import hmac
import hashlib
import json
import re
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
        raise AssertionError("Expected CSRF token in rendered form")
    return match.group(1).decode()


@unittest.skipUnless(FLASK_AVAILABLE, "Install application requirements to run Flask route tests")
class TestClientRequestWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "requests.db"
        self.db_patch = patch.object(database, "settings", SimpleNamespace(db_path=self.db_path))
        self.db_patch.start()
        self.app_settings = SimpleNamespace(
            owner_dashboard_password="test-owner-password",
            owner_notification_email="owner@example.com",
            public_app_url="https://revenue.example.com",
            email_provider="brevo",
            brevo_api_key=None,
            brevo_sender=None,
            google_api_key=None,
            hf_token=None,
            hf_api_key=None,
            sendgrid_api_key=None,
            sendgrid_sender=None,
            daily_limit=10,
            domain_cooldown=3600,
        )
        self.client_settings_patch = patch("app.web.client_requests.settings", self.app_settings)
        self.client_settings_patch.start()
        self.admin_settings_patch = patch("app.web.admin.settings", self.app_settings)
        self.admin_settings_patch.start()
        database.init_db()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()

    def tearDown(self):
        self.admin_settings_patch.stop()
        self.client_settings_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def _consented_consultation(self):
        public_id = "consultation-test-001"
        with database.connect() as conn:
            conn.execute(
                """INSERT INTO consultation_submissions
                   (public_id,business_name,email,stage,consent_at,data_json)
                   VALUES(?,?,?,?,?,?)""",
                (public_id, "Example Coaching", "owner@example.com", "BUDGET_DISCLOSED", "2026-10-15T00:00:00+00:00", "{}"),
            )
            conn.commit()
        consultation_data = {
            "business_name": "Example Coaching",
            "contact_email": "owner@example.com",
            "contact_consent": True,
            "submission_public_id": public_id,
            "business_type": "IELTS coaching",
            "service_key": "LANDING_PAGE",
            "website": "https://example.com",
            "biggest_problem": "Enquiries are lost after first contact",
            "requirements": "A single course page with a clear enquiry next step",
            "requirements_collected": True,
            "budget_disclosed": True,
            "stage": "BUDGET_DISCLOSED",
        }
        with database.connect() as conn:
            conn.execute("UPDATE consultation_submissions SET data_json=? WHERE public_id=?", (json.dumps(consultation_data), public_id))
            conn.commit()
        with self.client.session_transaction() as sess:
            sess["consultation_public_id"] = public_id

    def test_delivery_request_requires_completed_consented_consultation(self):
        page = self.client.get("/client/request")
        token = _csrf(page)
        with patch("app.web.client_requests.generate_landing_page_draft") as generator, patch("app.web.client_requests.notify_owner") as notify:
            response = self.client.post("/client/request", data={
                "csrf_token": token,
                "business_name": "Example Coaching",
                "email": "owner@example.com",
                "request_type": "delivery",
                "message": "Please prepare a page for our next course.",
                "contact_consent": "yes",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"complete the consented Revenue Engine consultation", response.data)
        generator.assert_not_called()
        notify.assert_not_called()

    def test_consenting_customer_request_creates_private_draft_and_notifies_owner(self):
        self._consented_consultation()
        page = self.client.get("/client/request")
        token = _csrf(page)
        generated = ("landing-page-draft.html", "<!doctype html><h1>Draft</h1>", "template_fallback", "HF token missing")
        with patch("app.web.client_requests.generate_landing_page_draft", return_value=generated) as generator, patch(
            "app.web.client_requests.notify_owner", return_value={"success": True, "reason": "accepted"}
        ) as notify:
            response = self.client.post("/client/request", data={
                "csrf_token": token,
                "business_name": "Example Coaching",
                "email": "owner@example.com",
                "request_type": "delivery",
                "message": "Please draft a page for the IELTS course.",
                "contact_consent": "yes",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Request received", response.data)
        generator.assert_called_once()
        notify.assert_called_once()
        self.assertIn("https://revenue.example.com/owner", notify.call_args.args[1])
        with database.connect() as conn:
            row = conn.execute("SELECT status,draft_content,draft_source,notification_status FROM client_requests").fetchone()
        self.assertEqual(row["status"], "PENDING_REVIEW")
        self.assertEqual(row["draft_content"], generated[1])
        self.assertEqual(row["draft_source"], "template_fallback")
        self.assertEqual(row["notification_status"], "sent")

    def test_support_request_must_match_existing_reference_and_email(self):
        with database.connect() as conn:
            conn.execute(
                """INSERT INTO client_requests(public_id,business_name,email,request_type,message,status)
                   VALUES('known-request','Example Coaching','owner@example.com','delivery','Original order request','PENDING_REVIEW')"""
            )
            conn.commit()
        page = self.client.get("/client/request")
        token = _csrf(page)
        with patch("app.web.client_requests.notify_owner") as notify:
            response = self.client.post("/client/request", data={
                "csrf_token": token,
                "business_name": "Example Coaching",
                "email": "other@example.com",
                "request_type": "not_satisfied",
                "related_request_public_id": "known-request",
                "message": "The output did not meet my expectation.",
                "contact_consent": "yes",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"reference was not found for this email", response.data)
        notify.assert_not_called()


@unittest.skipUnless(FLASK_AVAILABLE, "Install application requirements to run Flask route tests")
class TestOwnerApprovalPaymentGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "owner.db"
        self.db_patch = patch.object(database, "settings", SimpleNamespace(db_path=self.db_path))
        self.db_patch.start()
        self.admin_settings = SimpleNamespace(
            owner_dashboard_password="test-owner-password", email_provider="brevo",
            brevo_api_key="fake", brevo_sender="sender@example.com", brevo_reply_to=None,
            google_api_key=None, hf_token=None, hf_api_key=None, owner_notification_email="owner@example.com",
            daily_limit=10, domain_cooldown=3600, sendgrid_api_key=None, sendgrid_sender=None,
        )
        self.admin_patch = patch("app.web.admin.settings", self.admin_settings)
        self.admin_patch.start()
        database.init_db()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()
        with database.connect() as conn:
            conn.execute(
                """INSERT INTO client_requests(public_id,business_name,email,request_type,message,status,
                    draft_filename,draft_content,draft_content_type,draft_source)
                   VALUES('request-001','Example Coaching','owner@example.com','delivery','Build our course page',
                   'PENDING_REVIEW','draft.html','<h1>Draft</h1>','text/html','template_fallback')"""
            )
            conn.commit()

    def tearDown(self):
        self.admin_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def _login_and_dashboard_token(self):
        login_page = self.client.get("/owner/login")
        login_token = _csrf(login_page)
        result = self.client.post("/owner/login", data={"csrf_token": login_token, "password": "test-owner-password"})
        self.assertEqual(result.status_code, 302)
        dashboard = self.client.get("/owner")
        self.assertEqual(dashboard.status_code, 200)
        return _csrf(dashboard)

    def test_arbitrary_payment_reference_cannot_approve_file(self):
        token = self._login_and_dashboard_token()
        with patch("app.web.admin.deliver_approved_file") as sender:
            response = self.client.post("/owner/requests/1", data={
                "csrf_token": token,
                "action": "approve_and_send",
                "scope_confirmed": "yes",
                "payment_reference": "pay-not-in-db",
                "approval_note": "Agreement checked",
                "confirm_approval": "yes",
            })
        self.assertEqual(response.status_code, 302)
        sender.assert_not_called()
        with database.connect() as conn:
            row = conn.execute("SELECT status FROM client_requests WHERE id=1").fetchone()
        self.assertEqual(row["status"], "PENDING_REVIEW")

    def test_captured_payment_payer_email_mismatch_blocks_approval(self):
        token = self._login_and_dashboard_token()
        payload = {
            "id": "evt-wrong-payer", "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": "pay-wrong-payer", "status": "captured", "captured": True,
                "amount": 49000, "currency": "INR", "email": "different@example.com",
            }}}
        }
        with database.connect() as conn:
            conn.execute("INSERT INTO payments(provider,provider_payment_id,amount,currency,status) VALUES('razorpay','pay-wrong-payer',49000,'INR','captured')")
            conn.execute("INSERT INTO webhook_events(provider,idempotency_key,event_type,resource_id,payload_json) VALUES('razorpay','razorpay:evt-wrong-payer','payment.captured','pay-wrong-payer',?)", (json.dumps(payload),))
            conn.commit()
        with patch("app.web.admin.deliver_approved_file") as sender:
            self.client.post("/owner/requests/1", data={
                "csrf_token": token,
                "action": "approve_and_send",
                "scope_confirmed": "yes",
                "payment_reference": "pay-wrong-payer",
                "approval_note": "Agreement checked",
                "confirm_approval": "yes",
            })
        sender.assert_not_called()
        with database.connect() as conn:
            row = conn.execute("SELECT status FROM client_requests WHERE id=1").fetchone()
        self.assertEqual(row["status"], "PENDING_REVIEW")


if __name__ == "__main__":
    unittest.main()
