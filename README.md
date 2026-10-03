# ltm - LiteLLM Manager

A lightweight CLI tool for tracking maintenance, health, and configuration of a [LiteLLM](https://github.com/BerriAI/litellm) LXC container.

## Features

- **Health checks** -- monitors LiteLLM service, PostgreSQL, HTTP endpoint, CPU, memory, and disk
- **Config snapshots** -- captures `.env`, `litellm.yaml`, systemd unit, network config, and `uv pip freeze`
- **Update management** -- follows LiteLLM's official uv/venv upgrade procedure: stops the service, backs up the DB with `pg_dump`, installs `litellm[proxy]==X`, regenerates the Prisma client, deploys migrations, restarts and verifies health, takes pre/post snapshots
- **Maintenance log** -- categorized notes with timestamps and tags
- **Cron scheduling** -- automated health checks on an interval
- **Proxmox LXC notes** -- generates markdown for the Proxmox Notes tab

## Installation

```bash
pip install -e /root/litellm-manager
```

## Usage

```bash
ltm status              # Quick dashboard (services, HTTP, resources)
ltm health-check        # Full health check (stored to DB)
ltm health-check --json # Machine-readable output
ltm health-history      # View past health checks

ltm add-note "title" -c update -d "details" -t "tag1,tag2"
ltm history             # View maintenance log
ltm history -c incident # Filter by category

ltm snapshot -l "before-upgrade"   # Capture all config files
ltm snapshots                      # List saved snapshots
ltm diff litellm-yaml ts1 ts2      # Compare two snapshots
ltm export <timestamp>             # Export snapshot as YAML

ltm check-update        # Check PyPI for newer litellm
ltm update              # Upgrade to latest (backup, install, migrate, restart, verify)
ltm update --dry-run    # Preview without changes
ltm update --version X  # Pin a release (current version = reinstall/repair)
ltm update --no-backup  # Skip the pg_dump backup

ltm cron-install -i 15  # Health check every 15 minutes
ltm cron-remove         # Remove cron job

ltm lxc-note            # Generate Proxmox LXC Notes markdown
```

## Categories

Notes and log entries support these categories (prefix-matching enabled):

`general`, `update`, `config-change`, `backup`, `incident`, `security`, `performance`

## Environment

| | |
|---|---|
| **Service** | `litellm.service` (port 4000) |
| **Database** | PostgreSQL 17 (`litellm_db`) |
| **Install path** | `/opt/litellm` |
| **Package manager** | `uv` |
| **Health endpoint** | `http://localhost:4000/health` |
| **Manager DB** | `ltm.db` (SQLite, overridable via `LTM_DB` env var) |

## Testing

```bash
python3 -m pytest tests/ -v
```

## Architecture

```
cli.py  -->  health.py   -->  db.py (SQLite)
        -->  snapshot.py  -->  db.py
        -->  update.py   -->  snapshot.py, health.py, db.py
        -->  lxcnote.py  -->  update.py, health.py, db.py
        -->  cron.py           config.py (constants)
```

## Claude Code Integration

Includes slash commands in `.claude/commands/` for all operations, enabling natural language maintenance via Claude Code.
