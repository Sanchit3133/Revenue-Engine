"""SQLite/PostgreSQL database access and additive, repeatable schema upgrades."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import psycopg
from app.config import settings


class DatabaseConfigurationError(RuntimeError):
    """The selected database backend is not safely configured."""


class DatabaseConnectionError(RuntimeError):
    """The configured database cannot be reached."""


INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT, place_id TEXT UNIQUE,
    business_name TEXT NOT NULL, address TEXT, phone TEXT, website TEXT,
    email TEXT, country TEXT NOT NULL DEFAULT 'India', qualification TEXT,
    score INTEGER DEFAULT 0, signal TEXT, notes TEXT,
    state TEXT NOT NULL DEFAULT 'DISCOVERED',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS outreach (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL,
    email TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
    sent_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provider TEXT, provider_message_id TEXT, last_error TEXT,
    retry_of_outreach_id INTEGER,
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    FOREIGN KEY (retry_of_outreach_id) REFERENCES outreach(id)
);
CREATE TABLE IF NOT EXISTS suppressions (
    email TEXT PRIMARY KEY, reason TEXT NOT NULL, source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS email_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,
    event_type TEXT NOT NULL, event_timestamp TEXT, reason TEXT,
    sg_event_id TEXT, sg_message_id TEXT, raw_event TEXT,
    provider TEXT, provider_event_id TEXT, provider_message_id TEXT,
    idempotency_key TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL,
    old_state TEXT, new_state TEXT NOT NULL, reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER, provider TEXT,
    provider_payment_id TEXT UNIQUE, amount INTEGER,
    currency TEXT DEFAULT 'INR', status TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, provider_order_id TEXT,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);
CREATE TABLE IF NOT EXISTS market_validations (
    country TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'RESEARCH_REQUIRED',
    payment_checked INTEGER NOT NULL DEFAULT 0,
    language_checked INTEGER NOT NULL DEFAULT 0,
    reachability_checked INTEGER NOT NULL DEFAULT 0,
    competition_checked INTEGER NOT NULL DEFAULT 0,
    demand_checked INTEGER NOT NULL DEFAULT 0,
    compliance_checked INTEGER NOT NULL DEFAULT 0,
    unit_economics_checked INTEGER NOT NULL DEFAULT 0,
    evidence_notes TEXT NOT NULL DEFAULT '', validated_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS outreach_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, outreach_id INTEGER NOT NULL,
    event_type TEXT NOT NULL, provider TEXT, reason TEXT, details_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (outreach_id) REFERENCES outreach(id)
);
CREATE TABLE IF NOT EXISTS consultation_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE,
    business_name TEXT NOT NULL, email TEXT NOT NULL, website TEXT,
    stage TEXT NOT NULL, consent_at TEXT NOT NULL, data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS client_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE,
    consultation_public_id TEXT, related_request_public_id TEXT,
    consent_at TEXT, business_name TEXT NOT NULL, email TEXT NOT NULL,
    request_type TEXT NOT NULL DEFAULT 'delivery', message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', draft_filename TEXT,
    draft_content TEXT, draft_content_type TEXT DEFAULT 'text/html',
    draft_source TEXT, draft_error TEXT, scope_confirmed INTEGER NOT NULL DEFAULT 0,
    payment_reference TEXT, approval_note TEXT, notification_status TEXT,
    delivery_message_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, approved_at TEXT,
    delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS consultation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, consultation_public_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (consultation_public_id) REFERENCES consultation_submissions(public_id)
);
CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
    object_type TEXT NOT NULL, object_public_id TEXT, details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS module_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT NOT NULL,
    status TEXT NOT NULL, summary TEXT NOT NULL, log_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL,
    resource_id TEXT, received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT, payload_json TEXT
);
CREATE TABLE IF NOT EXISTS reply_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE, from_email TEXT,
    subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL, budget_signal TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);
CREATE INDEX IF NOT EXISTS idx_leads_state ON leads(state);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_outreach_email ON outreach(email);
CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach(status);
CREATE INDEX IF NOT EXISTS idx_events_email ON email_events(email);
CREATE INDEX IF NOT EXISTS idx_state_events_lead ON state_events(lead_id);
CREATE INDEX IF NOT EXISTS idx_client_requests_status ON client_requests(status);
CREATE INDEX IF NOT EXISTS idx_client_requests_email ON client_requests(email);
CREATE INDEX IF NOT EXISTS idx_client_requests_consultation ON client_requests(consultation_public_id);
CREATE INDEX IF NOT EXISTS idx_consultation_messages_public_id ON consultation_messages(consultation_public_id, id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit(created_at);
CREATE INDEX IF NOT EXISTS idx_module_runs_created ON module_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_webhook_provider ON webhook_events(provider, event_type);
CREATE INDEX IF NOT EXISTS idx_reply_lead ON reply_events(lead_id, received_at);
"""

_MIGRATION_COLUMNS = {
    "leads": {"country": "TEXT NOT NULL DEFAULT 'India'"},
    "outreach": {
        "provider": "TEXT",
        "provider_message_id": "TEXT",
        "last_error": "TEXT",
        "retry_of_outreach_id": "INTEGER REFERENCES outreach(id)",
    },
    "email_events": {
        "provider": "TEXT",
        "provider_event_id": "TEXT",
        "provider_message_id": "TEXT",
        "idempotency_key": "TEXT",
    },
    "payments": {"provider_order_id": "TEXT"},
    "webhook_events": {"payload_json": "TEXT"},
}

_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_outreach_retry_source ON outreach(retry_of_outreach_id)",
    "CREATE INDEX IF NOT EXISTS idx_leads_country ON leads(country)",
    "CREATE INDEX IF NOT EXISTS idx_email_events_provider_msg ON email_events(provider, provider_message_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_events_idempotency ON email_events(idempotency_key) WHERE idempotency_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(provider, provider_order_id)",
    "CREATE INDEX IF NOT EXISTS idx_consultations_email ON consultation_submissions(email)",
    "CREATE INDEX IF NOT EXISTS idx_consultations_updated ON consultation_submissions(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_outreach_events_outreach ON outreach_events(outreach_id)",
)


class HybridRow(dict):
    """PostgreSQL row with sqlite.Row-compatible key and integer indexing."""

    def __init__(self, columns: list[str], values: tuple[Any, ...]):
        super().__init__(zip(columns, values))
        self._values = values

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def __iter__(self):
        return iter(self._values)


def _hybrid_row_factory(cursor: Any) -> Callable[[tuple[Any, ...]], HybridRow]:
    if cursor.description is None:
        return lambda values: values
    columns = [description.name for description in cursor.description]
    return lambda values: HybridRow(columns, values)


def _translate_sql(sql: str) -> str:
    """Translate the application's SQLite placeholders and ignore syntax for PG."""
    ignore_insert = bool(
        re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", sql, flags=re.IGNORECASE)
    )
    sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql, flags=re.IGNORECASE)
    out: list[str] = []
    index = 0
    quote: str | None = None
    line_comment = False
    block_comment = False
    while index < len(sql):
        char = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if line_comment:
            out.append(char)
            if char == "\n":
                line_comment = False
        elif block_comment:
            out.append(char)
            if char == "*" and following == "/":
                out.append(following)
                index += 1
                block_comment = False
        elif quote:
            out.append(char)
            if char == quote:
                if following == quote:
                    out.append(following)
                    index += 1
                else:
                    quote = None
        elif char == "-" and following == "-":
            out.extend((char, following))
            index += 1
            line_comment = True
        elif char == "/" and following == "*":
            out.extend((char, following))
            index += 1
            block_comment = True
        elif char in {"'", '"'}:
            quote = char
            out.append(char)
        elif char == "?":
            out.append("%s")
        else:
            out.append(char)
        index += 1

    translated = "".join(out)
    if ignore_insert:
        translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    # Application *_at columns remain text-compatible with the SQLite schema.
    if not re.search(r"\bTIMESTAMPTZ\b", translated, re.IGNORECASE):
        translated = re.sub(
            r"\bCURRENT_TIMESTAMP\b(?!\s*::)",
            "CURRENT_TIMESTAMP::text",
            translated,
            flags=re.IGNORECASE,
        )
    return translated


class PostgresConnection:
    """Small compatibility layer for the existing sqlite-style query API."""

    def __init__(self, raw: psycopg.Connection):
        self._raw = raw

    def __enter__(self) -> PostgresConnection:
        self._raw.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        return self._raw.__exit__(exc_type, exc, traceback)

    def execute(self, sql: str, params: Any = None) -> Any:
        return self._raw.execute(_translate_sql(sql), params)

    def executemany(self, sql: str, params: Any) -> Any:
        return self._raw.executemany(_translate_sql(sql), params)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


def get_database_backend() -> str:
    """Return the backend the app will actually use, rejecting unsafe prod fallback."""
    # Tests and callers that replace settings with a path-only object are SQLite-only.
    if not hasattr(settings, "database_backend"):
        return "sqlite"

    backend = settings.database_backend
    database_url = getattr(settings, "database_url", None)
    environment = getattr(settings, "environment", "development")
    if backend == "sqlite":
        if environment == "production":
            raise DatabaseConfigurationError("SQLite is not allowed as the production database backend")
        return "sqlite"
    if backend == "postgresql":
        if not database_url:
            raise DatabaseConfigurationError("DATABASE_URL is required for the PostgreSQL backend")
        return "postgresql"
    if database_url:
        return "postgresql"
    if environment == "production":
        raise DatabaseConfigurationError("DATABASE_URL is required in production; refusing SQLite fallback")
    return "sqlite"


def connect() -> Any:
    """Connect to the configured backend without silently falling back in production."""
    backend = get_database_backend()
    if backend == "sqlite":
        db_path = Path(getattr(settings, "db_path", "data/revenue_engine.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    database_url = getattr(settings, "database_url", None)
    try:
        parsed = urlsplit(database_url or "")
    except ValueError:
        raise DatabaseConfigurationError(
            "DATABASE_URL must be a valid PostgreSQL connection URL"
        ) from None
    if parsed.scheme not in {"postgres", "postgresql"} or not database_url:
        raise DatabaseConfigurationError("DATABASE_URL must be a valid PostgreSQL connection URL")
    try:
        raw = psycopg.connect(
            database_url,
            connect_timeout=8,
            row_factory=_hybrid_row_factory,
        )
    except Exception:
        # Driver exceptions may include the DSN; never include or chain them.
        raise DatabaseConnectionError("Could not connect to the configured PostgreSQL database") from None
    return PostgresConnection(raw)


def health_check() -> str:
    """Check connectivity and expected core schema, returning the active backend."""
    backend = get_database_backend()
    with connect() as conn:
        conn.execute("SELECT COUNT(*) FROM leads").fetchone()
    return backend


def _schema_statements(backend: str) -> list[str]:
    schema = SCHEMA
    if backend == "postgresql":
        schema = schema.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT",
            "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
        )
        schema = re.sub(
            r"(\b[a-z_]+_at\s+TEXT\s+NOT NULL DEFAULT\s+)CURRENT_TIMESTAMP",
            r"\1(CURRENT_TIMESTAMP::text)",
            schema,
            flags=re.IGNORECASE,
        )
    return [statement.strip() for statement in schema.split(";") if statement.strip()]


def _ensure_column(conn: Any, backend: str, table: str, column: str, definition: str) -> None:
    if backend == "postgresql":
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {definition}')
        return
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    """Apply additive schema changes in development; production uses Replit Publish."""
    backend = get_database_backend()
    if getattr(settings, "environment", "development") == "production":
        raise DatabaseConfigurationError(
            "Production schema changes are applied through Replit Publish, not runtime migrations"
        )

    with connect() as conn:
        if backend == "sqlite":
            conn.execute("BEGIN IMMEDIATE")
        for statement in _schema_statements(backend):
            conn.execute(statement)

        if backend == "postgresql":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        else:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

        for table, columns in _MIGRATION_COLUMNS.items():
            for column, definition in columns.items():
                _ensure_column(conn, backend, table, column, definition)
        for statement in _MIGRATION_INDEXES:
            conn.execute(statement)

        conn.execute(
            "INSERT INTO schema_migrations(version) VALUES (?) ON CONFLICT(version) DO NOTHING",
            (1,),
        )
        conn.commit()
