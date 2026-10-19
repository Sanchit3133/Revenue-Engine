import hashlib
import hmac
import unittest
from unittest.mock import Mock

import requests

from app.payment_checkout import (
    amount_to_minor_units,
    checkout_token,
    create_razorpay_order,
    token_hash,
    verify_checkout_signature,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class TestPaymentCheckoutHelpers(unittest.TestCase):
    def test_amount_is_converted_to_minor_units_without_float_rounding(self):
        self.assertEqual(amount_to_minor_units("490"), 49000)
        self.assertEqual(amount_to_minor_units("490.50"), 49050)

    def test_amount_rejects_invalid_precision_and_unsupported_currency(self):
        for raw in ("", "0", "-1", "10.001", "nan", "inf"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                amount_to_minor_units(raw)
        with self.assertRaises(ValueError):
            amount_to_minor_units("100", currency="USD")

    def test_checkout_token_is_deterministic_and_order_bound(self):
        values = {"secret": "unit-test-secret", "request_public_id": "request-01", "order_id": "order_01", "expires_at": "2026-10-27T00:00:00+00:00"}
        first = checkout_token(**values)
        self.assertEqual(first, checkout_token(**values))
        self.assertNotEqual(first, checkout_token(**{**values, "order_id": "order_02"}))
        self.assertEqual(len(token_hash(first)), 64)

    def test_checkout_signature_verification(self):
        order_id = "order_01"
        payment_id = "pay_01"
        secret = "razorpay-test-secret"
        signature = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        self.assertTrue(verify_checkout_signature(order_id=order_id, payment_id=payment_id, signature=signature, key_secret=secret))
        self.assertFalse(verify_checkout_signature(order_id=order_id, payment_id=payment_id, signature="bad", key_secret=secret))
        self.assertFalse(verify_checkout_signature(order_id=order_id, payment_id=payment_id, signature=signature, key_secret=None))

    def test_order_creation_uses_server_amount_and_credentials(self):
        client = Mock()
        client.post.return_value = FakeResponse(200, {"id": "order_123", "amount": 49000, "currency": "INR"})
        result = create_razorpay_order(
            key_id="rzp_test_key", key_secret="not-for-client",
            amount_minor=49000, currency="INR", receipt="re-123",
            request_public_id="client-request-1", session=client,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["order_id"], "order_123")
        kwargs = client.post.call_args.kwargs
        self.assertEqual(kwargs["auth"], ("rzp_test_key", "not-for-client"))
        self.assertEqual(kwargs["json"]["amount"], 49000)
        self.assertEqual(kwargs["json"]["currency"], "INR")
        self.assertNotIn("key_secret", result)

    def test_order_creation_marks_transport_error_uncertain(self):
        client = Mock()
        client.post.side_effect = requests.Timeout()
        result = create_razorpay_order(
            key_id="rzp_test_key", key_secret="secret",
            amount_minor=49000, currency="INR", receipt="re-123",
            request_public_id="client-request-1", session=client,
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["uncertain"])
        self.assertIn("check_dashboard_before_retry", result["reason"])

    def test_provider_amount_mismatch_is_not_accepted(self):
        client = Mock()
        client.post.return_value = FakeResponse(200, {"id": "order_123", "amount": 1, "currency": "INR"})
        result = create_razorpay_order(
            key_id="rzp_test_key", key_secret="secret",
            amount_minor=49000, currency="INR", receipt="re-123",
            request_public_id="client-request-1", session=client,
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["uncertain"])
        self.assertEqual(result["reason"], "razorpay_order_response_mismatch_check_dashboard")


if __name__ == "__main__":
    unittest.main()
