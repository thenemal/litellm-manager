"""Tests for database operations."""

import os
import tempfile
import pytest

os.environ["LTM_DB"] = os.path.join(tempfile.gettempdir(), "ltm_test.db")

from ltm.db import init_db, get_db


@pytest.fixture(autouse=True)
def fresh_db():
    """Use a fresh DB for each test."""
    db_path = os.environ["LTM_DB"]
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)


def test_init_creates_tables():
    with get_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = [r["name"] for r in tables]
    assert "maintenance_log" in names
    assert "config_snapshot" in names
    assert "health_check" in names


def test_insert_maintenance_log():
    with get_db() as conn:
        conn.execute(
            "INSERT INTO maintenance_log (category, title, description) VALUES (?, ?, ?)",
            ("update", "Test entry", "Description here"),
        )

    with get_db() as conn:
        row = conn.execute("SELECT * FROM maintenance_log").fetchone()

    assert row["title"] == "Test entry"
    assert row["category"] == "update"
    assert row["timestamp"] is not None


def test_insert_config_snapshot():
    with get_db() as conn:
        conn.execute(
            "INSERT INTO config_snapshot (source, file_path, content) VALUES (?, ?, ?)",
            ("test-source", "/etc/test", "file contents here"),
        )
        row = conn.execute("SELECT * FROM config_snapshot").fetchone()

    assert row["source"] == "test-source"
    assert row["content"] == "file contents here"


def test_insert_health_check():
    with get_db() as conn:
        conn.execute(
            "INSERT INTO health_check (service_up, http_status, response_ms) VALUES (?, ?, ?)",
            (1, 200, 42.5),
        )
        row = conn.execute("SELECT * FROM health_check").fetchone()

    assert row["service_up"] == 1
    assert row["http_status"] == 200
    assert row["response_ms"] == 42.5
