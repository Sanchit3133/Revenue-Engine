import unittest
from unittest.mock import Mock
import requests
from app.brevo_client import BREVO_SEND_URL, send_message
from app.outreach import Message


class TestBrevoClient(unittest.TestCase):
    def setUp(self):
        self.message = Message(
            email="contact@coachinginstitute.in",
            subject="Quick question about your enquiries",
            body="How do you currently handle admission enquiries?",
        )
        self.session = Mock()
        self.response = Mock()
        self.response.status_code = 201
        self.response.headers = {}
        self.response.json.return_value = {"messageId": "brevo-msg-123"}
        self.session.post.return_value = self.response

    def test_success_requires_201_and_returns_message_id(self):
        result = send_message(
            self.message,
            api_key="test-brevo-key",
            sender="Sanchit <hello@sanchit-agarwal.me>",
            reply_to="noreply.sanchitagarwal@gmail.com",
            session=self.session,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.message_id, "brevo-msg-123")
        self.assertEqual(result.reason, "accepted")

    def test_request_uses_brevo_endpoint_and_api_key_header(self):
        send_message(self.message, api_key="test-key", sender="hello@sanchit-agarwal.me", session=self.session)
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], BREVO_SEND_URL)
        self.assertEqual(kwargs["headers"]["api-key"], "test-key")
        self.assertEqual(kwargs["json"]["sender"]["email"], "hello@sanchit-agarwal.me")
        self.assertEqual(kwargs["json"]["to"][0]["email"], self.message.email)

    def test_reply_to_is_added(self):
        send_message(self.message, api_key="test-key", sender="hello@sanchit-agarwal.me", reply_to="reply@example.com", session=self.session)
        self.assertEqual(self.session.post.call_args.kwargs["json"]["replyTo"]["email"], "reply@example.com")

    def test_missing_key_fails_before_request(self):
        result = send_message(self.message, api_key=None, sender="hello@sanchit-agarwal.me", session=self.session)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "brevo_api_key_missing")
        self.session.post.assert_not_called()

    def test_invalid_message_fails_before_request(self):
        bad = Message("contact@coachinginstitute.in", "Buy now", "Please pay now")
        result = send_message(bad, api_key="key", sender="hello@sanchit-agarwal.me", session=self.session)
        self.assertFalse(result.success)
        self.session.post.assert_not_called()

    def test_non_201_response_is_failure(self):
        self.response.status_code = 401
        self.response.text = "denied"
        result = send_message(self.message, api_key="key", sender="hello@sanchit-agarwal.me", session=self.session)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "brevo_http_401")

    def test_network_failure_is_reported(self):
        self.session.post.side_effect = requests.RequestException("network unavailable")
        result = send_message(self.message, api_key="key", sender="hello@sanchit-agarwal.me", session=self.session)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "brevo_request_failed:RequestException")


if __name__ == "__main__":
    unittest.main()
