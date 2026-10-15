import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import app.database as database


class TestDatabaseMigrations(unittest.TestCase):
    def test_old_database_is_migrated_without_losing_outreach_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE leads(id INTEGER PRIMARY KEY, business_name TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'DISCOVERED', email TEXT);
                CREATE TABLE outreach(id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, email TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, attempt_number INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL, sent_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE suppressions(email TEXT PRIMARY KEY, reason TEXT NOT NULL, source TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE email_events(id INTEGER PRIMARY KEY, email TEXT, event_type TEXT NOT NULL, event_timestamp TEXT, reason TEXT, sg_event_id TEXT, sg_message_id TEXT, raw_event TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE state_events(id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL, old_state TEXT, new_state TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE payments(id INTEGER PRIMARY KEY, lead_id INTEGER, provider TEXT, provider_payment_id TEXT UNIQUE, amount INTEGER, currency TEXT DEFAULT 'INR', status TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO leads(id,business_name,state,email) VALUES(1,'Kept Lead','QUALIFIED','contact@coachinginstitute.in');
                INSERT INTO outreach(id,lead_id,email,subject,body,attempt_number,status) VALUES(1,1,'contact@coachinginstitute.in','Kept subject','Kept body',1,'FAILED');
            """)
            conn.commit()
            conn.close()
            with patch.object(database, "settings", SimpleNamespace(db_path=path)):
                database.init_db()
                with database.connect() as migrated:
                    outreach = migrated.execute("SELECT id,status,subject FROM outreach WHERE id=1").fetchone()
                    columns = {row["name"] for row in migrated.execute("PRAGMA table_info(outreach)")}
                    self.assertEqual(tuple(outreach), (1, "FAILED", "Kept subject"))
                    self.assertTrue({"provider", "provider_message_id", "last_error", "retry_of_outreach_id"}.issubset(columns))
                    lead_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(leads)")}
                    self.assertIn("country", lead_columns)
                    self.assertEqual(migrated.execute("SELECT country FROM leads WHERE id=1").fetchone()[0], "India")
                    self.assertEqual(migrated.execute("SELECT COUNT(*) FROM consultation_submissions").fetchone()[0], 0)
                    self.assertEqual(migrated.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
