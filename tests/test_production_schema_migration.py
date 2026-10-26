import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import database


class TestProductionSchemaMigrationGuard(unittest.TestCase):
    def production_settings(self):
        return SimpleNamespace(
            environment="production",
            database_backend="postgresql",
            database_url="postgresql://example.invalid/revenue_engine",
        )

    def test_production_schema_migration_requires_explicit_approval(self):
        with patch.object(database, "settings", self.production_settings()), patch.object(
            database, "connect"
        ) as connect:
            with self.assertRaisesRegex(
                database.DatabaseConfigurationError, "explicit --allow-production"
            ):
                database.init_db()
            connect.assert_not_called()

    def test_production_schema_migration_requires_production_environment(self):
        development_settings = SimpleNamespace(
            environment="development",
            database_backend="sqlite",
            database_url=None,
        )
        with patch.object(database, "settings", development_settings), patch.object(
            database, "connect"
        ) as connect:
            with self.assertRaisesRegex(
                database.DatabaseConfigurationError, "only when ENVIRONMENT=production"
            ):
                database.init_db(allow_production=True)
            connect.assert_not_called()

    def test_explicit_production_migration_runs_only_with_postgresql(self):
        fake_connection = Mock()
        fake_connection.__enter__.return_value = fake_connection
        fake_connection.execute.return_value.fetchone.side_effect = [(False,), (0,)]

        with patch.object(database, "settings", self.production_settings()), patch.object(
            database, "connect", return_value=fake_connection
        ):
            database.init_db(allow_production=True)

        fake_connection.commit.assert_called_once()
        calls = [call.args[0] for call in fake_connection.execute.call_args_list]
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS leads" in sql for sql in calls))
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS consultation_messages" in sql for sql in calls))


if __name__ == "__main__":
    unittest.main()
