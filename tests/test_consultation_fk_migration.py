import unittest

from app.database import DatabaseConfigurationError, _ensure_consultation_lead_foreign_key


class FakeResult:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class FakeConnection:
    def __init__(self, constraint_exists=False, orphan_count=0):
        self.constraint_exists = constraint_exists
        self.orphan_count = orphan_count
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "FROM pg_constraint" in sql:
            return FakeResult(self.constraint_exists)
        if "LEFT JOIN public.leads" in sql:
            return FakeResult(self.orphan_count)
        return FakeResult(None)


class TestConsultationLeadForeignKeyMigration(unittest.TestCase):
    def test_postgresql_adds_missing_foreign_key_when_no_orphans_exist(self):
        conn = FakeConnection(constraint_exists=False, orphan_count=0)

        _ensure_consultation_lead_foreign_key(conn, "postgresql")

        self.assertEqual(len(conn.statements), 3)
        self.assertIn(
            "ADD CONSTRAINT consultation_submissions_lead_id_fkey",
            conn.statements[-1][0],
        )

    def test_existing_foreign_key_is_left_untouched(self):
        conn = FakeConnection(constraint_exists=True)

        _ensure_consultation_lead_foreign_key(conn, "postgresql")

        self.assertEqual(len(conn.statements), 1)
        self.assertNotIn(
            "ADD CONSTRAINT",
            "\n".join(statement for statement, _ in conn.statements),
        )

    def test_orphaned_consultations_block_foreign_key_creation(self):
        conn = FakeConnection(constraint_exists=False, orphan_count=2)

        with self.assertRaisesRegex(DatabaseConfigurationError, "existing submissions"):
            _ensure_consultation_lead_foreign_key(conn, "postgresql")

        self.assertEqual(len(conn.statements), 2)
        self.assertFalse(any("ADD CONSTRAINT" in statement for statement, _ in conn.statements))

    def test_non_postgresql_backend_is_left_untouched(self):
        conn = FakeConnection()

        _ensure_consultation_lead_foreign_key(conn, "sqlite")

        self.assertEqual(conn.statements, [])


if __name__ == "__main__":
    unittest.main()
