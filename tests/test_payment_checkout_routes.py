import hashlib
import hmac
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
from app.payment_checkout import checkout_token, token_hash


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is required for customer checkout route tests")
class TestPaymentCheckoutRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "checkout.db"
        self.db_patch = patch.object(database, "settings", SimpleNamespace(db_path=self.db_path))
        self.db_patch.start()
        self.signing_secret = "checkout-signing-test-secret"
        self.key_secret = "razorpay-key-test-secret"
        self.checkout_settings = SimpleNamespace(
            flask_secret_key=self.signing_secret,
            razorpay_key_id="rzp_test_key",
            razorpay_key_secret=self.key_secret,
        )
        self.checkout_patch = patch("app.web.payment_checkout.settings", self.checkout_settings)
        self.checkout_patch.start()
        database.init_db()
        self.request_public_id = "customer-request-001"
        self.order_id = "order_test_checkout_001"
        self.expiry = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        self.token = checkout_token(
            secret=self.signing_secret,
            request_public_id=self.request_public_id,
            order_id=self.order_id,
            expires_at=self.expiry,
        )
        with database.connect() as conn:
            conn.execute(
                """INSERT INTO client_requests
                   (public_id,consent_at,business_name,email,request_type,message,status,
                    draft_filename,draft_content,draft_content_type,draft_source)
                   VALUES(?,?,?,?,?,'Customer requested a landing page','AWAITING_PAYMENT',
                          'draft.html','<h1>Draft</h1>','text/html','template_fallback')""",
                (self.request_public_id, datetime.now(timezone.utc).isoformat(), "Example Coaching", "buyer@example.com", "delivery"),
            )
            conn.execute(
                """INSERT INTO payments
                   (lead_id,provider,amount,currency,status,provider_order_id,client_request_public_id,
                    checkout_token_hash,checkout_expires_at)
                   VALUES(NULL,'razorpay',49000,'INR','link_sent',?,?,?,?)""",
                (self.order_id, self.request_public_id, token_hash(self.token), self.expiry),
            )
            conn.commit()
        self.app = create_app({"TESTING": True, "SECRET_KEY": self.signing_secret})
        self.client = self.app.test_client()

    def tearDown(self):
        self.checkout_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_checkout_page_displays_stored_amount_and_order(self):
        response = self.client.get("/pay/" + self.token)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"490.00", response.data)
        self.assertIn(self.order_id.encode(), response.data)
        self.assertIn(b"rzp_test_key", response.data)
        self.assertNotIn(self.key_secret.encode(), response.data)

    def test_unknown_token_does_not_open_checkout(self):
        response = self.client.get("/pay/not-a-real-token")
        self.assertEqual(response.status_code, 404)

    def test_expired_token_does_not_open_checkout(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        token = checkout_token(secret=self.signing_secret, request_public_id=self.request_public_id, order_id=self.order_id, expires_at=past)
        with database.connect() as conn:
            conn.execute(
                "UPDATE payments SET checkout_token_hash=?,checkout_expires_at=? WHERE provider_order_id=?",
                (token_hash(token), past, self.order_id),
            )
            conn.commit()
        response = self.client.get("/pay/" + token)
        self.assertEqual(response.status_code, 404)

    def test_checkout_signature_does_not_mark_request_paid_before_webhook(self):
        payment_id = "pay_from_checkout"
        signature = hmac.new(
            self.key_secret.encode(),
            f"{self.order_id}|{payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        response = self.client.post(
            "/pay/" + self.token + "/verify",
            json={
                "razorpay_order_id": self.order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["verified"])
        with database.connect() as conn:
            request_status = conn.execute("SELECT status FROM client_requests WHERE public_id=?", (self.request_public_id,)).fetchone()[0]
            payment_status = conn.execute("SELECT status FROM payments WHERE provider_order_id=?", (self.order_id,)).fetchone()[0]
        self.assertEqual(request_status, "AWAITING_PAYMENT")
        self.assertEqual(payment_status, "link_sent")

    def test_invalid_checkout_signature_is_rejected(self):
        response = self.client.post(
            "/pay/" + self.token + "/verify",
            json={
                "razorpay_order_id": self.order_id,
                "razorpay_payment_id": "pay_bad",
                "razorpay_signature": "invalid",
            },
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
