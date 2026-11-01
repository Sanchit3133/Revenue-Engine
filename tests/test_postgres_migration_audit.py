import unittest

from scripts.audit_postgres_migration import analyze_rows


class TestPostgresMigrationRowAudit(unittest.TestCase):
    def test_identical_rows_with_same_primary_key_are_counted(self):
        source = [{"id": 1, "public_id": "a", "content": "hello"}]
        target = [{"id": 1, "public_id": "a", "content": "hello"}]
        result = analyze_rows(source, target, ("id",), [("public_id",)])
        self.assertEqual(result["identical_same_pk"], 1)
        self.assertEqual(result["different_same_pk"], 0)
        self.assertEqual(result["source_only_pk"], 0)

    def test_different_rows_with_same_primary_key_are_conflicts(self):
        source = [{"id": 1, "content": "source"}]
        target = [{"id": 1, "content": "destination"}]
        result = analyze_rows(source, target, ("id",), [])
        self.assertEqual(result["different_same_pk"], 1)

    def test_source_only_rows_are_counted_without_being_conflicts(self):
        source = [{"id": 1, "public_id": "a"}, {"id": 2, "public_id": "b"}]
        target = [{"id": 1, "public_id": "a"}]
        result = analyze_rows(source, target, ("id",), [("public_id",)])
        self.assertEqual(result["identical_same_pk"], 1)
        self.assertEqual(result["source_only_pk"], 1)
        self.assertEqual(result["unique_conflict_rows"], 0)

    def test_destination_unique_key_collision_is_detected(self):
        source = [{"id": 9, "public_id": "already-used"}]
        target = [{"id": 2, "public_id": "already-used"}]
        result = analyze_rows(source, target, ("id",), [("public_id",)])
        self.assertEqual(result["source_only_pk"], 1)
        self.assertEqual(result["unique_conflict_rows"], 1)

    def test_null_unique_key_does_not_conflict(self):
        source = [{"id": 9, "public_id": None}]
        target = [{"id": 2, "public_id": None}]
        result = analyze_rows(source, target, ("id",), [("public_id",)])
        self.assertEqual(result["unique_conflict_rows"], 0)

    def test_primary_key_is_required(self):
        with self.assertRaisesRegex(ValueError, "primary key"):
            analyze_rows([], [], (), [])


if __name__ == "__main__":
    unittest.main()
