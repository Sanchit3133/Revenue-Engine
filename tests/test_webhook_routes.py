import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from flask import Flask  # noqa: F401
    from app.web import create_app
    FLASK_AVAILABLE = True
except ModuleNotFoundError:
    create_app = None
    FLASK_AVAILABLE = False

import app.database as database


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is installed from requirements.txt in the application environment")
class TestWebhookRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "webhooks.db"
        self.db_patch = patch.object(database, "settings", SimpleNamespace(db_path=self.path))
        self.db_patch.start()
        self.webhook_settings_patch = patch(
            "app.web.webhooks.settings",
            SimpleNamespace(brevo_webhook_token="test-brevo-token", razorpay_webhook_secret="test-razorpay-secret"),
        )
        self.webhook_settings_patch.start()
        database.init_db()
        with database.connect() as conn:
            conn.execute("INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)", ("route-place", "Route Test", "route@example.com", "CONTACTED"))
            lead_id = conn.execute("SELECT id FROM leads").fetchone()[0]
            conn.execute("INSERT INTO outreach(lead_id,email,subject,body,attempt_number,status,provider,provider_message_id) VALUES(?,?,?,?,1,'SENT','brevo','route-message-id')", (lead_id, "route@example.com", "Question", "Body"))
            conn.commit()
        self.lead_id = lead_id
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
        self.client = self.app.test_client()

    def tearDown(self):
        self.webhook_settings_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_brevo_webhook_rejects_missing_token(self):
        response = self.client.post("/webhooks/brevo", json={"event": "delivered", "email": "route@example.com", "message-id": "route-message-id"})
        self.assertEqual(response.status_code, 401)

    def test_brevo_webhook_processes_authenticated_delivery(self):
        response = self.client.post(
            "/webhooks/brevo",
            json={"event": "delivered", "email": "route@example.com", "message-id": "route-message-id", "ts_event": 123},
            headers={"X-Revenue-Webhook-Token": "test-brevo-token"},
        )
        self.assertEqual(response.status_code, 200)
        with database.connect() as conn:
            state = conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0]
        self.assertEqual(state, "DELIVERED")

    def test_razorpay_webhook_rejects_bad_signature(self):
        response = self.client.post("/webhooks/razorpay", data=b'{"event":"payment.captured"}', content_type="application/json", headers={"X-Razorpay-Signature": "bad"})
        self.assertEqual(response.status_code, 401)

    def test_razorpay_webhook_accepts_valid_signed_capture(self):
        payload = {
            "id": "evt_route_capture",
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": "pay_route_capture", "order_id": "order_route_capture", "status": "captured",
                "captured": True, "amount": 49000, "currency": "INR", "notes": {"lead_id": self.lead_id},
            }}}
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(b"test-razorpay-secret", raw, hashlib.sha256).hexdigest()
        response = self.client.post("/webhooks/razorpay", data=raw, content_type="application/json", headers={"X-Razorpay-Signature": signature})
        self.assertEqual(response.status_code, 200)
        with database.connect() as conn:
            state = conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM payments WHERE provider_payment_id='pay_route_capture'").fetchone()[0]
        self.assertEqual(state, "PAID")
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
