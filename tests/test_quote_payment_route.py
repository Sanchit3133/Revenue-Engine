import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from app.web import create_app
    FLASK_AVAILABLE = True
except ModuleNotFoundError:
    create_app = None
    FLASK_AVAILABLE = False

import app.database as database


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is required for owner quote-route tests")
class TestOwnerQuotePaymentLink(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "quote.db"
        self.db_patch = patch.object(database, "settings", SimpleNamespace(db_path=self.db_path))
        self.db_patch.start()
        self.owner_settings = SimpleNamespace(
            owner_dashboard_password="owner-test-password",
            razorpay_key_id="rzp_test_key",
            razorpay_key_secret="razorpay-secret-for-test",
            flask_secret_key="long-flask-test-secret",
            brevo_api_key="brevo-test-api-key",
            brevo_sender="billing@example.com",
            public_app_url="https://revenue-engine.example.test",
        )
        self.settings_patch = patch("app.web.admin.settings", self.owner_settings)
        self.settings_patch.start()
        database.init_db()
        self.request_id = 1
        self.request_public_id = "quote-request-001"
        with database.connect() as conn:
            conn.execute(
                """INSERT INTO client_requests
                   (public_id,consent_at,business_name,email,request_type,message,status,
                    draft_filename,draft_content,draft_content_type,draft_source)
                   VALUES (?,?,?,?,?,'Please prepare a course landing page','PENDING_REVIEW',
                           'landing.html','<h1>Private draft</h1>','text/html','template_fallback')""",
                (self.request_public_id, "2026-10-19T00:00:00+00:00", "Example Coaching", "buyer@example.com", "delivery"),
            )
            conn.commit()
        self.app = create_app({"TESTING": True, "SECRET_KEY": self.owner_settings.flask_secret_key})
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["owner_authenticated"] = True
            session["owner_csrf_token"] = "test-csrf"

    def tearDown(self):
        self.settings_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_owner_confirmation_creates_server_order_and_emails_link(self):
        provider_result = {
            "success": True, "uncertain": False, "order_id": "order_owner_quote_001",
            "amount": 49000, "currency": "INR",
        }
        with patch("app.web.admin.create_razorpay_order", return_value=provider_result) as create_order, patch(
            "app.web.admin.send_brevo_email", return_value={"success": True, "reason": "accepted", "message_id": "msg-1", "uncertain": False}
        ) as send_email:
            response = self.client.post(
                "/owner/requests/1/payment-link",
                data={
                    "csrf_token": "test-csrf",
                    "csrf_scope_confirmation": "yes",
                    "amount_rupees": "490.00",
                    "currency": "INR",
                    "scope_note": "Written scope accepted by customer",
                },
            )

        self.assertEqual(response.status_code, 302)
        create_order.assert_called_once()
        self.assertEqual(create_order.call_args.kwargs["amount_minor"], 49000)
        self.assertEqual(create_order.call_args.kwargs["currency"], "INR")
        send_email.assert_called_once()
        email_body = send_email.call_args.kwargs["body"]
        self.assertIn("/pay/", email_body)
        self.assertIn("₹490.00 INR", email_body)
        with database.connect() as conn:
            payment = conn.execute(
                """SELECT amount,currency,status,provider_order_id,client_request_public_id,
                          checkout_token_hash,checkout_expires_at
                   FROM payments WHERE client_request_public_id=?""",
                (self.request_public_id,),
            ).fetchone()
            request_row = conn.execute(
                "SELECT status,scope_confirmed,approval_note FROM client_requests WHERE public_id=?",
                (self.request_public_id,),
            ).fetchone()
        self.assertEqual(payment["amount"], 49000)
        self.assertEqual(payment["currency"], "INR")
        self.assertEqual(payment["status"], "link_sent")
        self.assertEqual(payment["provider_order_id"], "order_owner_quote_001")
        self.assertEqual(payment["client_request_public_id"], self.request_public_id)
        self.assertTrue(payment["checkout_token_hash"])
        self.assertTrue(payment["checkout_expires_at"])
        self.assertEqual(request_row["status"], "AWAITING_PAYMENT")
        self.assertEqual(request_row["scope_confirmed"], 1)
        self.assertEqual(request_row["approval_note"], "Written scope accepted by customer")

    def test_owner_must_explicitly_confirm_scope_and_amount(self):
        with patch("app.web.admin.create_razorpay_order") as create_order, patch("app.web.admin.send_brevo_email") as send_email:
            response = self.client.post(
                "/owner/requests/1/payment-link",
                data={
                    "csrf_token": "test-csrf",
                    "amount_rupees": "490.00",
                    "currency": "INR",
                    "scope_note": "Written scope accepted by customer",
                },
            )
        self.assertEqual(response.status_code, 302)
        create_order.assert_not_called()
        send_email.assert_not_called()
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)

    def test_missing_razorpay_credentials_block_order_creation(self):
        self.owner_settings.razorpay_key_secret = None
        with patch("app.web.admin.create_razorpay_order") as create_order, patch("app.web.admin.send_brevo_email") as send_email:
            response = self.client.post(
                "/owner/requests/1/payment-link",
                data={
                    "csrf_token": "test-csrf",
                    "csrf_scope_confirmation": "yes",
                    "amount_rupees": "490.00",
                    "currency": "INR",
                    "scope_note": "Written scope accepted by customer",
                },
            )
        self.assertEqual(response.status_code, 302)
        create_order.assert_not_called()
        send_email.assert_not_called()
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
