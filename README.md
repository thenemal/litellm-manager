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

ltm check-update        # Compare installed litellm with latest on PyPI
ltm update              # Upgrade to latest (backup, install, migrate, restart, verify)
ltm update --dry-run    # Preview without changes
ltm update --version X  # Pin a release (current version = reinstall/repair)
ltm update --no-backup  # Skip the pg_dump backup

ltm cron-install -i 15  # Health check every 15 minutes
ltm cron-remove         # Remove cron job

ltm lxc-note            # Generate Proxmox LXC Notes markdown
```

## Update procedure

`ltm update` follows LiteLLM's [uv/venv upgrade guide](https://docs.litellm.ai/docs/troubleshoot/pip_venv_upgrade):

1. Pre-update config snapshot
2. `systemctl stop litellm`
3. `pg_dump -F c` to `backups/pre-<old-version>-<timestamp>.dump` (skip with `--no-backup`)
4. `uv pip install 'litellm[proxy]==X'` -- the `[proxy]` extra pins the matching `litellm-proxy-extras` and proxy-only deps; never upgrade proxy-extras on its own
5. `prisma generate`, `prisma migrate deploy`, `prisma migrate status` against `litellm_proxy_extras/schema.prisma`
6. `systemctl start litellm`, then poll health for up to 10 minutes
7. Post-update snapshot and a `maintenance_log` entry

If stop, backup, or install fails, the old version is started again. If a Prisma step fails, the service is left stopped. Restore a backup with `pg_restore`.

`/opt/litellm/.env` sets `DISABLE_SCHEMA_UPDATE=true`, so the proxy does not migrate at startup and `ltm update` is the only migrator. A manual upgrade must run `prisma migrate deploy` itself. With this flag the proxy logs a harmless `Failed to generate migration diff ... schema.prisma: file or directory not found` at startup: LiteLLM's drift check uses a relative `./schema.prisma` path that only exists in its Docker image.

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
| **Admin UI login** | Personal `proxy_admin` accounts only -- `general_settings.disable_env_credential_login: true` blocks the shared `admin`/master-key login (the master key still works over the API) |
| **DB backups** | `backups/` (gitignored) |
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
