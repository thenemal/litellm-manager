"""SQLite database for maintenance history, snapshots, and health checks."""

import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(os.environ.get("LTM_DB", "/root/litellm-manager/ltm.db"))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS maintenance_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                category    TEXT NOT NULL DEFAULT 'general',
                title       TEXT NOT NULL,
                description TEXT,
                tags        TEXT
            );

            CREATE TABLE IF NOT EXISTS config_snapshot (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                label       TEXT,
                source      TEXT NOT NULL,
                file_path   TEXT NOT NULL,
                content     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS health_check (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                service_up      INTEGER NOT NULL,
                http_status     INTEGER,
                response_ms     REAL,
                cpu_percent     REAL,
                mem_used_mb     REAL,
                mem_total_mb    REAL,
                disk_used_gb    REAL,
                disk_total_gb   REAL,
                error_summary   TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_log_timestamp ON maintenance_log(timestamp);
            CREATE INDEX IF NOT EXISTS idx_log_category ON maintenance_log(category);
            CREATE INDEX IF NOT EXISTS idx_snapshot_timestamp ON config_snapshot(timestamp);
            CREATE INDEX IF NOT EXISTS idx_snapshot_source ON config_snapshot(source);
            CREATE INDEX IF NOT EXISTS idx_health_timestamp ON health_check(timestamp);
        """)
