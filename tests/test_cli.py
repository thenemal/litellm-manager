"""Tests for CLI commands."""

import os
import tempfile
import subprocess

os.environ["LTM_DB"] = os.path.join(tempfile.gettempdir(), "ltm_test_cli.db")

import pytest
from ltm.db import init_db, get_db


@pytest.fixture(autouse=True)
def fresh_db():
    db_path = os.environ["LTM_DB"]
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)


def run_ltm(*args):
    env = os.environ.copy()
    result = subprocess.run(
        ["python3", "-m", "ltm.cli", *args],
        capture_output=True, text=True, env=env,
    )
    return result


def test_add_note():
    r = run_ltm("add-note", "Test note", "-c", "update", "-d", "Details here")
    assert r.returncode == 0
    assert "Test note" in r.stdout


def test_history_empty():
    r = run_ltm("history")
    assert r.returncode == 0
    assert "No maintenance entries" in r.stdout


def test_history_with_entries():
    run_ltm("add-note", "Note 1")
    run_ltm("add-note", "Note 2", "-c", "incident")
    r = run_ltm("history")
    assert "Note 1" in r.stdout
    assert "Note 2" in r.stdout


def test_history_filter_category():
    run_ltm("add-note", "General note")
    run_ltm("add-note", "Incident note", "-c", "incident")
    r = run_ltm("history", "-c", "incident")
    assert "Incident note" in r.stdout
    assert "General note" not in r.stdout


def test_version():
    r = run_ltm("--version")
    assert "0.1.0" in r.stdout
