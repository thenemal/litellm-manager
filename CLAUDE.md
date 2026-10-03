# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A lightweight CLI tool (`ltm`) for tracking maintenance, health, and configuration of a LiteLLM LXC container (Debian 13, systemd service on port 4000, installed at `/opt/litellm`, backed by PostgreSQL). SQLite-backed, single external dependency (PyYAML). Updates via `uv` (not git).

This repo is the *management tool*, not LiteLLM itself. LiteLLM lives at `/opt/litellm`; this tool lives at `/root/litellm-manager`.

## Commands

```bash
# Install (editable/dev mode)
pip install -e /root/litellm-manager

# Run tests
python3 -m pytest tests/ -v

# Run a single test
python3 -m pytest tests/test_db.py::test_init_creates_tables

# Use the CLI
ltm status
ltm health-check --json
ltm add-note "title" -c update -d "details"
ltm snapshot -l "before-upgrade"
ltm check-update
ltm update --dry-run
ltm update
ltm update --version 1.103.2   # pin / reinstall-repair
ltm update --no-backup
ltm lxc-note
```

## Architecture

```
cli.py  ──→  health.py   ──→  db.py (SQLite via context manager)
        ──→  snapshot.py  ──→  db.py
        ──→  update.py   ──→  snapshot.py, health.py, db.py
        ──→  lxcnote.py  ──→  update.py, health.py, db.py
        ──→  cron.py           config.py (constants only)
```

- **cli.py**: argparse-based entry point (`ltm=ltm.cli:main`). Uses lazy imports for `snapshot`, `health`, and `cron` modules inside command handlers to keep startup fast.
- **db.py**: All DB access goes through `with get_db() as conn:` context manager which auto-commits and closes. Uses `sqlite3.Row` for dict-like row access. Schema is in `init_db()`. DB path overridable via `LTM_DB` env var.
- **health.py**: Reads `/proc/stat` and `/proc/meminfo` directly, uses `os.statvfs()` for disk, `urllib` for HTTP checks, `subprocess` for systemctl/journalctl. Checks both litellm and postgresql services. All checks return `None` on failure rather than raising.
- **snapshot.py**: Captures files listed in `config.SNAPSHOT_SOURCES` plus `systemctl status` and `uv pip freeze` output. Diffing uses `difflib.unified_diff`. Export uses PyYAML.
- **config.py**: Pure constants — paths, URLs, valid categories. No logic.
- **update.py**: Follows the official uv/venv upgrade procedure (https://docs.litellm.ai/docs/troubleshoot/pip_venv_upgrade): stop service → `pg_dump -F c` into `backups/` → `uv pip install 'litellm[proxy]==X'` (the extra pins the matching `litellm-proxy-extras` and proxy-only deps — never upgrade proxy-extras independently) → `prisma generate`, `migrate deploy`, `migrate status` against `litellm_proxy_extras/schema.prisma` (venv `bin` prepended to PATH so the client lands in the venv) → start service and poll health (up to 10 min). `check-update` compares the installed litellm version to PyPI's latest only. Takes pre/post snapshots and logs results to `maintenance_log`. Each step aborts on failure and logs the reason; failures before install restart the old version, failures after leave the service stopped. `/opt/litellm/.env` sets `DISABLE_SCHEMA_UPDATE=true`, so the proxy does not migrate at startup — `ltm update` is the only migrator; any manual upgrade must run `prisma migrate deploy` itself.
- **lxcnote.py**: Generates markdown for Proxmox LXC Notes tab — pulls hostname, IP, OS, version, service status (litellm + postgresql), resources, and last maintenance entry.
- **cron.py**: Manages crontab entries identified by a `# ltm-health-check` marker comment.

## Database

Three tables in `ltm.db` (all timestamps ISO 8601 localtime):
- `maintenance_log` — manual notes with category/tags
- `config_snapshot` — file contents grouped by timestamp
- `health_check` — metrics: service_up, http_status, response_ms, cpu/mem/disk

## Testing

Tests use `LTM_DB` env var to point at a temp file, with an `autouse` fixture that creates a fresh DB per test. CLI tests invoke `python3 -m ltm.cli` via subprocess for true integration testing.

## Key Conventions

- Python 3.10+ required (uses `float | None` union syntax)
- All health/snapshot functions degrade gracefully — return `None` or skip on failure, never crash the tool
- JSON output (`--json`) available on health-check and history commands for scripting
- Snapshot sources are defined in `config.py:SNAPSHOT_SOURCES` dict — add new files to track there
- Maintenance categories are defined in `config.py:CATEGORIES` list
- The `-c` flag accepts unambiguous prefixes (e.g. `config` → `config-change`, `up` → `update`)