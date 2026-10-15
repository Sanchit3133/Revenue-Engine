import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import app.database as database
try:
    from flask import Flask  # noqa: F401
    from app.web import create_app
except ModuleNotFoundError:
    create_app = None


@unittest.skipIf(create_app is None, "Flask is installed from requirements.txt in the application environment")
class TestConsultationPersistence(unittest.TestCase):
    def test_consenting_consultation_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consult.db"
            with patch.object(database, "settings", SimpleNamespace(db_path=path)):
                app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
                client = app.test_client()
                get_response = client.get("/consultation")
                token = re.search(rb'name="consultation_csrf_token" value="([^"]+)"', get_response.data).group(1).decode()
                response = client.post("/consultation", data={
                    "consultation_csrf_token": token,
                    "action": "business_details",
                    "business_name": "Example Coaching",
                    "contact_email": "owner@example.com",
                    "contact_consent": "yes",
                    "business_type": "IELTS coaching",
                    "website": "https://example.com",
                    "customer_process": "Forms and calls",
                    "biggest_problem": "Enquiries are not followed up",
                    "current_tools": "WhatsApp",
                })
                self.assertEqual(response.status_code, 200)
                with database.connect() as conn:
                    row = conn.execute("SELECT business_name,email,consent_at,data_json FROM consultation_submissions").fetchone()
                    self.assertEqual(row["business_name"], "Example Coaching")
                    self.assertEqual(row["email"], "owner@example.com")
                    self.assertTrue(row["consent_at"])
                    self.assertEqual(json.loads(row["data_json"])["contact_consent"], True)

    def test_missing_consent_does_not_persist_consultation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consult.db"
            with patch.object(database, "settings", SimpleNamespace(db_path=path)):
                app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
                client = app.test_client()
                get_response = client.get("/consultation")
                token = re.search(rb'name="consultation_csrf_token" value="([^"]+)"', get_response.data).group(1).decode()
                response = client.post("/consultation", data={
                    "consultation_csrf_token": token,
                    "action": "business_details",
                    "business_name": "Example Coaching",
                    "contact_email": "owner@example.com",
                    "business_type": "IELTS coaching",
                    "biggest_problem": "Enquiries are not followed up",
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Please confirm", response.data)
                if path.exists():
                    with database.connect() as conn:
                        self.assertEqual(conn.execute("SELECT COUNT(*) FROM consultation_submissions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
