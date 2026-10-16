"""Copy a SQLite Revenue Engine database into an empty PostgreSQL database."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

from app.config import settings
from app.database import connect, get_database_backend, init_db

TABLES = (
    "leads",
    "suppressions",
    "market_validations",
    "consultation_submissions",
    "outreach",
    "email_events",
    "state_events",
    "payments",
    "outreach_events",
    "client_requests",
    "consultation_messages",
    "admin_audit",
    "module_runs",
    "webhook_events",
    "reply_events",
)
IGNORED_SOURCE_TABLES = {"schema_migrations"}
BATCH_SIZE = 500


class MigrationPrecheckError(ValueError):
    """A safe, user-actionable migration issue without connection details."""


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _source_table_names(source: sqlite3.Connection) -> set[str]:
    rows = source.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _source_columns(source: sqlite3.Connection, table: str) -> list[str]:
    return [
        row["name"]
        for row in source.execute(f"PRAGMA table_info({_quote_identifier(table)})")
    ]


def _target_columns(target: Any, table: str) -> list[str]:
    rows = target.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ? "
        "ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [row["column_name"] for row in rows]


def _source_counts(source: sqlite3.Connection, names: set[str]) -> dict[str, int]:
    counts = {}
    for table in TABLES:
        counts[table] = (
            source.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0]
            if table in names
            else 0
        )
    unexpected = names - set(TABLES) - IGNORED_SOURCE_TABLES
    for table in sorted(unexpected):
        count = source.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
        ).fetchone()[0]
        if count:
            raise MigrationPrecheckError(
                f"SQLite table {table!r} contains records outside the supported schema; "
                "nothing was copied."
            )
    return counts


def _target_counts(target: Any) -> dict[str, int]:
    return {
        table: target.execute(
            f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
        ).fetchone()[0]
        for table in TABLES
    }


def _copy_table(
    source: sqlite3.Connection,
    target: Any,
    table: str,
    columns: list[str],
) -> int:
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join("%s" for _ in columns)
    identity_override = " OVERRIDING SYSTEM VALUE" if "id" in columns else ""
    insert_sql = (
        f"INSERT INTO {_quote_identifier(table)} ({quoted_columns}){identity_override} "
        f"VALUES ({placeholders})"
    )

    cursor = source.execute(
        f"SELECT {quoted_columns} FROM {_quote_identifier(table)}"
    )
    copied = 0
    while rows := cursor.fetchmany(BATCH_SIZE):
        target.executemany(insert_sql, [tuple(row) for row in rows])
        copied += len(rows)

    if "id" in columns and copied:
        maximum = target.execute(
            f"SELECT MAX(id) FROM {_quote_identifier(table)}"
        ).fetchone()[0]
        target.execute(
            "SELECT setval(pg_get_serial_sequence(?, 'id'), ?, true)",
            (table, maximum),
        )
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=str(settings.db_path),
        help="Path to the source SQLite database (read-only; defaults to OUTREACH_DB).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy records. Without this flag the command only reports the migration plan.",
    )
    args = parser.parse_args()

    if settings.environment == "production":
        print("Refusing to run a data migration from a production environment.", file=sys.stderr)
        return 2
    try:
        if get_database_backend() != "postgresql":
            raise MigrationPrecheckError(
                "Set DATABASE_URL to PostgreSQL and use DATABASE_BACKEND=auto or postgresql."
            )
    except Exception as exc:
        print(f"Database configuration is not ready: {type(exc).__name__}", file=sys.stderr)
        return 2

    source_path = Path(args.sqlite_path)
    if not source_path.is_file():
        print(f"SQLite source file does not exist: {source_path}", file=sys.stderr)
        return 2

    uri = source_path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as source:
            source.row_factory = sqlite3.Row
            source_names = _source_table_names(source)
            counts = _source_counts(source, source_names)
            total = sum(counts.values())
            print(f"SQLite source: {source_path}")
            print(f"Records found: {total}")
            for table, count in counts.items():
                if count:
                    print(f"  {table}: {count}")

            if not args.apply:
                print("Dry run only. No records were written.")
                print("Review this plan, back up the SQLite file, then rerun with --apply.")
                return 0

            # Apply schema only to the development database. Production DDL is handled by Publish.
            init_db()
            with connect() as target:
                existing = _target_counts(target)
                occupied = {table: count for table, count in existing.items() if count}
                if occupied:
                    raise MigrationPrecheckError(
                        "PostgreSQL is not empty; refusing to overwrite or skip existing records. "
                        "No SQLite records were copied."
                    )

                target_column_map = {
                    table: _target_columns(target, table)
                    for table, count in counts.items()
                    if count
                }
                for table, count in counts.items():
                    if not count:
                        continue
                    source_columns = _source_columns(source, table)
                    unsupported = set(source_columns) - set(target_column_map[table])
                    if unsupported:
                        raise MigrationPrecheckError(
                            f"SQLite table {table!r} has columns not present in PostgreSQL; "
                            "no records were copied."
                        )

                copied = {}
                for table, count in counts.items():
                    if count:
                        copied[table] = _copy_table(
                            source,
                            target,
                            table,
                            _source_columns(source, table),
                        )
                if copied != {table: count for table, count in counts.items() if count}:
                    raise MigrationPrecheckError(
                        "The number of copied records did not match the source."
                    )

        print(f"Migration complete: {sum(copied.values())} records copied.")
        print("The SQLite source was opened read-only and was not changed.")
        return 0
    except MigrationPrecheckError as exc:
        print(f"Migration stopped safely: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Do not print exception messages or tracebacks; drivers can include a DSN.
        print(
            f"Migration stopped safely: {type(exc).__name__}. No source records were deleted.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
