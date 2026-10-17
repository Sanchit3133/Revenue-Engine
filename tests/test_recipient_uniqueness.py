import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.database as database


class TestRecipientUniqueness(unittest.TestCase):
    def test_normalized_email_is_globally_unique_across_leads(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "dedupe.db"
            configured = SimpleNamespace(
                db_path=db_path,
                database_backend="sqlite",
                environment="development",
                database_url=None,
            )
            with patch.object(database, "settings", configured):
                database.init_db()
                with database.connect() as conn:
                    conn.execute(
                        "INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)",
                        ("source-a:1", "Business One", "contact@sample.example", "QUALIFIED"),
                    )
                    conn.commit()
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            "INSERT INTO leads(place_id,business_name,email,state) VALUES(?,?,?,?)",
                            ("source-b:2", "Business Two", " CONTACT@SAMPLE.EXAMPLE ", "QUALIFIED"),
                        )
                    conn.rollback()
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
                        1,
                    )


if __name__ == "__main__":
    unittest.main()
