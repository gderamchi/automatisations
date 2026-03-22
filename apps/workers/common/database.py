from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from apps.workers.common.settings import Settings, ensure_runtime_directories, get_settings
from apps.workers.common.time import utcnow_iso


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_kind TEXT NOT NULL,
    source_name TEXT,
    document_type TEXT,
    supplier_name TEXT,
    supplier_siret TEXT,
    invoice_number TEXT,
    invoice_date TEXT,
    due_date TEXT,
    currency TEXT DEFAULT 'EUR',
    net_amount TEXT,
    vat_amount TEXT,
    gross_amount TEXT,
    project_ref TEXT,
    confidence REAL DEFAULT 0,
    current_stage TEXT NOT NULL DEFAULT 'ingested',
    validation_status TEXT NOT NULL DEFAULT 'pending',
    export_status TEXT NOT NULL DEFAULT 'pending',
    archived_path TEXT,
    normalized_payload_json TEXT,
    validated_payload_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    file_role TEXT NOT NULL DEFAULT 'original',
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    mime_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ocr_extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    normalized_payload_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS validation_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    extracted_payload_json TEXT NOT NULL,
    corrected_payload_json TEXT,
    validator_name TEXT,
    validation_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS supplier_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL UNIQUE,
    supplier_match TEXT NOT NULL,
    compte_charge TEXT NOT NULL,
    compte_tva TEXT NOT NULL,
    compte_tiers TEXT NOT NULL,
    journal TEXT NOT NULL,
    confidence_threshold REAL NOT NULL DEFAULT 0.75,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounting_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    entry_group_id TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    journal TEXT NOT NULL,
    account_code TEXT NOT NULL,
    debit TEXT NOT NULL DEFAULT '0',
    credit TEXT NOT NULL DEFAULT '0',
    label TEXT NOT NULL,
    reference TEXT,
    entry_date TEXT NOT NULL,
    export_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interfast_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    updated_at_remote TEXT,
    payload_json TEXT NOT NULL,
    synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, external_id)
);

CREATE TABLE IF NOT EXISTS bank_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    booking_date TEXT NOT NULL,
    value_date TEXT,
    label TEXT NOT NULL,
    reference TEXT,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    status TEXT NOT NULL DEFAULT 'pending',
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bank_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_transaction_id INTEGER NOT NULL REFERENCES bank_transactions(id) ON DELETE CASCADE,
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    score REAL NOT NULL,
    outcome TEXT NOT NULL,
    rationale_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS doe_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_project_id TEXT UNIQUE,
    project_code TEXT,
    project_name TEXT NOT NULL,
    base_path TEXT,
    completeness_status TEXT NOT NULL DEFAULT 'unknown',
    expected_documents_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    related_type TEXT,
    related_id TEXT,
    provider_message_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processed_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processed_key TEXT NOT NULL UNIQUE,
    mailbox_uid TEXT NOT NULL,
    message_id TEXT,
    sender TEXT,
    subject TEXT,
    attachment_index INTEGER NOT NULL,
    attachment_filename TEXT NOT NULL,
    attachment_sha256 TEXT NOT NULL,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS worker_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    error_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_stage ON documents(current_stage, validation_status);
CREATE INDEX IF NOT EXISTS idx_document_files_document_id ON document_files(document_id);
CREATE INDEX IF NOT EXISTS idx_validation_tasks_status ON validation_tasks(status);
CREATE INDEX IF NOT EXISTS idx_accounting_entries_export_status ON accounting_entries(export_status);
CREATE INDEX IF NOT EXISTS idx_interfast_entities_type ON interfast_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_bank_transactions_status ON bank_transactions(status);
CREATE INDEX IF NOT EXISTS idx_bank_matches_transaction ON bank_matches(bank_transaction_id);
CREATE INDEX IF NOT EXISTS idx_processed_emails_mailbox_uid ON processed_emails(mailbox_uid);
"""


def get_connection(settings: Settings | None = None) -> sqlite3.Connection:
    current = settings or get_settings()
    ensure_runtime_directories(current)
    connection = sqlite3.connect(current.db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(settings: Settings | None = None) -> None:
    current = settings or get_settings()
    ensure_runtime_directories(current)
    with get_connection(current) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


@contextmanager
def job_run(connection: sqlite3.Connection, job_name: str, details_json: str = "{}") -> Iterator[int]:
    cursor = connection.execute(
        """
        INSERT INTO job_runs(job_name, status, started_at, details_json)
        VALUES (?, 'running', ?, ?)
        """,
        (job_name, utcnow_iso(), details_json),
    )
    job_id = int(cursor.lastrowid)
    connection.commit()
    try:
        yield job_id
    except Exception as exc:
        connection.execute(
            """
            UPDATE job_runs
            SET status = 'failed', finished_at = ?, error_text = ?
            WHERE id = ?
            """,
            (utcnow_iso(), str(exc), job_id),
        )
        connection.commit()
        raise
    else:
        connection.execute(
            """
            UPDATE job_runs
            SET status = 'completed', finished_at = ?
            WHERE id = ?
            """,
            (utcnow_iso(), job_id),
        )
        connection.commit()
