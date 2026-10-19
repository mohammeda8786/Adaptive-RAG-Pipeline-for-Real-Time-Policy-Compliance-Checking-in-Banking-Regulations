from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("RBI_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.getenv("RBI_DATABASE_PATH", DATA_DIR / "rbi_chat.sqlite3"))
REPORT_DIR = Path(os.getenv("RBI_REPORT_DIR", DATA_DIR / "reports"))
POLICY_DIR = Path(os.getenv("RBI_POLICY_DIR", DATA_DIR / "bank_policies"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize_storage() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources_json TEXT NOT NULL DEFAULT '[]',
                report_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policies (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                display_name TEXT NOT NULL,
                text_content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS message_feedback (
                message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                rating TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                conversation_id TEXT REFERENCES conversations(id),
                title TEXT NOT NULL,
                report_type TEXT NOT NULL,
                status TEXT NOT NULL,
                output_format TEXT NOT NULL,
                source_docs_json TEXT NOT NULL DEFAULT '[]',
                policy_id TEXT,
                request_json TEXT NOT NULL,
                result_json TEXT,
                file_path TEXT,
                file_size INTEGER,
                finding_count INTEGER,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id, id);
            CREATE INDEX IF NOT EXISTS idx_reports_created
                ON reports(created_at DESC);
            """
        )


def json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def conversation_exists(connection: sqlite3.Connection, conversation_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone() is not None


def add_message(
    connection: sqlite3.Connection,
    conversation_id: str,
    role: str,
    content: str,
    sources: list[dict[str, Any]] | None = None,
    report_id: str | None = None,
) -> int:
    now = utc_now()
    if not conversation_exists(connection, conversation_id):
        title = content.strip().replace("\n", " ")[:72] or "New conversation"
        connection.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conversation_id, title, now, now),
        )
    else:
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )

    cursor = connection.execute(
        "INSERT INTO messages (conversation_id, role, content, sources_json, report_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (conversation_id, role, content, json.dumps(sources or []), report_id, now),
    )
    return int(cursor.lastrowid)


def serialize_report(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "title": row["title"],
        "report_type": row["report_type"],
        "status": row["status"],
        "format": row["output_format"],
        "source_documents": json_load(row["source_docs_json"], []),
        "policy_id": row["policy_id"],
        "result": json_load(row["result_json"], None),
        "file_size": row["file_size"],
        "finding_count": row["finding_count"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }