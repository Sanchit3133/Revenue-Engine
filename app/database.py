"""SQLite storage and additive, idempotent schema migrations.

Migrations never delete existing lead, outreach, event, or payment records.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT UNIQUE,
    business_name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    website TEXT,
    email TEXT,
    country TEXT NOT NULL DEFAULT 'India',
    qualification TEXT,
    score INTEGER DEFAULT 0,
    signal TEXT,
    notes TEXT,
    state TEXT NOT NULL DEFAULT 'DISCOVERED',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS outreach (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provider TEXT,
    provider_message_id TEXT,
    last_error TEXT,
    retry_of_outreach_id INTEGER,
    FOREIGN KEY (lead_id) REFERENCES leads(id),
    FOREIGN KEY (retry_of_outreach_id) REFERENCES outreach(id)
);
CREATE TABLE IF NOT EXISTS suppressions (
    email TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS email_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT,
    event_type TEXT NOT NULL,
    event_timestamp TEXT,
    reason TEXT,
    sg_event_id TEXT,
    sg_message_id TEXT,
    raw_event TEXT,
    provider TEXT,
    provider_event_id TEXT,
    provider_message_id TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    old_state TEXT,
    new_state TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER,
    provider TEXT,
    provider_payment_id TEXT UNIQUE,
    amount INTEGER,
    currency TEXT DEFAULT 'INR',
    status TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provider_order_id TEXT,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);
CREATE TABLE IF NOT EXISTS market_validations (
    country TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'RESEARCH_REQUIRED',
    payment_checked INTEGER NOT NULL DEFAULT 0,
    language_checked INTEGER NOT NULL DEFAULT 0,
    reachability_checked INTEGER NOT NULL DEFAULT 0,
    competition_checked INTEGER NOT NULL DEFAULT 0,
    demand_checked INTEGER NOT NULL DEFAULT 0,
    compliance_checked INTEGER NOT NULL DEFAULT 0,
    unit_economics_checked INTEGER NOT NULL DEFAULT 0,
    evidence_notes TEXT NOT NULL DEFAULT '',
    validated_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_leads_state ON leads(state);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_outreach_email ON outreach(email);
CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach(status);
CREATE INDEX IF NOT EXISTS idx_events_email ON email_events(email);
CREATE INDEX IF NOT EXISTS idx_state_events_lead ON state_events(lead_id);
CREATE TABLE IF NOT EXISTS outreach_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outreach_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    provider TEXT,
    reason TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (outreach_id) REFERENCES outreach(id)
);
CREATE TABLE IF NOT EXISTS consultation_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    business_name TEXT NOT NULL,
    email TEXT NOT NULL,
    website TEXT,
    stage TEXT NOT NULL,
    consent_at TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS client_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    consultation_public_id TEXT,
    related_request_public_id TEXT,
    consent_at TEXT,
    business_name TEXT NOT NULL,
    email TEXT NOT NULL,
    request_type TEXT NOT NULL DEFAULT 'delivery',
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    draft_filename TEXT,
    draft_content TEXT,
    draft_content_type TEXT DEFAULT 'text/html',
    draft_source TEXT,
    draft_error TEXT,
    scope_confirmed INTEGER NOT NULL DEFAULT 0,
    payment_reference TEXT,
    approval_note TEXT,
    notification_status TEXT,
    delivery_message_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT,
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_client_requests_status ON client_requests(status);
CREATE INDEX IF NOT EXISTS idx_client_requests_email ON client_requests(email);
CREATE INDEX IF NOT EXISTS idx_client_requests_consultation ON client_requests(consultation_public_id);
CREATE TABLE IF NOT EXISTS consultation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consultation_public_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (consultation_public_id) REFERENCES consultation_submissions(public_id)
);
CREATE INDEX IF NOT EXISTS idx_consultation_messages_public_id ON consultation_messages(consultation_public_id, id);
CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_public_id TEXT,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit(created_at);
CREATE TABLE IF NOT EXISTS module_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    log_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_module_runs_created ON module_runs(created_at);
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    resource_id TEXT,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT,
    payload_json TEXT
);
CREATE TABLE IF NOT EXISTS reply_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    from_email TEXT,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL,
    budget_signal TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);
CREATE INDEX IF NOT EXISTS idx_outreach_events_outreach ON outreach_events(outreach_id);
CREATE INDEX IF NOT EXISTS idx_consultations_email ON consultation_submissions(email);
CREATE INDEX IF NOT EXISTS idx_consultations_updated ON consultation_submissions(updated_at);
CREATE INDEX IF NOT EXISTS idx_webhook_provider ON webhook_events(provider, event_type);
CREATE INDEX IF NOT EXISTS idx_reply_lead ON reply_events(lead_id, received_at);
"""


def connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    """Create all tables and apply safe additive migrations to existing DBs."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        migrations = {
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
        for table, columns in migrations.items():
            for column, definition in columns.items():
                _ensure_column(conn, table, column, definition)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_outreach_retry_source ON outreach(retry_of_outreach_id);
            CREATE INDEX IF NOT EXISTS idx_leads_country ON leads(country);
            CREATE INDEX IF NOT EXISTS idx_email_events_provider_msg ON email_events(provider, provider_message_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_email_events_idempotency
                ON email_events(idempotency_key) WHERE idempotency_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(provider, provider_order_id);
            CREATE INDEX IF NOT EXISTS idx_consultations_email ON consultation_submissions(email);
            CREATE INDEX IF NOT EXISTS idx_consultations_updated ON consultation_submissions(updated_at);
            CREATE TABLE IF NOT EXISTS outreach_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                outreach_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                provider TEXT,
                reason TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (outreach_id) REFERENCES outreach(id)
            );
            CREATE INDEX IF NOT EXISTS idx_outreach_events_outreach ON outreach_events(outreach_id);
            CREATE TABLE IF NOT EXISTS consultation_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE,
                business_name TEXT NOT NULL,
                email TEXT NOT NULL,
                website TEXT,
                stage TEXT NOT NULL,
                consent_at TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_consultations_email ON consultation_submissions(email);
            CREATE INDEX IF NOT EXISTS idx_consultations_updated ON consultation_submissions(updated_at);
                        CREATE TABLE IF NOT EXISTS client_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE,
                consultation_public_id TEXT,
                related_request_public_id TEXT,
                consent_at TEXT,
                business_name TEXT NOT NULL,
                email TEXT NOT NULL,
                request_type TEXT NOT NULL DEFAULT 'delivery',
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                draft_filename TEXT,
                draft_content TEXT,
                draft_content_type TEXT DEFAULT 'text/html',
                draft_source TEXT,
                draft_error TEXT,
                scope_confirmed INTEGER NOT NULL DEFAULT 0,
                payment_reference TEXT,
                approval_note TEXT,
                notification_status TEXT,
                delivery_message_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                approved_at TEXT,
                delivered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_client_requests_status ON client_requests(status);
            CREATE INDEX IF NOT EXISTS idx_client_requests_email ON client_requests(email);
            CREATE INDEX IF NOT EXISTS idx_client_requests_consultation ON client_requests(consultation_public_id);
                        CREATE TABLE IF NOT EXISTS consultation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                consultation_public_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (consultation_public_id) REFERENCES consultation_submissions(public_id)
            );
            CREATE INDEX IF NOT EXISTS idx_consultation_messages_public_id ON consultation_messages(consultation_public_id, id);
CREATE TABLE IF NOT EXISTS admin_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_public_id TEXT,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit(created_at);
            CREATE TABLE IF NOT EXISTS module_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                log_text TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_module_runs_created ON module_runs(created_at);
CREATE TABLE IF NOT EXISTS webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                resource_id TEXT,
                received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at TEXT,
                payload_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_webhook_provider ON webhook_events(provider, event_type);
            CREATE TABLE IF NOT EXISTS reply_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                from_email TEXT,
                subject TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                intent TEXT NOT NULL,
                budget_signal TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_reply_lead ON reply_events(lead_id, received_at);
            """
        )
        conn.commit()
