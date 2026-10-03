"""LiteLLM update operations via uv.

Follows the official uv/venv upgrade procedure:
https://docs.litellm.ai/docs/troubleshoot/pip_venv_upgrade
stop proxy -> pg_dump -> install litellm[proxy]==X -> prisma generate ->
prisma migrate deploy -> migrate status -> start proxy.
"""

import glob
import json
import os
import re
import subprocess
import time
import urllib.request

from .config import (
    BACKUP_DIR, LITELLM_DIR, LITELLM_ENV, LITELLM_SERVICE, LITELLM_YAML, PYPI_URL,
)
from .db import get_db
from .health import check_service_status, check_http


def _pkg_version(pkg: str) -> str | None:
    """Return the installed version of a package in the litellm venv, or None."""
    try:
        result = subprocess.run(
            ["uv", "pip", "show", pkg, "--directory", LITELLM_DIR],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def get_current_version() -> dict:
    """Get current litellm (and proxy-extras) versions from the venv."""
    return {
        "version": _pkg_version("litellm"),
        "proxy_extras": _pkg_version("litellm-proxy-extras"),
    }


def get_latest_version() -> str:
    """Return the latest litellm release on PyPI (raises on failure)."""
    with urllib.request.urlopen(PYPI_URL, timeout=15) as resp:
        return json.load(resp)["info"]["version"]


def check_for_updates() -> dict:
    """Check PyPI for a newer litellm release.

    Only litellm itself counts — dependency drift (e.g. a markupsafe patch)
    is not an update. Returns dict with keys: up_to_date, current_version,
    latest_version, error.
    """
    current_ver = get_current_version().get("version")
    try:
        latest = get_latest_version()
    except Exception as e:
        return {"up_to_date": None, "current_version": current_ver,
                "latest_version": None, "error": str(e)}
    return {
        "up_to_date": current_ver == latest,
        "current_version": current_ver,
        "latest_version": latest,
        "error": None,
    }


def _run_step(cmd: list[str], label: str, cwd: str = LITELLM_DIR, timeout: int = 300,
              env: dict | None = None) -> dict:
    """Run a subprocess step, return result dict."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env,
        )
        return {
            "step": label,
            "success": result.returncode == 0,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"step": label, "success": False, "stderr": f"Timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"step": label, "success": False, "stderr": str(e), "returncode": -1}


def _find_prisma_schema() -> str | None:
    """Locate the prisma schema shipped in litellm_proxy_extras.

    Per the LiteLLM docs this is the source of truth for both the client and
    the migrations (its migrations/ directory sits next to it).
    """
    matches = glob.glob(os.path.join(
        LITELLM_DIR, ".venv", "lib", "python*", "site-packages",
        "litellm_proxy_extras", "schema.prisma",
    ))
    return matches[0] if matches else None


def _database_url() -> str | None:
    """Resolve the DB URL prisma needs, from the .env or litellm.yaml."""
    try:
        with open(LITELLM_ENV) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    try:
        with open(LITELLM_YAML) as f:
            m = re.search(r"database_url:\s*(\S+)", f.read())
            if m:
                return m.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _prisma_step(args: list[str], timeout: int = 600) -> dict:
    """Run `prisma <args> --schema <proxy-extras schema>` inside the venv.

    The venv's bin is prepended to PATH so prisma writes the client into the
    venv rather than the system site-packages, and DATABASE_URL is supplied
    because the schema references it via env(). The generous timeout covers
    the one-time query-engine download after a Prisma version bump.
    """
    label = "prisma " + " ".join(args)
    schema = _find_prisma_schema()
    if not schema:
        return {"step": label, "success": False,
                "stderr": "litellm_proxy_extras/schema.prisma not found under the venv"}

    venv_bin = os.path.join(LITELLM_DIR, ".venv", "bin")
    python = os.path.join(venv_bin, "python")
    env = os.environ.copy()
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    db_url = _database_url()
    if db_url:
        env["DATABASE_URL"] = db_url

    return _run_step([python, "-m", "prisma", *args, "--schema", schema],
                     label, timeout=timeout, env=env)


def _backup_db_step(version: str | None) -> dict:
    """pg_dump the LiteLLM database (custom format) into BACKUP_DIR."""
    label = "pg_dump backup"
    db_url = _database_url()
    if not db_url:
        return {"step": label, "success": False, "stderr": "DATABASE_URL not found"}
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(
        BACKUP_DIR, f"pre-{version or 'unknown'}-{time.strftime('%Y%m%d-%H%M%S')}.dump")
    # libpq ignores the ?schema= param prisma uses, so strip query params.
    step = _run_step(["pg_dump", "-F", "c", "-f", path, db_url.split("?", 1)[0]],
                     label, timeout=600)
    if step["success"]:
        step["detail"] = path
    return step


def _wait_for_healthy(timeout: int = 600, interval: int = 5) -> bool:
    """Poll service health until up or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_service_status():
            http_status, _ = check_http()
            if http_status and http_status < 500:
                return True
        time.sleep(interval)
    return False


def run_update(dry_run: bool = False, version: str | None = None,
               backup: bool = True) -> dict:
    """Perform the full update workflow.

    `version` pins the target release (default: latest on PyPI). Re-running
    with the current version reinstalls it and repairs companions.
    Returns a result dict with success, versions, steps, and error info.
    """
    from .snapshot import take_snapshot

    result = {
        "success": False,
        "dry_run": dry_run,
        "old_version": None,
        "new_version": None,
        "target_version": None,
        "steps": [],
        "error": None,
    }

    old = get_current_version()
    result["old_version"] = old

    if version:
        target = version
    else:
        info = check_for_updates()
        result["update_info"] = info
        if info["error"]:
            result["error"] = f"Could not check PyPI: {info['error']}"
            return result
        if info["up_to_date"]:
            result["success"] = True
            result["up_to_date"] = True
            return result
        target = info["latest_version"]
    result["target_version"] = target

    spec = f"litellm[proxy]=={target}"
    if dry_run:
        step = _run_step(
            ["uv", "pip", "install", "--dry-run", spec, "--directory", LITELLM_DIR],
            f"uv pip install --dry-run {spec}", timeout=120,
        )
        step["detail"] = (step.get("stdout", "") + step.get("stderr", "")).strip()
        result["steps"].append(step)
        result["success"] = step["success"]
        return result

    def fail(msg: str, service_stopped: bool = False) -> dict:
        result["error"] = msg
        if service_stopped:
            result["error"] += " (service left stopped)"
        _log_update_failure(result, old)
        return result

    # Pre-update snapshot
    try:
        take_snapshot(label="pre-update")
        result["steps"].append({"step": "pre-update snapshot", "success": True})
    except Exception as e:
        result["steps"].append({"step": "pre-update snapshot", "success": False, "stderr": str(e)})

    # 1. Stop the proxy
    step = _run_step(["systemctl", "stop", LITELLM_SERVICE], "stop service", timeout=90)
    result["steps"].append(step)
    if not step["success"]:
        return fail(f"Service stop failed: {step['stderr']}")

    def restart_old(msg: str) -> dict:
        # Nothing has changed yet, so bring the old version back up.
        _run_step(["systemctl", "start", LITELLM_SERVICE], "start service", timeout=30)
        return fail(msg)

    # 2. Back up the database
    if backup:
        step = _backup_db_step(old.get("version"))
        result["steps"].append(step)
        if not step["success"]:
            return restart_old(f"Database backup failed: {step['stderr']}")
        result["backup_path"] = step["detail"]

    # 3. Install litellm[proxy] pinned — the extra pins the matching
    # litellm-proxy-extras and pulls proxy-only deps (e.g. httpx2).
    step = _run_step(["uv", "pip", "install", spec, "--directory", LITELLM_DIR],
                     f"uv pip install {spec}", timeout=600)
    result["steps"].append(step)
    if not step["success"]:
        return restart_old(f"uv pip install failed: {step['stderr']}")

    # 4. Regenerate the Prisma client from the new schema
    step = _prisma_step(["generate"])
    result["steps"].append(step)
    if not step["success"]:
        return fail(f"prisma generate failed: {step['stderr']}", service_stopped=True)

    # 5. Apply migrations before startup so failures surface here
    step = _prisma_step(["migrate", "deploy"])
    result["steps"].append(step)
    if not step["success"]:
        return fail(f"prisma migrate deploy failed: {step['stderr'] or step.get('stdout', '')}",
                    service_stopped=True)

    step = _prisma_step(["migrate", "status"], timeout=120)
    result["steps"].append(step)
    if not step["success"]:
        return fail(f"prisma migrate status reports problems: {step.get('stdout') or step['stderr']}",
                    service_stopped=True)

    # 6. Start the proxy and wait for it
    step = _run_step(["systemctl", "start", LITELLM_SERVICE], "start service", timeout=30)
    result["steps"].append(step)
    if not step["success"]:
        return fail(f"Service start failed: {step['stderr']}")

    healthy = _wait_for_healthy()
    result["steps"].append({"step": "health check", "success": healthy})
    if not healthy:
        return fail("Service did not become healthy after start")

    new = get_current_version()
    result["new_version"] = new

    try:
        take_snapshot(label="post-update")
        result["steps"].append({"step": "post-update snapshot", "success": True})
    except Exception as e:
        result["steps"].append({"step": "post-update snapshot", "success": False, "stderr": str(e)})

    old_ver = old.get("version") or "?"
    new_ver = new.get("version") or "?"
    old_pe = old.get("proxy_extras") or "?"
    new_pe = new.get("proxy_extras") or "?"
    backup_line = f"DB backup: {result['backup_path']}\n" if result.get("backup_path") else ""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO maintenance_log (category, title, description, tags) VALUES (?, ?, ?, ?)",
            ("update", f"Updated LiteLLM {old_ver} -> {new_ver}",
             f"Automated update completed successfully.\n"
             f"litellm: {old_ver} -> {new_ver}\n"
             f"litellm-proxy-extras: {old_pe} -> {new_pe}\n"
             f"{backup_line}"
             f"Prisma client regenerated; migrations deployed.",
             "automated,update"),
        )

    result["success"] = True
    return result


def _log_update_failure(result: dict, old_version: dict):
    """Log a failed update attempt to maintenance_log."""
    old_ver = old_version.get("version") or "?"
    error = result.get("error", "Unknown error")

    with get_db() as conn:
        conn.execute(
            "INSERT INTO maintenance_log (category, title, description, tags) VALUES (?, ?, ?, ?)",
            ("update", f"Update FAILED at {old_ver}",
             f"Error: {error}",
             "automated,update,failed"),
        )
