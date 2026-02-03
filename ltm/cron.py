"""Cron job management for scheduled health checks."""

import subprocess

CRON_MARKER = "# ltm-health-check"
CRON_LINE_TEMPLATE = "*/{interval} * * * * /usr/local/bin/ltm health-check --json >> /var/log/ltm-health.log 2>&1 " + CRON_MARKER


def install_cron(interval_minutes: int = 15):
    """Add ltm health-check to root crontab."""
    existing = _get_crontab()
    if CRON_MARKER in existing:
        # Remove existing line
        lines = [l for l in existing.splitlines() if CRON_MARKER not in l]
        existing = "\n".join(lines)

    new_line = CRON_LINE_TEMPLATE.format(interval=interval_minutes)
    new_crontab = existing.rstrip("\n") + "\n" + new_line + "\n"

    subprocess.run(
        ["crontab", "-"],
        input=new_crontab, text=True, check=True,
    )
    return new_line


def remove_cron():
    """Remove ltm health-check from root crontab."""
    existing = _get_crontab()
    if CRON_MARKER not in existing:
        return False

    lines = [l for l in existing.splitlines() if CRON_MARKER not in l]
    subprocess.run(
        ["crontab", "-"],
        input="\n".join(lines) + "\n", text=True, check=True,
    )
    return True


def _get_crontab() -> str:
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""
