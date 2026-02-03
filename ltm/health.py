"""Health check logic for LiteLLM service, PostgreSQL, and host resources."""

import subprocess
import time
import urllib.request
import urllib.error

from .config import HEALTH_CHECK_URL, HEALTH_CHECK_TIMEOUT, LITELLM_SERVICE
from .db import get_db


def check_service_status() -> bool:
    """Check if the litellm systemd service is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", LITELLM_SERVICE],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def check_postgresql_status() -> bool:
    """Check if the postgresql systemd service is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "postgresql"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def check_http(url: str = HEALTH_CHECK_URL, timeout: int = HEALTH_CHECK_TIMEOUT):
    """HTTP GET to LiteLLM /health, returns (status_code, response_time_ms) or (None, None)."""
    try:
        start = time.monotonic()
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.monotonic() - start) * 1000
            return resp.status, elapsed
    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        return e.code, elapsed
    except Exception:
        return None, None


def get_cpu_percent() -> float | None:
    """Get 1-second CPU usage snapshot via /proc/stat."""
    try:
        def read_stat():
            with open("/proc/stat") as f:
                parts = f.readline().split()
            # user, nice, system, idle, iowait, irq, softirq, steal
            vals = list(map(int, parts[1:9]))
            idle = vals[3] + vals[4]
            total = sum(vals)
            return idle, total

        idle1, total1 = read_stat()
        time.sleep(1)
        idle2, total2 = read_stat()

        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        if total_delta == 0:
            return 0.0
        return round((1.0 - idle_delta / total_delta) * 100, 1)
    except Exception:
        return None


def get_memory_mb() -> tuple[float | None, float | None]:
    """Return (used_mb, total_mb) from /proc/meminfo."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                info[parts[0].rstrip(":")] = int(parts[1])
        total = info["MemTotal"] / 1024
        available = info["MemAvailable"] / 1024
        return round(total - available, 1), round(total, 1)
    except Exception:
        return None, None


def get_disk_gb(path: str = "/") -> tuple[float | None, float | None]:
    """Return (used_gb, total_gb) for the given mount point."""
    try:
        import os
        st = os.statvfs(path)
        total = (st.f_blocks * st.f_frsize) / (1024**3)
        free = (st.f_bavail * st.f_frsize) / (1024**3)
        return round(total - free, 1), round(total, 1)
    except Exception:
        return None, None


def get_recent_errors(lines: int = 50) -> str | None:
    """Pull recent journald errors for LiteLLM."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", LITELLM_SERVICE, "-p", "err", "-n", str(lines),
             "--no-pager", "--output=short"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.strip()
        return output if output and "No entries" not in output else None
    except Exception:
        return None


def run_health_check(quiet: bool = False) -> dict:
    """Run full health check and store result. Returns the check data."""
    service_up = check_service_status()
    pg_up = check_postgresql_status()
    http_status, response_ms = check_http()
    cpu = get_cpu_percent()
    mem_used, mem_total = get_memory_mb()
    disk_used, disk_total = get_disk_gb()
    errors = get_recent_errors()

    row = {
        "service_up": 1 if service_up else 0,
        "http_status": http_status,
        "response_ms": round(response_ms, 1) if response_ms else None,
        "cpu_percent": cpu,
        "mem_used_mb": mem_used,
        "mem_total_mb": mem_total,
        "disk_used_gb": disk_used,
        "disk_total_gb": disk_total,
        "error_summary": errors[:2000] if errors else None,
    }

    with get_db() as conn:
        conn.execute("""
            INSERT INTO health_check
                (service_up, http_status, response_ms, cpu_percent,
                 mem_used_mb, mem_total_mb, disk_used_gb, disk_total_gb, error_summary)
            VALUES
                (:service_up, :http_status, :response_ms, :cpu_percent,
                 :mem_used_mb, :mem_total_mb, :disk_used_gb, :disk_total_gb, :error_summary)
        """, row)

    # Add pg_up to returned data (not stored in DB schema, but useful for display)
    row["pg_up"] = pg_up

    return row
