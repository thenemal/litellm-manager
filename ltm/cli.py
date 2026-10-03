"""CLI entry point for LiteLLM Management."""

import argparse
import sys
import json
from datetime import datetime

from . import __version__
from .db import init_db, get_db
from .config import CATEGORIES


def resolve_category(value):
    """Resolve a category by exact match or unambiguous prefix."""
    if value in CATEGORIES:
        return value
    matches = [c for c in CATEGORIES if c.startswith(value)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        raise argparse.ArgumentTypeError(
            f"unknown category '{value}' (choose from: {', '.join(CATEGORIES)})"
        )
    raise argparse.ArgumentTypeError(
        f"ambiguous category '{value}' — matches: {', '.join(matches)}"
    )


def main():
    init_db()

    parser = argparse.ArgumentParser(
        prog="ltm",
        description="LiteLLM Maintenance Tracker",
    )
    parser.add_argument("--version", action="version", version=f"ltm {__version__}")
    sub = parser.add_subparsers(dest="command")

    # --- add-note ---
    p_note = sub.add_parser("add-note", help="Record a maintenance note")
    p_note.add_argument("title", help="Short title for the entry")
    p_note.add_argument("-d", "--description", default="", help="Detailed description")
    p_note.add_argument("-c", "--category", default="general", type=resolve_category,
                        metavar="CAT", help=f"Category or prefix ({', '.join(CATEGORIES)})")
    p_note.add_argument("-t", "--tags", default="", help="Comma-separated tags")

    # --- history ---
    p_hist = sub.add_parser("history", help="View maintenance log")
    p_hist.add_argument("-n", "--limit", type=int, default=20, help="Number of entries")
    p_hist.add_argument("-c", "--category", default=None, type=resolve_category,
                        metavar="CAT", help=f"Filter by category or prefix ({', '.join(CATEGORIES)})")
    p_hist.add_argument("--json", dest="as_json", action="store_true")

    # --- snapshot ---
    p_snap = sub.add_parser("snapshot", help="Take a configuration snapshot")
    p_snap.add_argument("-l", "--label", default=None, help="Label for this snapshot")

    # --- snapshots ---
    p_snaps = sub.add_parser("snapshots", help="List saved snapshots")
    p_snaps.add_argument("-n", "--limit", type=int, default=20)

    # --- diff ---
    p_diff = sub.add_parser("diff", help="Compare two snapshots")
    p_diff.add_argument("source", help="Config source name (e.g. litellm-env)")
    p_diff.add_argument("timestamp1", help="First snapshot timestamp")
    p_diff.add_argument("timestamp2", help="Second snapshot timestamp")

    # --- export ---
    p_export = sub.add_parser("export", help="Export snapshot as YAML")
    p_export.add_argument("timestamp", help="Snapshot timestamp to export")

    # --- health-check ---
    p_health = sub.add_parser("health-check", help="Run a health check")
    p_health.add_argument("--json", dest="as_json", action="store_true")

    # --- health-history ---
    p_hh = sub.add_parser("health-history", help="View health check history")
    p_hh.add_argument("-n", "--limit", type=int, default=20)
    p_hh.add_argument("--json", dest="as_json", action="store_true")

    # --- status ---
    sub.add_parser("status", help="Quick status overview")

    # --- update ---
    p_update = sub.add_parser("update", help="Update LiteLLM to latest version")
    p_update.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    p_update.add_argument("--version", dest="target_version", metavar="X.Y.Z",
                          help="Install this litellm release (default: latest; current version = reinstall/repair)")
    p_update.add_argument("--no-backup", action="store_true", help="Skip the pg_dump backup before upgrading")

    # --- check-update ---
    sub.add_parser("check-update", help="Check if LiteLLM updates are available")

    # --- cron-install ---
    p_cron = sub.add_parser("cron-install", help="Install cron job for health checks")
    p_cron.add_argument("-i", "--interval", type=int, default=15, help="Minutes between checks (default: 15)")

    # --- lxc-note ---
    sub.add_parser("lxc-note", help="Generate markdown note for Proxmox LXC Notes tab")

    # --- cron-remove ---
    sub.add_parser("cron-remove", help="Remove cron job for health checks")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    commands = {
        "add-note": cmd_add_note,
        "history": cmd_history,
        "snapshot": cmd_snapshot,
        "snapshots": cmd_snapshots,
        "diff": cmd_diff,
        "export": cmd_export,
        "health-check": cmd_health_check,
        "health-history": cmd_health_history,
        "update": cmd_update,
        "check-update": cmd_check_update,
        "lxc-note": cmd_lxc_note,
        "cron-install": cmd_cron_install,
        "cron-remove": cmd_cron_remove,
        "status": cmd_status,
    }
    commands[args.command](args)


def cmd_add_note(args):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO maintenance_log (category, title, description, tags) VALUES (?, ?, ?, ?)",
            (args.category, args.title, args.description, args.tags),
        )
    print(f"Logged: [{args.category}] {args.title}")


def cmd_history(args):
    with get_db() as conn:
        if args.category:
            rows = conn.execute(
                "SELECT * FROM maintenance_log WHERE category = ? ORDER BY timestamp DESC LIMIT ?",
                (args.category, args.limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM maintenance_log ORDER BY timestamp DESC LIMIT ?",
                (args.limit,),
            ).fetchall()

    if args.as_json:
        print(json.dumps([dict(r) for r in rows], indent=2))
        return

    if not rows:
        print("No maintenance entries found.")
        return

    for r in rows:
        tags = f" [{r['tags']}]" if r["tags"] else ""
        print(f"  {r['timestamp']}  {r['category']:15s}  {r['title']}{tags}")
        if r["description"]:
            print(f"    {r['description']}")


def cmd_snapshot(args):
    from .snapshot import take_snapshot
    captured = take_snapshot(label=args.label)
    label_str = f" ({args.label})" if args.label else ""
    print(f"Snapshot captured{label_str}: {len(captured)} files")
    for c in captured:
        print(f"  {c['source']:20s}  {c['path']}  ({c['size']} bytes)")


def cmd_snapshots(args):
    from .snapshot import list_snapshots
    snaps = list_snapshots(limit=args.limit)
    if not snaps:
        print("No snapshots found.")
        return
    for s in snaps:
        label = f"  ({s['label']})" if s["label"] else ""
        print(f"  {s['timestamp']}  {s['file_count']} files{label}")


def cmd_diff(args):
    from .snapshot import diff_snapshots
    result = diff_snapshots(args.source, args.timestamp1, args.timestamp2)
    if "error" in result:
        print(f"Error: {result['error']}")
        return
    if result["changed"]:
        print(result["diff"])
    else:
        print(f"No changes in {result['source']} between {result['from']} and {result['to']}")


def cmd_export(args):
    from .snapshot import export_snapshot_yaml
    print(export_snapshot_yaml(args.timestamp))


def cmd_health_check(args):
    from .health import run_health_check
    result = run_health_check()

    if args.as_json:
        print(json.dumps(result, indent=2))
        return

    up = "UP" if result["service_up"] else "DOWN"
    pg = "UP" if result.get("pg_up") else "DOWN"
    http = result["http_status"] or "N/A"
    ms = f"{result['response_ms']:.0f}ms" if result["response_ms"] else "N/A"
    cpu = f"{result['cpu_percent']}%" if result["cpu_percent"] is not None else "N/A"
    mem = f"{result['mem_used_mb']}/{result['mem_total_mb']} MB" if result["mem_used_mb"] else "N/A"
    disk = f"{result['disk_used_gb']}/{result['disk_total_gb']} GB" if result["disk_used_gb"] else "N/A"

    print(f"  LiteLLM:   {up}")
    print(f"  PostgreSQL:{pg}")
    print(f"  HTTP:      {http} ({ms})")
    print(f"  CPU:       {cpu}")
    print(f"  Memory:    {mem}")
    print(f"  Disk:      {disk}")

    if result["error_summary"]:
        print(f"\n  Recent errors:\n{result['error_summary']}")


def cmd_health_history(args):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM health_check ORDER BY timestamp DESC LIMIT ?",
            (args.limit,),
        ).fetchall()

    if args.as_json:
        print(json.dumps([dict(r) for r in rows], indent=2))
        return

    if not rows:
        print("No health checks recorded.")
        return

    print(f"  {'Timestamp':20s}  {'Svc':4s}  {'HTTP':5s}  {'ms':>6s}  {'CPU':>5s}  {'Mem':>12s}  {'Disk':>12s}")
    print("  " + "-" * 75)
    for r in rows:
        up = "OK" if r["service_up"] else "DOWN"
        http = str(r["http_status"] or "-")
        ms = f"{r['response_ms']:.0f}" if r["response_ms"] else "-"
        cpu = f"{r['cpu_percent']}%" if r["cpu_percent"] is not None else "-"
        mem = f"{r['mem_used_mb']}/{r['mem_total_mb']}" if r["mem_used_mb"] else "-"
        disk = f"{r['disk_used_gb']}/{r['disk_total_gb']}" if r["disk_used_gb"] else "-"
        print(f"  {r['timestamp']:20s}  {up:4s}  {http:5s}  {ms:>6s}  {cpu:>5s}  {mem:>12s}  {disk:>12s}")


def cmd_status(args):
    from .health import check_service_status, check_postgresql_status, check_http, get_memory_mb, get_disk_gb

    hostname = "unknown"
    ip = "unknown"
    try:
        with open("/etc/hostname") as f:
            hostname = f.read().strip()
    except Exception:
        pass
    try:
        import subprocess
        r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            ip = r.stdout.strip().split()[0]
    except Exception:
        pass

    svc = check_service_status()
    pg = check_postgresql_status()
    http_status, resp_ms = check_http()
    mem_used, mem_total = get_memory_mb()
    disk_used, disk_total = get_disk_gb()

    print(f"  LiteLLM Manager v{__version__}")
    print(f"  Host: {hostname} ({ip})")
    print()
    print(f"  LiteLLM:    {'RUNNING' if svc else 'STOPPED'}")
    print(f"  PostgreSQL: {'RUNNING' if pg else 'STOPPED'}")
    if http_status:
        print(f"  HTTP:       {http_status} ({resp_ms:.0f}ms)")
    else:
        print(f"  HTTP:       unreachable")
    if mem_used:
        pct = round(mem_used / mem_total * 100, 1)
        print(f"  Memory:     {mem_used}/{mem_total} MB ({pct}%)")
    if disk_used:
        pct = round(disk_used / disk_total * 100, 1)
        print(f"  Disk:       {disk_used}/{disk_total} GB ({pct}%)")

    # Last maintenance note
    with get_db() as conn:
        last = conn.execute(
            "SELECT timestamp, category, title FROM maintenance_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        last_hc = conn.execute(
            "SELECT timestamp FROM health_check ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

    print()
    if last:
        print(f"  Last note:    [{last['category']}] {last['title']} ({last['timestamp']})")
    else:
        print(f"  Last note:    (none)")
    if last_hc:
        print(f"  Last check:   {last_hc['timestamp']}")
    else:
        print(f"  Last check:   (none)")


def cmd_update(args):
    from .update import run_update
    if args.dry_run:
        print("Dry run — checking what would happen...\n")
    else:
        print("Starting LiteLLM update...\n")

    result = run_update(dry_run=args.dry_run, version=args.target_version,
                        backup=not args.no_backup)

    if result.get("up_to_date"):
        print(f"Already up to date ({(result.get('old_version') or {}).get('version', '?')}).")
        return

    for step in result["steps"]:
        status = "OK" if step["success"] else "FAIL"
        detail = step.get("detail")
        if detail and "\n" in detail:
            print(f"  [{status}] {step['step']}")
            print("\n".join("        " + line for line in detail.splitlines()))
        else:
            print(f"  [{status}] {step['step']}" + (f"  ({detail})" if detail else ""))

    print()
    old = result.get("old_version") or {}
    if result["dry_run"] and result["success"]:
        verb = "reinstall" if old.get("version") == result["target_version"] else "update"
        print(f"Would {verb} litellm {old.get('version', '?')} -> {result['target_version']}")
    elif result["success"]:
        new = result.get("new_version") or {}
        print(f"Update successful!")
        print(f"  litellm:               {old.get('version', '?')} -> {new.get('version', '?')}")
        print(f"  litellm-proxy-extras:  {old.get('proxy_extras', '?')} -> {new.get('proxy_extras', '?')}")
        if result.get("backup_path"):
            print(f"  DB backup:             {result['backup_path']}")
    else:
        print(f"Update FAILED: {result.get('error', 'unknown error')}")
        if result.get("backup_path"):
            print(f"  DB backup: {result['backup_path']}")
        sys.exit(1)


def cmd_check_update(args):
    from .update import check_for_updates
    info = check_for_updates()
    print(f"  Current: {info.get('current_version') or '?'}")
    if info.get("error"):
        print(f"  Error checking PyPI: {info['error']}")
        sys.exit(1)
    print(f"  Latest:  {info.get('latest_version')}")
    print()
    if info.get("up_to_date"):
        print("  Already up to date.")
    else:
        print(f"  Update available. Run 'ltm update' to apply.")


def cmd_lxc_note(args):
    from .lxcnote import generate_lxc_note
    note = generate_lxc_note()
    print(note)
    out_path = "/root/lxc-note.md"
    with open(out_path, "w") as f:
        f.write(note)
    print(f"\nWritten to {out_path}")


def cmd_cron_install(args):
    from .cron import install_cron
    line = install_cron(args.interval)
    print(f"Installed cron job (every {args.interval} min):")
    print(f"  {line}")


def cmd_cron_remove(args):
    from .cron import remove_cron
    if remove_cron():
        print("Removed ltm health-check cron job.")
    else:
        print("No ltm cron job found.")


if __name__ == "__main__":
    main()
