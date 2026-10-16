import json
import re
import tempfile
import unittest
from pathlib import Path
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
class TestServerSideConsultationState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "consultation.db"
        self.db_patch = patch.object(database, "settings", type("DBSettings", (), {"db_path": self.db_path})())
        self.db_patch.start()
        database.init_db()
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-session-secret"})
        self.client = self.app.test_client()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_consultation_state_is_server_side_and_survives_next_request(self):
        page = self.client.get("/consultation")
        token = re.search(rb'name="consultation_csrf_token" value="([^"]+)"', page.data).group(1).decode()
        response = self.client.post("/consultation", data={
            "consultation_csrf_token": token,
            "action": "business_details",
            "business_name": "Example Coaching",
            "contact_email": "owner@example.com",
            "contact_consent": "yes",
            "business_type": "IELTS coaching",
            "website": "https://example.com",
            "customer_process": "Forms, calls and staff follow-up",
            "biggest_problem": "Enquiries are missed after the first conversation",
            "current_tools": "Spreadsheets",
        })
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as sess:
            public_id = sess.get("consultation_public_id")
            self.assertTrue(public_id)
            self.assertNotIn("consultation", sess)
        next_page = self.client.get("/client/request")
        self.assertEqual(next_page.status_code, 200)
        self.assertIn(b"Example Coaching", next_page.data)
        with database.connect() as conn:
            row = conn.execute("SELECT data_json FROM consultation_submissions WHERE public_id=?", (public_id,)).fetchone()
        self.assertEqual(json.loads(row["data_json"]["contact_consent"] if isinstance(row["data_json"], dict) else row["data_json"])["contact_consent"], True)

    def test_restart_clears_session_pointer_but_keeps_history(self):
        with database.connect() as conn:
            conn.execute("INSERT INTO consultation_submissions(public_id,business_name,email,stage,consent_at,data_json) VALUES(?,?,?,?,?,?)", ("old-public-id", "Example", "a@example.com", "REPLIED", "2026-10-15T00:00:00Z", json.dumps({"business_name":"Example","contact_email":"a@example.com","contact_consent":True,"submission_public_id":"old-public-id","stage":"REPLIED"})))
            conn.commit()
        with self.client.session_transaction() as sess:
            sess["consultation_public_id"] = "old-public-id"
            sess["consultation_csrf_token"] = "test-csrf"
        response = self.client.post("/consultation", data={"consultation_csrf_token":"test-csrf", "action":"restart"})
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertNotIn("consultation_public_id", sess)
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM consultation_submissions WHERE public_id='old-public-id'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
