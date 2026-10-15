import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.database as database
from scripts.discover_leads import save_lead


class TestLeadCountry(unittest.TestCase):
    def test_discovered_lead_keeps_selected_country(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "leads.db"
            with patch.object(database, "settings", SimpleNamespace(db_path=db_path)):
                database.init_db()
                with database.connect() as conn:
                    lead_id = save_lead(conn, {
                        "place_id": "test-uganda-001",
                        "business_name": "Example Training Centre",
                        "address": "Kampala",
                        "phone": None,
                        "website": "https://example.test",
                        "email": "info@example.test",
                        "country": "Uganda",
                        "qualification": "coaching_or_training",
                        "score": 70,
                        "signal": "training",
                        "notes": "Test lead only",
                    })
                    self.assertIsNotNone(lead_id)
                    row = conn.execute("SELECT country,state FROM leads WHERE id=?", (lead_id,)).fetchone()
                    self.assertEqual(row["country"], "Uganda")
                    self.assertEqual(row["state"], "QUALIFIED")


if __name__ == "__main__":
    unittest.main()
