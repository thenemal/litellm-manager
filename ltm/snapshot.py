"""Configuration snapshot operations."""

import subprocess
from pathlib import Path

import yaml

from .config import SNAPSHOT_SOURCES, LITELLM_SERVICE, LITELLM_DIR
from .db import get_db


def take_snapshot(label: str | None = None) -> list[dict]:
    """Snapshot all configured files and store in DB. Returns list of captured files."""
    captured = []

    for source_name, file_path in SNAPSHOT_SOURCES.items():
        content = _read_file(file_path)
        if content is None:
            continue

        with get_db() as conn:
            conn.execute(
                "INSERT INTO config_snapshot (label, source, file_path, content) VALUES (?, ?, ?, ?)",
                (label, source_name, file_path, content),
            )
        captured.append({"source": source_name, "path": file_path, "size": len(content)})

    # Capture systemd service status
    status = _get_service_status()
    if status:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO config_snapshot (label, source, file_path, content) VALUES (?, ?, ?, ?)",
                (label, "service-status", f"systemctl status {LITELLM_SERVICE}", status),
            )
        captured.append({"source": "service-status", "path": "systemctl", "size": len(status)})

    # Capture installed packages via uv pip freeze
    pip_list = _get_uv_pip_freeze()
    if pip_list:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO config_snapshot (label, source, file_path, content) VALUES (?, ?, ?, ?)",
                (label, "uv-packages", "uv pip freeze", pip_list),
            )
        captured.append({"source": "uv-packages", "path": "uv pip freeze", "size": len(pip_list)})

    return captured


def list_snapshots(limit: int = 20) -> list[dict]:
    """List recent snapshot groups by timestamp."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT timestamp, label, COUNT(*) as file_count
            FROM config_snapshot
            GROUP BY timestamp
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_snapshot_detail(timestamp: str) -> list[dict]:
    """Get all files from a specific snapshot timestamp."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT source, file_path, length(content) as size FROM config_snapshot WHERE timestamp = ?",
            (timestamp,),
        ).fetchall()
    return [dict(r) for r in rows]


def diff_snapshots(source: str, ts1: str, ts2: str) -> dict:
    """Compare a specific source between two snapshot timestamps."""
    with get_db() as conn:
        r1 = conn.execute(
            "SELECT content FROM config_snapshot WHERE source = ? AND timestamp = ?",
            (source, ts1),
        ).fetchone()
        r2 = conn.execute(
            "SELECT content FROM config_snapshot WHERE source = ? AND timestamp = ?",
            (source, ts2),
        ).fetchone()

    if not r1 or not r2:
        return {"error": "One or both snapshots not found for that source."}

    import difflib
    diff = list(difflib.unified_diff(
        r1["content"].splitlines(keepends=True),
        r2["content"].splitlines(keepends=True),
        fromfile=f"{source} @ {ts1}",
        tofile=f"{source} @ {ts2}",
    ))
    return {
        "source": source,
        "from": ts1,
        "to": ts2,
        "changed": len(diff) > 0,
        "diff": "".join(diff) if diff else "(no changes)",
    }


def export_snapshot_yaml(timestamp: str) -> str:
    """Export a full snapshot as YAML."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT source, file_path, content FROM config_snapshot WHERE timestamp = ?",
            (timestamp,),
        ).fetchall()

    data = {
        "snapshot_timestamp": timestamp,
        "files": {row["source"]: {"path": row["file_path"], "content": row["content"]} for row in rows},
    }
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def _read_file(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except Exception:
        return None


def _get_service_status() -> str | None:
    try:
        r = subprocess.run(
            ["systemctl", "status", LITELLM_SERVICE, "--no-pager"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout
    except Exception:
        return None


def _get_uv_pip_freeze() -> str | None:
    try:
        r = subprocess.run(
            ["uv", "pip", "freeze", "--directory", LITELLM_DIR],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip()
    except Exception:
        return None
