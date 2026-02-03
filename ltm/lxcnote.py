"""Generate a markdown note for the Proxmox LXC Notes tab."""

import platform
import subprocess

from .db import get_db
from .health import check_service_status, check_postgresql_status, get_memory_mb, get_disk_gb


def _get_hostname() -> str:
    """Read container hostname."""
    try:
        with open("/etc/hostname") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


def _get_ip() -> str | None:
    """Get the primary IP address."""
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().split()[0]
    except Exception:
        pass
    return None


def _get_os_info() -> str:
    """Get OS description from os-release."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return platform.platform()


def generate_lxc_note() -> str:
    """Build a markdown string suitable for the Proxmox Notes tab."""
    from .update import get_current_version

    hostname = _get_hostname()
    ip = _get_ip()
    os_info = _get_os_info()
    version_info = get_current_version()
    service_up = check_service_status()
    pg_up = check_postgresql_status()
    mem_used, mem_total = get_memory_mb()
    disk_used, disk_total = get_disk_gb()

    version_str = version_info.get("version") or "unknown"

    lines = [
        f"# {hostname}",
        "",
        "## Overview",
        "",
        f"| | |",
        f"|---|---|",
        f"| **Hostname** | `{hostname}` |",
    ]

    if ip:
        lines.append(f"| **IP** | `{ip}` |")

    lines.extend([
        f"| **OS** | {os_info} |",
        f"| **LiteLLM** | v{version_str} |",
        f"| **Service** | `litellm.service` — {'running' if service_up else 'stopped'} |",
        f"| **PostgreSQL** | `postgresql.service` — {'running' if pg_up else 'stopped'} |",
        f"| **Port** | 4000 |",
        f"| **Install Path** | `/opt/litellm` |",
    ])

    if mem_total:
        lines.append(f"| **Memory** | {mem_used}/{mem_total} MB |")
    if disk_total:
        lines.append(f"| **Disk** | {disk_used}/{disk_total} GB |")

    lines.extend([
        "",
        "## Management",
        "",
        "This container is managed with [`ltm`](https://github.com/thenemal/litellm-manager) "
        "(LiteLLM Manager CLI).",
        "Claude Code is enabled for maintenance and administration.",
        "",
        "```bash",
        "ltm status          # Quick dashboard",
        "ltm health-check    # Run health check",
        "ltm check-update    # Check for updates",
        "ltm update          # Apply updates",
        "ltm add-note ...    # Log maintenance",
        "```",
    ])

    # Last maintenance entry
    with get_db() as conn:
        last = conn.execute(
            "SELECT timestamp, category, title FROM maintenance_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

    if last:
        lines.extend([
            "",
            "## Last Maintenance",
            "",
            f"**[{last['category']}]** {last['title']} ({last['timestamp']})",
        ])

    lines.append("")
    return "\n".join(lines)
