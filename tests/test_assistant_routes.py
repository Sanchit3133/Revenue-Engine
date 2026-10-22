import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.database as database

try:
    import flask  # noqa: F401
    from app.web import create_app
    FLASK_AVAILABLE = True
except ModuleNotFoundError:
    create_app = None
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Install application requirements to run Flask route tests")
class TestConsultationAssistantRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "assistant-routes.db"
        self.db_patch = patch.object(
            database, "settings", SimpleNamespace(db_path=self.db_path)
        )
        self.db_patch.start()
        database.init_db()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "assistant-test-secret"})
        self.client = self.app.test_client()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def _start_consented_consultation(self):
        page = self.client.get("/consultation")
        self.assertEqual(page.status_code, 200)

        with self.client.session_transaction() as sess:
            form_csrf = sess["consultation_csrf_token"]
            assistant_csrf = sess["assistant_csrf_token"]

        response = self.client.post("/consultation", data={
            "consultation_csrf_token": form_csrf,
            "action": "business_details",
            "business_name": "Example Coaching",
            "contact_email": "owner@example.com",
            "contact_consent": "yes",
            "business_type": "IELTS coaching",
            "website": "https://example.com",
            "customer_process": "Website forms and calls",
            "biggest_problem": "We want to understand how to grow",
            "current_tools": "Spreadsheets",
        })
        self.assertEqual(response.status_code, 200)

        with self.client.session_transaction() as sess:
            public_id = sess.get("consultation_public_id")
            self.assertTrue(public_id)

        return assistant_csrf, public_id

    def test_chat_rejects_request_without_consent(self):
        response = self.client.post("/api/assistant", json={
            "csrf_token": "irrelevant",
            "message": "How can I grow my business?",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "start_consultation_first")

    def test_chat_rejects_invalid_csrf_for_consented_consultation(self):
        self._start_consented_consultation()
        response = self.client.post("/api/assistant", json={
            "csrf_token": "incorrect-token",
            "message": "How can I grow my business?",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_csrf_token")

    def test_chat_returns_and_persists_assistant_reply(self):
        csrf_token, public_id = self._start_consented_consultation()
        expected_reply = (
            "What is your main growth goal: more enquiries, better conversion, "
            "or repeat customers?"
        )

        with patch(
            "app.web.assistant.build_consultation_reply",
            return_value=(expected_reply, True),
        ) as build_reply:
            response = self.client.post("/api/assistant", json={
                "csrf_token": csrf_token,
                "message": "How can I increase business?",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "reply": expected_reply,
            "using_ai": True,
        })
        build_reply.assert_called_once()

        with database.connect() as conn:
            rows = conn.execute(
                """SELECT role, content FROM consultation_messages
                   WHERE consultation_public_id = ? ORDER BY id""",
                (public_id,),
            ).fetchall()

        self.assertEqual(
            [(row["role"], row["content"]) for row in rows],
            [
                ("user", "How can I increase business?"),
                ("assistant", expected_reply),
            ],
        )

    def test_chat_enforces_hourly_rate_limit(self):
        csrf_token, _public_id = self._start_consented_consultation()

        with self.client.session_transaction() as sess:
            sess["assistant_request_timestamps"] = [time.time() - 5] * 20

        with patch("app.web.assistant.build_consultation_reply") as build_reply:
            response = self.client.post("/api/assistant", json={
                "csrf_token": csrf_token,
                "message": "How can I grow?",
            })

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"], "chat_rate_limit")
        build_reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
