"""Offline regression tests for quote checkout and Razorpay payment gating."""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.webhook_processing import process_razorpay_event
import app.database as database
from app.web import create_app


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class TestClientPaymentFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "client-payment.db"
        self.db_patch = patch.object(database, "settings", SimpleNamespace(db_path=self.db_path))
        self.db_patch.start()

        self.admin_settings = SimpleNamespace(
            owner_dashboard_password="test-owner-password",
            public_app_url="https://revenue-engine.example",
            brevo_api_key="brevo-test-key",
            brevo_sender="owner@example.com",
            owner_notification_email="owner@example.com",
        )
        self.admin_settings_patch = patch("app.web.admin.settings", self.admin_settings)
        self.admin_settings_patch.start()

        self.payment_settings = SimpleNamespace(
            razorpay_key_id="rzp_test_testkey",
            razorpay_key_secret="test-razorpay-api-secret",
            razorpay_webhook_secret="test-razorpay-webhook-secret",
            razorpay_live_payments_enabled=False,
        )
        self.payment_settings_patch = patch("app.web.payments.settings", self.payment_settings)
        self.payment_settings_patch.start()

        database.init_db()
        with database.connect() as conn:
            conn.execute(
                """INSERT INTO client_requests
                   (public_id,business_name,email,request_type,message,status,draft_filename,
                    draft_content,draft_source,consent_at)
                   VALUES(?,?,?,?,?,'PENDING_REVIEW','landing-page-draft.html',
                    '<!doctype html><title>Draft</title>','template_fallback',?)""",
                (
                    "request-public-001",
                    "Example Coaching",
                    "client@example.com",
                    "delivery",
                    "Prepare a landing page draft for our course",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            self.request_id = int(conn.execute(
                "SELECT id FROM client_requests WHERE public_id='request-public-001'"
            ).fetchone()[0])

        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["owner_authenticated"] = True
            session["owner_csrf_token"] = "owner-test-csrf"

    def tearDown(self):
        self.payment_settings_patch.stop()
        self.admin_settings_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def _insert_quote(self, *, amount=49000, currency="INR", status="ORDER_CREATED",
                      order_id="order_test_001", payment_id=None):
        token = "known-test-checkout-token"
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        with database.connect() as conn:
            conn.execute(
                """INSERT INTO client_orders
                   (public_id,client_request_id,token_hash,amount,currency,scope_summary,status,
                    razorpay_order_id,razorpay_payment_id,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    "quote-public-001", self.request_id,
                    hashlib.sha256(token.encode()).hexdigest(), amount, currency,
                    "One landing page with course information and enquiry CTA",
                    status, order_id, payment_id, expires,
                ),
            )
            if order_id:
                conn.execute(
                    """INSERT INTO payments
                       (lead_id,provider,provider_payment_id,amount,currency,status,provider_order_id)
                       VALUES(NULL,'razorpay',NULL,?,?,'created',?)""",
                    (amount, currency, order_id),
                )
            conn.commit()
        return token

    def _captured_payload(self, *, order_id="order_test_001", payment_id="pay_test_001",
                          amount=49000, currency="INR", event_id="evt_test_001"):
        return {
            "id": event_id,
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": payment_id,
                "order_id": order_id,
                "status": "captured",
                "captured": True,
                "amount": amount,
                "currency": currency,
                "email": "client@example.com",
                "notes": {},
            }}},
        }

    def test_owner_quote_action_uses_owner_amount_and_sends_expiring_link(self):
        data = {
            "csrf_token": "owner-test-csrf",
            "quote_amount": "490.00",
            "quote_scope": "One responsive landing page for the IELTS course, including one enquiry CTA.",
            "quote_note": "Written scope reviewed with the customer.",
            "confirm_scope": "yes",
        }
        with patch("app.web.admin.send_brevo_email", return_value={
            "success": True, "message_id": "brevo-quote-001"
        }) as send:
            response = self.client.post(
                f"/owner/requests/{self.request_id}/quote", data=data
            )
        self.assertEqual(response.status_code, 302)
        send.assert_called_once()
        mail_body = send.call_args.kwargs["body"]
        self.assertIn("INR 490.00", mail_body)
        self.assertIn("https://revenue-engine.example/pay/", mail_body)
        with database.connect() as conn:
            row = conn.execute(
                "SELECT amount,currency,status,token_hash,scope_summary FROM client_orders WHERE client_request_id=?",
                (self.request_id,),
            ).fetchone()
        self.assertEqual((row["amount"], row["currency"], row["status"]), (49000, "INR", "QUOTED"))
        self.assertEqual(len(row["token_hash"]), 64)
        self.assertNotIn("known-test-checkout-token", row["token_hash"])

    def test_quote_requires_written_scope_confirmation(self):
        with patch("app.web.admin.send_brevo_email") as send:
            response = self.client.post(
                f"/owner/requests/{self.request_id}/quote",
                data={
                    "csrf_token": "owner-test-csrf",
                    "quote_amount": "490.00",
                    "quote_scope": "One landing page for the IELTS course.",
                },
            )
        self.assertEqual(response.status_code, 302)
        send.assert_not_called()
        with database.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM client_orders").fetchone()[0]
        self.assertEqual(count, 0)

    def test_checkout_creates_provider_order_using_database_amount_not_browser_input(self):
        token = self._insert_quote(amount=73500, status="QUOTED", order_id=None)
        page = self.client.get(f"/pay/{token}")
        self.assertEqual(page.status_code, 200)
        with self.client.session_transaction() as session:
            csrf = session["payment_csrf_token"]
        response_payload = {"id": "order_from_razorpay", "amount": 73500, "currency": "INR", "status": "created"}
        with patch("app.web.payments.requests.post", return_value=FakeResponse(200, response_payload)) as provider:
            response = self.client.post(
                f"/pay/{token}/order",
                headers={"X-CSRF-Token": csrf},
                json={"amount": 1, "currency": "USD"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["amount"], 73500)
        self.assertEqual(response.json["currency"], "INR")
        self.assertEqual(provider.call_args.kwargs["json"]["amount"], 73500)
        self.assertEqual(provider.call_args.kwargs["json"]["currency"], "INR")
        with database.connect() as conn:
            quote = conn.execute("SELECT status,razorpay_order_id FROM client_orders").fetchone()
        self.assertEqual((quote["status"], quote["razorpay_order_id"]), ("ORDER_CREATED", "order_from_razorpay"))

    def test_live_razorpay_key_is_blocked_by_default(self):
        token = self._insert_quote(amount=73500, status="QUOTED", order_id=None)
        with self.client.session_transaction() as session:
            csrf = session["payment_csrf_token"]
        self.payment_settings.razorpay_key_id = "rzp_live_not_for_tests"
        with patch("app.web.payments.requests.post") as provider:
            response = self.client.post(
                f"/pay/{token}/order", headers={"X-CSRF-Token": csrf}
            )
        self.assertEqual(response.status_code, 503)
        provider.assert_not_called()

    def test_checkout_callback_with_bad_signature_does_not_query_or_mark_paid(self):
        token = self._insert_quote()
        page = self.client.get(f"/pay/{token}")
        with self.client.session_transaction() as session:
            csrf = session["payment_csrf_token"]
        with patch("app.web.payments.requests.get") as provider:
            response = self.client.post(
                f"/pay/{token}/verify",
                headers={"X-CSRF-Token": csrf},
                json={
                    "razorpay_order_id": "order_test_001",
                    "razorpay_payment_id": "pay_test_001",
                    "razorpay_signature": "invalid-signature",
                },
            )
        self.assertEqual(response.status_code, 401)
        provider.assert_not_called()
        with database.connect() as conn:
            quote = conn.execute("SELECT status FROM client_orders").fetchone()
        self.assertEqual(quote["status"], "ORDER_CREATED")

    def test_verified_checkout_callback_marks_exact_quote_paid(self):
        token = self._insert_quote()
        page = self.client.get(f"/pay/{token}")
        with self.client.session_transaction() as session:
            csrf = session["payment_csrf_token"]
        pay_id = "pay_checkout_captured"
        order_id = "order_test_001"
        signature = hmac.new(
            self.payment_settings.razorpay_key_secret.encode(),
            f"{order_id}|{pay_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        api_payment = {
            "id": pay_id, "order_id": order_id, "amount": 49000,
            "currency": "INR", "status": "captured", "email": "client@example.com",
            "notes": {},
        }
        with patch("app.web.payments.requests.get", return_value=FakeResponse(200, api_payment)):
            response = self.client.post(
                f"/pay/{token}/verify",
                headers={"X-CSRF-Token": csrf},
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": signature,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "paid")
        with database.connect() as conn:
            quote = conn.execute("SELECT status,razorpay_payment_id FROM client_orders").fetchone()
            request_row = conn.execute("SELECT payment_reference FROM client_requests WHERE id=?", (self.request_id,)).fetchone()
            payment = conn.execute("SELECT amount,currency,status FROM payments WHERE provider_payment_id=?", (pay_id,)).fetchone()
        self.assertEqual((quote["status"], quote["razorpay_payment_id"]), ("PAID", pay_id))
        self.assertEqual(request_row["payment_reference"], pay_id)
        self.assertEqual(tuple(payment), (49000, "INR", "captured"))

    def test_capture_webhook_rejects_amount_mismatch_and_does_not_mark_paid(self):
        self._insert_quote(amount=49000)
        with database.connect() as conn:
            result = process_razorpay_event(
                conn, self._captured_payload(amount=100, event_id="evt_wrong_amount")
            )
            quote = conn.execute("SELECT status FROM client_orders").fetchone()
            request_row = conn.execute("SELECT payment_reference FROM client_requests WHERE id=?", (self.request_id,)).fetchone()
        self.assertTrue(result.get("reconciliation_required"))
        self.assertEqual(quote["status"], "PAYMENT_MISMATCH")
        self.assertIsNone(request_row["payment_reference"])

    def test_capture_webhook_rejects_currency_mismatch_and_does_not_mark_paid(self):
        self._insert_quote(amount=49000, currency="INR")
        with database.connect() as conn:
            result = process_razorpay_event(
                conn, self._captured_payload(currency="USD", event_id="evt_wrong_currency")
            )
            quote = conn.execute("SELECT status FROM client_orders").fetchone()
            request_row = conn.execute("SELECT payment_reference FROM client_requests WHERE id=?", (self.request_id,)).fetchone()
        self.assertTrue(result.get("reconciliation_required"))
        self.assertEqual(quote["status"], "PAYMENT_MISMATCH")
        self.assertIsNone(request_row["payment_reference"])

    def test_duplicate_capture_event_does_not_duplicate_payment(self):
        self._insert_quote()
        payload = self._captured_payload(event_id="evt_duplicate_capture")
        with database.connect() as conn:
            first = process_razorpay_event(conn, payload)
            second = process_razorpay_event(conn, payload)
            count = conn.execute("SELECT COUNT(*) FROM payments WHERE provider_payment_id='pay_test_001'").fetchone()[0]
        self.assertTrue(first["accepted"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
