"""LiteLLM update operations via uv."""

import subprocess
import time

from .config import LITELLM_DIR, LITELLM_SERVICE
from .db import get_db
from .health import check_service_status, check_http


def get_current_version() -> dict:
    """Get current litellm version from the venv."""
    version = None

    try:
        result = subprocess.run(
            ["uv", "pip", "show", "litellm", "--directory", LITELLM_DIR],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    return {"version": version}


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


def _run_step(cmd: list[str], label: str, cwd: str = LITELLM_DIR, timeout: int = 300) -> dict:
    """Run a subprocess step, return result dict."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
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
    with get_db() as conn:
        conn.execute(
            "INSERT INTO maintenance_log (category, title, description, tags) VALUES (?, ?, ?, ?)",
            ("update", f"Updated LiteLLM {old_ver} -> {new_ver}",
             f"Automated update completed successfully.\nOld: {old_ver}\nNew: {new_ver}",
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
