"""Read-only comparison of two Revenue Engine PostgreSQL databases."""

from __future__ import annotations

import os
import sys
from typing import Any

import psycopg
from psycopg import sql

SKIP_TABLES = {"schema_migrations"}
BATCH_SIZE = 500


def _connect_from_env(env_name: str) -> psycopg.Connection:
    dsn = os.environ.get(env_name, "").strip()
    if not dsn:
        raise ValueError(f"{env_name} is not set")
    return psycopg.connect(
        dsn,
        connect_timeout=15,
        options="-c default_transaction_read_only=on",
        application_name="revenue-engine-postgres-migration-audit",
    )


def _database_identity(conn: psycopg.Connection) -> tuple[str, str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current_database(), current_user, "
            "COALESCE(inet_server_addr()::text, '')"
        )
        row = cur.fetchone()
    return str(row[0]), str(row[1]), str(row[2])


def _schema(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        tables = [
            row[0] for row in cur.fetchall()
            if row[0] not in SKIP_TABLES
        ]

        for table in tables:
            cur.execute(
                """
                SELECT column_name, data_type, udt_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table,),
            )
            columns = {
                row[0]: (row[1], row[2], row[3])
                for row in cur.fetchall()
            }

            cur.execute(
                """
                SELECT tc.constraint_type, tc.constraint_name,
                       array_agg(kcu.column_name ORDER BY kcu.ordinal_position)
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON kcu.constraint_catalog = tc.constraint_catalog
                 AND kcu.constraint_schema = tc.constraint_schema
                 AND kcu.constraint_name = tc.constraint_name
                 AND kcu.table_schema = tc.table_schema
                 AND kcu.table_name = tc.table_name
                WHERE tc.constraint_schema = 'public'
                  AND tc.table_name = %s
                  AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
                GROUP BY tc.constraint_type, tc.constraint_name
                """,
                (table,),
            )
            primary_key: tuple[str, ...] = ()
            unique_keys: list[tuple[str, ...]] = []
            for kind, _name, key_columns in cur.fetchall():
                key = tuple(key_columns)
                if kind == "PRIMARY KEY":
                    primary_key = key
                elif key:
                    unique_keys.append(key)

            # Include simple, non-partial unique indexes as well as constraints.
            cur.execute(
                """
                SELECT ARRAY(
                    SELECT a.attname
                    FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum, position)
                    JOIN pg_attribute AS a
                      ON a.attrelid = t.oid AND a.attnum = k.attnum
                    WHERE k.attnum > 0
                    ORDER BY k.position
                )
                FROM pg_index AS i
                JOIN pg_class AS t ON t.oid = i.indrelid
                JOIN pg_namespace AS n ON n.oid = t.relnamespace
                WHERE n.nspname = 'public'
                  AND t.relname = %s
                  AND i.indisunique
                  AND i.indpred IS NULL
                  AND i.indexprs IS NULL
                """,
                (table,),
            )
            for row in cur.fetchall():
                key = tuple(row[0] or ())
                if key and key != primary_key and key not in unique_keys:
                    unique_keys.append(key)

            result[table] = {
                "columns": columns,
                "primary_key": primary_key,
                "unique_keys": unique_keys,
            }
    return result


def _read_rows(
    conn: psycopg.Connection,
    table: str,
    columns: list[str],
    primary_key: tuple[str, ...],
) -> list[dict[str, Any]]:
    selected = sql.SQL(", ").join(sql.Identifier(name) for name in columns)
    ordering = sql.SQL(", ").join(sql.Identifier(name) for name in primary_key)
    statement = sql.SQL("SELECT {} FROM public.{} ORDER BY {}").format(
        selected, sql.Identifier(table), ordering
    )
    rows: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(statement)
        while batch := cur.fetchmany(BATCH_SIZE):
            rows.extend(dict(zip(columns, row)) for row in batch)
    return rows


def analyze_rows(
    source_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    primary_key: tuple[str, ...],
    target_unique_keys: list[tuple[str, ...]],
) -> dict[str, int]:
    """Classify rows by PK and detect destination unique-key collisions."""
    if not primary_key:
        raise ValueError("A primary key is required for safe row comparison")

    def pk(row: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(row[name] for name in primary_key)

    target_by_pk = {pk(row): row for row in target_rows}
    source_by_pk = {pk(row): row for row in source_rows}
    source_keys = set(source_by_pk)
    target_keys = set(target_by_pk)

    identical = 0
    different = 0
    for key in source_keys & target_keys:
        if source_by_pk[key] == target_by_pk[key]:
            identical += 1
        else:
            different += 1

    target_unique_maps: dict[tuple[str, ...], dict[tuple[Any, ...], set[tuple[Any, ...]]]] = {}
    for unique_key in target_unique_keys:
        lookup: dict[tuple[Any, ...], set[tuple[Any, ...]]] = {}
        for row in target_rows:
            value = tuple(row[name] for name in unique_key)
            # PostgreSQL UNIQUE constraints allow multiple NULL-containing keys.
            if any(item is None for item in value):
                continue
            lookup.setdefault(value, set()).add(pk(row))
        target_unique_maps[unique_key] = lookup

    unique_conflicts: set[tuple[Any, ...]] = set()
    for key in source_keys - target_keys:
        row = source_by_pk[key]
        for unique_key, lookup in target_unique_maps.items():
            value = tuple(row[name] for name in unique_key)
            if any(item is None for item in value):
                continue
            matching_target_keys = lookup.get(value, set())
            if matching_target_keys and key not in matching_target_keys:
                unique_conflicts.add(key)
                break

    return {
        "source_rows": len(source_rows),
        "target_rows": len(target_rows),
        "identical_same_pk": identical,
        "different_same_pk": different,
        "source_only_pk": len(source_keys - target_keys),
        "target_only_pk": len(target_keys - source_keys),
        "unique_conflict_rows": len(unique_conflicts),
    }


def main() -> int:
    source = None
    target = None
    try:
        source = _connect_from_env("SOURCE_PRODUCTION_DATABASE_URL")
        target = _connect_from_env("HEROKU_DATABASE_URL")

        source_identity = _database_identity(source)
        target_identity = _database_identity(target)
        if source_identity == target_identity:
            print("STOP: Source and destination identify as the same database.")
            return 2

        source_schema = _schema(source)
        target_schema = _schema(target)

        print("READ-ONLY POSTGRESQL MIGRATION AUDIT")
        print("Source and destination are connected with read-only transactions.")
        print("Personal row contents and key values will not be displayed.")
        print(f"Source database: {source_identity[0]}")
        print(f"Destination database: {target_identity[0]}")
        print(f"Source application tables: {len(source_schema)}")
        print(f"Destination application tables: {len(target_schema)}")

        schema_issues: list[str] = []
        row_conflicts = 0
        unique_conflicts = 0
        source_only_total = 0
        identical_total = 0

        for table in sorted(set(source_schema) | set(target_schema)):
            src = source_schema.get(table)
            dst = target_schema.get(table)
            if src is None:
                schema_issues.append(f"{table}: table exists only in Heroku")
                print(f"\n{table}: STOP — table exists only in destination")
                continue
            if dst is None:
                schema_issues.append(f"{table}: table missing from Heroku")
                print(f"\n{table}: STOP — table missing from destination")
                continue

            src_cols = src["columns"]
            dst_cols = dst["columns"]
            source_only = sorted(set(src_cols) - set(dst_cols))
            target_only = sorted(set(dst_cols) - set(src_cols))
            type_differences = sorted(
                name for name in set(src_cols) & set(dst_cols)
                if src_cols[name][:2] != dst_cols[name][:2]
            )

            if source_only or target_only or type_differences:
                if source_only:
                    schema_issues.append(f"{table}: source-only columns {source_only}")
                if target_only:
                    schema_issues.append(f"{table}: destination-only columns {target_only}")
                if type_differences:
                    schema_issues.append(f"{table}: column type differences {type_differences}")

            pk_src = src["primary_key"]
            pk_dst = dst["primary_key"]
            if not pk_src or pk_src != pk_dst:
                schema_issues.append(f"{table}: primary key missing or different")
                print(f"\n{table}: STOP — primary key missing or different")
                continue

            comparable_columns = [
                name for name in src_cols if name in dst_cols
            ]
            if not set(pk_src).issubset(comparable_columns):
                schema_issues.append(f"{table}: primary key columns are not comparable")
                continue

            source_rows = _read_rows(source, table, comparable_columns, pk_src)
            target_rows = _read_rows(target, table, comparable_columns, pk_src)
            summary = analyze_rows(
                source_rows,
                target_rows,
                pk_src,
                [
                    key for key in dst["unique_keys"]
                    if set(key).issubset(comparable_columns)
                ],
            )
            row_conflicts += summary["different_same_pk"]
            unique_conflicts += summary["unique_conflict_rows"]
            source_only_total += summary["source_only_pk"]
            identical_total += summary["identical_same_pk"]

            print(
                f"\n{table}: source={summary['source_rows']}, "
                f"Heroku={summary['target_rows']}, "
                f"same-PK-identical={summary['identical_same_pk']}, "
                f"same-PK-different={summary['different_same_pk']}, "
                f"source-only={summary['source_only_pk']}, "
                f"Heroku-only={summary['target_only_pk']}, "
                f"unique-conflicts={summary['unique_conflict_rows']}"
            )
            if source_only or target_only or type_differences:
                print(
                    f"  Schema differences: source-only columns={source_only or 'none'}; "
                    f"Heroku-only columns={target_only or 'none'}; "
                    f"type differences={type_differences or 'none'}"
                )

        print("\n=== SUMMARY ===")
        print("Identical same-PK rows:", identical_total)
        print("Source-only rows to consider:", source_only_total)
        print("Different rows sharing a primary key:", row_conflicts)
        print("Source rows colliding with Heroku unique keys:", unique_conflicts)
        print("Schema issues:", len(schema_issues))
        if schema_issues:
            print("SCHEMA REVIEW REQUIRED. No data was changed.")
        elif row_conflicts or unique_conflicts:
            print("DATA CONFLICTS REQUIRE REVIEW. No data was changed.")
        else:
            print("READ-ONLY AUDIT PASSED. This command did not modify either database.")
            print("A separate approved merge procedure is still required.")
        return 0

    except Exception as exc:
        # Do not print DSNs or exception messages, which may contain credentials.
        print(f"Audit stopped safely: {type(exc).__name__}. No data was changed.")
        return 1
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


if __name__ == "__main__":
    raise SystemExit(main())
