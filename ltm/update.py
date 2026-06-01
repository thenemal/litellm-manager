"""LiteLLM update operations via uv."""

import glob
import os
import re
import subprocess
import time

from .config import LITELLM_DIR, LITELLM_ENV, LITELLM_SERVICE, LITELLM_YAML
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


def check_for_updates() -> dict:
    """Check PyPI for a newer version of litellm.

    Returns dict with keys: up_to_date, current_version, latest_version, error.
    """
    current = get_current_version()
    current_ver = current.get("version")

    try:
        result = subprocess.run(
            ["uv", "pip", "install", "--dry-run", "--upgrade", "litellm",
             "--directory", LITELLM_DIR],
            capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr

        # If "Would install" or "Would upgrade" appears, there's an update
        # "Would make no changes" means already up to date
        has_update = ("Would install" in output or "Would upgrade" in output) and "Would make no changes" not in output
        if has_update:
            return {
                "up_to_date": False,
                "current_version": current_ver,
                "detail": output.strip(),
                "error": None,
            }
        else:
            return {
                "up_to_date": True,
                "current_version": current_ver,
                "detail": output.strip(),
                "error": None,
            }
    except Exception as e:
        return {"up_to_date": None, "current_version": current_ver, "error": str(e)}


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
    """Locate litellm's runtime prisma schema inside the venv."""
    matches = glob.glob(os.path.join(
        LITELLM_DIR, ".venv", "lib", "python*", "site-packages",
        "litellm", "proxy", "schema.prisma",
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


def _prisma_generate_step() -> dict:
    """Regenerate the Prisma client into the venv so it matches the new schema.

    The venv's bin is prepended to PATH so prisma writes the client into the
    venv rather than the system site-packages, and DATABASE_URL is supplied
    because the schema references it via env() even for `generate`.
    """
    schema = _find_prisma_schema()
    if not schema:
        return {"step": "prisma generate", "success": False,
                "stderr": "litellm prisma schema.prisma not found under the venv"}

    venv_bin = os.path.join(LITELLM_DIR, ".venv", "bin")
    python = os.path.join(venv_bin, "python")
    env = os.environ.copy()
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    db_url = _database_url()
    if db_url:
        env["DATABASE_URL"] = db_url

    return _run_step(
        [python, "-m", "prisma", "generate", "--schema", schema],
        "prisma generate", timeout=180, env=env,
    )


def _wait_for_healthy(timeout: int = 60, interval: int = 5) -> bool:
    """Poll service health until up or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_service_status():
            http_status, _ = check_http()
            if http_status and http_status < 500:
                return True
        time.sleep(interval)
    return False


def run_update(dry_run: bool = False) -> dict:
    """Perform the full update workflow.

    Returns a result dict with success, versions, steps, and error info.
    """
    from .snapshot import take_snapshot

    result = {
        "success": False,
        "dry_run": dry_run,
        "old_version": None,
        "new_version": None,
        "steps": [],
        "error": None,
    }

    # Record old version
    old = get_current_version()
    result["old_version"] = old

    if dry_run:
        update_info = check_for_updates()
        result["update_info"] = update_info
        result["steps"].append({"step": "dry-run check", "success": True, "detail": update_info})
        result["success"] = True
        return result

    # Pre-update snapshot
    try:
        take_snapshot(label="pre-update")
        result["steps"].append({"step": "pre-update snapshot", "success": True})
    except Exception as e:
        result["steps"].append({"step": "pre-update snapshot", "success": False, "stderr": str(e)})

    # Upgrade litellm via uv
    step = _run_step(
        ["uv", "pip", "install", "--upgrade", "litellm", "--directory", LITELLM_DIR],
        "uv pip install --upgrade litellm",
        timeout=300,
    )
    result["steps"].append(step)
    if not step["success"]:
        result["error"] = f"uv pip install failed: {step['stderr']}"
        _log_update_failure(result, old)
        return result

    # Upgrade litellm-proxy-extras in lockstep — litellm pins an old version,
    # so `--upgrade litellm` alone leaves it stale and the service won't start.
    step = _run_step(
        ["uv", "pip", "install", "--upgrade", "litellm-proxy-extras", "--directory", LITELLM_DIR],
        "uv pip install --upgrade litellm-proxy-extras",
        timeout=300,
    )
    result["steps"].append(step)
    if not step["success"]:
        result["error"] = f"proxy-extras upgrade failed: {step['stderr']}"
        _log_update_failure(result, old)
        return result

    # Regenerate the Prisma client so it matches the new schema (otherwise the
    # client goes stale and DB-backed features error with "Could not find field").
    step = _prisma_generate_step()
    result["steps"].append(step)
    if not step["success"]:
        result["error"] = f"prisma generate failed: {step['stderr']}"
        _log_update_failure(result, old)
        return result

    # Restart service
    step = _run_step(["systemctl", "restart", LITELLM_SERVICE], "restart service", timeout=30)
    result["steps"].append(step)
    if not step["success"]:
        result["error"] = f"Service restart failed: {step['stderr']}"
        _log_update_failure(result, old)
        return result

    # Wait for healthy
    healthy = _wait_for_healthy()
    result["steps"].append({"step": "health check", "success": healthy})
    if not healthy:
        result["error"] = "Service did not become healthy after restart"
        _log_update_failure(result, old)
        return result

    # Record new version
    new = get_current_version()
    result["new_version"] = new

    # Post-update snapshot
    try:
        take_snapshot(label="post-update")
        result["steps"].append({"step": "post-update snapshot", "success": True})
    except Exception as e:
        result["steps"].append({"step": "post-update snapshot", "success": False, "stderr": str(e)})

    # Log success
    old_ver = old.get("version") or "?"
    new_ver = new.get("version") or "?"
    old_pe = old.get("proxy_extras") or "?"
    new_pe = new.get("proxy_extras") or "?"
    with get_db() as conn:
        conn.execute(
            "INSERT INTO maintenance_log (category, title, description, tags) VALUES (?, ?, ?, ?)",
            ("update", f"Updated LiteLLM {old_ver} -> {new_ver}",
             f"Automated update completed successfully.\n"
             f"litellm: {old_ver} -> {new_ver}\n"
             f"litellm-proxy-extras: {old_pe} -> {new_pe}\n"
             f"Prisma client regenerated.",
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
