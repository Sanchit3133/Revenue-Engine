import base64
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.notifications import send_brevo_email, notify_owner


class TestNotifications(unittest.TestCase):
    def test_missing_brevo_credentials_fails_without_request(self):
        with patch("app.notifications.settings", SimpleNamespace(brevo_api_key=None, brevo_sender=None)):
            with patch("app.notifications.requests.post") as post:
                result = send_brevo_email(to_email="owner@example.com", subject="Request", body="Review")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "brevo_not_configured")
        post.assert_not_called()

    def test_owner_notification_includes_configured_recipient(self):
        settings = SimpleNamespace(owner_notification_email="owner@example.com", brevo_api_key="fake", brevo_sender="sender@example.com", brevo_reply_to=None)
        response = Mock(status_code=201)
        response.json.return_value = {"messageId": "brevo-msg-1"}
        with patch("app.notifications.settings", settings), patch("app.notifications.requests.post", return_value=response) as post:
            result = notify_owner("Review needed", "A request arrived")
        self.assertTrue(result["success"])
        self.assertEqual(result["message_id"], "brevo-msg-1")
        self.assertEqual(post.call_args.kwargs["json"]["to"], [{"email": "owner@example.com"}])

    def test_approved_attachment_is_base64_encoded(self):
        response = Mock(status_code=201)
        response.json.return_value = {"messageId": "brevo-msg-2"}
        settings = SimpleNamespace(brevo_api_key="fake", brevo_sender="sender@example.com", brevo_reply_to=None)
        with patch("app.notifications.settings", settings), patch("app.notifications.requests.post", return_value=response) as post:
            result = send_brevo_email(
                to_email="client@example.com", subject="Approved", body="Attached",
                attachment_name="draft.html", attachment_content="<h1>Draft</h1>",
            )
        self.assertTrue(result["success"])
        attach = post.call_args.kwargs["json"]["attachment"][0]
        self.assertEqual(attach["name"], "draft.html")
        self.assertEqual(base64.b64decode(attach["content"]).decode(), "<h1>Draft</h1>")


if __name__ == "__main__":
    unittest.main()
