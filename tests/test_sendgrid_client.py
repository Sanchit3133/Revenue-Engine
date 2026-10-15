import unittest
from unittest.mock import Mock

import requests

from app.outreach import Message
from app.sendgrid_client import (
    SENDGRID_MAIL_SEND_URL,
    send_message,
)


TEST_EMAIL = "contact@coachinginstitute.in"
TEST_SENDER = "Sanchit <hello@sanchit-agarwal.me>"
TEST_REPLY_TO = "hello@sanchit-agarwal.me"


def valid_message() -> Message:
    return Message(
        email=TEST_EMAIL,
        subject="Quick question about your enquiries",
        body=(
            "How do you currently handle course or admission enquiries "
            "when the person does not respond after the first conversation?"
        ),
    )


class TestSendGridClient(unittest.TestCase):
    def make_session(self, status_code=202, message_id="sg-test-message-id"):
        session = Mock()

        response = Mock()
        response.status_code = status_code
        response.headers = {}

        if message_id is not None:
            response.headers["X-Message-Id"] = message_id

        session.post.return_value = response

        return session

    def test_missing_api_key_is_rejected(self):
        session = self.make_session()

        result = send_message(
            valid_message(),
            api_key=None,
            sender=TEST_SENDER,
            session=session,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.status_code)
        self.assertEqual(result.reason, "sendgrid_api_key_missing")
        session.post.assert_not_called()

    def test_missing_sender_is_rejected(self):
        session = self.make_session()

        result = send_message(
            valid_message(),
            api_key="test-api-key",
            sender=None,
            session=session,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.status_code)
        self.assertEqual(result.reason, "sendgrid_sender_missing")
        session.post.assert_not_called()

    def test_invalid_message_is_rejected_before_http_request(self):
        session = self.make_session()

        message = Message(
            email=TEST_EMAIL,
            subject="Buy now",
            body="Please visit [BUY_LINK]",
        )

        result = send_message(
            message,
            api_key="test-api-key",
            sender=TEST_SENDER,
            session=session,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.reason, "unresolved_buy_link")
        session.post.assert_not_called()

    def test_successful_send_returns_202_and_message_id(self):
        session = self.make_session(
            status_code=202,
            message_id="sg-success-123",
        )

        result = send_message(
            valid_message(),
            api_key="test-api-key",
            sender=TEST_SENDER,
            reply_to=TEST_REPLY_TO,
            session=session,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.message_id, "sg-success-123")
        self.assertEqual(result.reason, "accepted")

        session.post.assert_called_once()

    def test_request_uses_sendgrid_mail_send_endpoint(self):
        session = self.make_session()

        send_message(
            valid_message(),
            api_key="test-api-key",
            sender=TEST_SENDER,
            session=session,
        )

        call = session.post.call_args

        self.assertEqual(
            call.args[0],
            SENDGRID_MAIL_SEND_URL,
        )

    def test_request_contains_bearer_authentication(self):
        session = self.make_session()

        send_message(
            valid_message(),
            api_key="test-api-key",
            sender=TEST_SENDER,
            session=session,
        )

        call = session.post.call_args
        headers = call.kwargs["headers"]

        self.assertEqual(
            headers["Authorization"],
            "Bearer test-api-key",
        )
        self.assertEqual(
            headers["Content-Type"],
            "application/json",
        )

    def test_request_contains_plain_text_payload(self):
        session = self.make_session()

        message = valid_message()

        send_message(
            message,
            api_key="test-api-key",
            sender=TEST_SENDER,
            session=session,
        )

        call = session.post.call_args
        payload = call.kwargs["json"]

        self.assertEqual(
            payload["personalizations"][0]["to"][0]["email"],
            TEST_EMAIL,
        )
        self.assertEqual(
            payload["from"]["email"],
            TEST_SENDER,
        )
        self.assertEqual(
            payload["subject"],
            message.subject,
        )
        self.assertEqual(
            payload["content"][0]["type"],
            "text/plain",
        )
        self.assertEqual(
            payload["content"][0]["value"],
            message.body,
        )

    def test_reply_to_is_added_when_configured(self):
        session = self.make_session()

        send_message(
            valid_message(),
            api_key="test-api-key",
            sender=TEST_SENDER,
            reply_to=TEST_REPLY_TO,
            session=session,
        )

        payload = session.post.call_args.kwargs["json"]

        self.assertEqual(
            payload["reply_to"]["email"],
            TEST_REPLY_TO,
        )

    def test_reply_to_is_omitted_when_empty(self):
        session = self.make_session()

        send_message(
            valid_message(),
            api_key="test-api-key",
            sender=TEST_SENDER,
            reply_to="",
            session=session,
        )

        payload = session.post.call_args.kwargs["json"]

        self.assertNotIn("reply_to", payload)

    def test_non_202_response_is_failure(self):
        session = self.make_session(
            status_code=400,
            message_id="sg-error-message",
        )

        result = send_message(
            valid_message(),
            api_key="test-api-key",
            sender=TEST_SENDER,
            session=session,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.message_id, "sg-error-message")
        self.assertEqual(result.reason, "sendgrid_http_400")

    def test_request_exception_is_failure(self):
        session = Mock()

        session.post.side_effect = requests.RequestException(
            "network unavailable"
        )

        result = send_message(
            valid_message(),
            api_key="test-api-key",
            sender=TEST_SENDER,
            session=session,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.status_code)
        self.assertIsNone(result.message_id)
        self.assertEqual(
            result.reason,
            "sendgrid_request_failed:RequestException",
        )

    def test_timeout_is_passed_to_request(self):
        session = self.make_session()

        send_message(
            valid_message(),
            api_key="test-api-key",
            sender=TEST_SENDER,
            timeout=37,
            session=session,
        )

        call = session.post.call_args

        self.assertEqual(
            call.kwargs["timeout"],
            37,
        )


if __name__ == "__main__":
    unittest.main()