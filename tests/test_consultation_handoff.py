import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import app.database as database
from app.consultation_handoff import new_handoff_material, resolve_handoff
from app.web.session_state import save_consultation


class TestConsultationHandoff(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "handoff.db"
        self.settings = SimpleNamespace(
            db_path=self.path,
            database_backend="sqlite",
            environment="development",
            database_url=None,
        )
        self.patch = patch.object(database, "settings", self.settings)
        self.patch.start()
        database.init_db()
        with database.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO leads(place_id,business_name,email,website,state)
                   VALUES(?,?,?,?,?)""",
                ("overture:handoff-1", "Example Coaching", "owner@example.in",
                 "https://example.in", "CONTACTED"),
            )
            self.lead_id = cursor.lastrowid
            conn.commit()
        self.flask_app = Flask(__name__)
        self.flask_app.secret_key = "test-secret"

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def _make_token(self):
        material = new_handoff_material("https://revenue-engine.example.app")
        self.assertIsNotNone(material)
        with database.connect() as conn:
            conn.execute(
                """INSERT INTO consultation_handoff_tokens(token_hash,lead_id,expires_at)
                   VALUES(?,?,?)""",
                (material["token_hash"], self.lead_id, material["expires_at"]),
            )
            conn.commit()
        return material

    def test_handoff_resolves_without_exposing_email_in_url(self):
        material = self._make_token()
        self.assertNotIn("owner@example.in", material["url"])
        with database.connect() as conn:
            found = resolve_handoff(conn, material["token"])
        self.assertEqual(found["lead_id"], self.lead_id)
        self.assertEqual(found["email"], "owner@example.in")

    def test_invalid_token_does_not_resolve(self):
        with database.connect() as conn:
            self.assertIsNone(resolve_handoff(conn, "not-a-real-long-enough-token-123456"))

    def test_consent_links_consultation_and_consumes_token_once(self):
        material = self._make_token()
        data = {
            "stage": "REPLIED",
            "business_name": "Example Coaching",
            "contact_email": "owner@example.in",
            "contact_consent": True,
            "submission_public_id": None,
            "consent_at": None,
            "website": "https://example.in",
            "lead_id": self.lead_id,
            "handoff_token_hash": material["token_hash"],
            "requirements_collected": False,
        }
        with self.flask_app.test_request_context("/consultation"):
            save_consultation(data)

        with database.connect() as conn:
            submission = conn.execute(
                "SELECT public_id,lead_id,email FROM consultation_submissions"
            ).fetchone()
            token = conn.execute(
                "SELECT used_at,consultation_public_id FROM consultation_handoff_tokens"
            ).fetchone()
            lead = conn.execute("SELECT state FROM leads WHERE id=?", (self.lead_id,)).fetchone()
        self.assertEqual(submission["lead_id"], self.lead_id)
        self.assertEqual(submission["email"], "owner@example.in")
        self.assertTrue(token["used_at"])
        self.assertEqual(token["consultation_public_id"], submission["public_id"])
        self.assertEqual(lead["state"], "INTERESTED")

    def test_handoff_cannot_be_reused_for_another_consultation(self):
        material = self._make_token()
        first = {
            "stage": "REPLIED", "business_name": "Example Coaching",
            "contact_email": "owner@example.in", "contact_consent": True,
            "submission_public_id": None, "consent_at": None,
            "website": "https://example.in", "lead_id": self.lead_id,
            "handoff_token_hash": material["token_hash"],
        }
        with self.flask_app.test_request_context("/consultation"):
            save_consultation(first)

        second = {
            "stage": "REPLIED", "business_name": "Another Business",
            "contact_email": "owner@example.in", "contact_consent": True,
            "submission_public_id": "second-consultation", "consent_at": None,
            "website": "https://another.example", "lead_id": self.lead_id,
            "handoff_token_hash": material["token_hash"],
        }
        with self.flask_app.test_request_context("/consultation"):
            with self.assertRaisesRegex(ValueError, "already_used"):
                save_consultation(second)
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM consultation_submissions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
