"""
EconAI — unified pipeline CLI.

Usage:
  python econai.py new-project <name> --type A --labels label1 label2
  python econai.py list
  python econai.py status <name>
  python econai.py advance <name>
  python econai.py set-stage <name> <stage>
  python econai.py serve [--port 8000]
"""

import argparse
import io
import sys
from pathlib import Path

# Force UTF-8 output on Windows so Unicode symbols print correctly
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Make sure app/ is importable regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))

from app.pipeline import (
    STAGE_DESCRIPTIONS,
    advance_stage,
    create_project,
    list_projects,
    load_config,
    load_pipeline,
    project_dir,
    set_stage,
    stages_for,
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _col(text: str, code: str) -> str:
    """Wrap text in an ANSI colour code (falls back gracefully on Windows)."""
    try:
        import os
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7
            )
    except Exception:
        pass
    return f"\033[{code}m{text}\033[0m"


GREEN  = lambda t: _col(t, "32")
YELLOW = lambda t: _col(t, "33")
CYAN   = lambda t: _col(t, "36")
BOLD   = lambda t: _col(t, "1")
DIM    = lambda t: _col(t, "2")


def _stage_line(stage: str, current: str, stages: list) -> str:
    idx_stage   = stages.index(stage)
    idx_current = stages.index(current)
    desc = STAGE_DESCRIPTIONS.get(stage, stage)
    if stage == current:
        marker = YELLOW("▶")
        label  = YELLOW(BOLD(stage))
        ddesc  = YELLOW(desc)
    elif idx_stage < idx_current:
        marker = GREEN("✓")
        label  = DIM(stage)
        ddesc  = DIM(desc)
    else:
        marker = " "
        label  = stage
        ddesc  = DIM(desc)
    return f"  {marker}  {label:<20} {ddesc}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_new_project(args):
    try:
        pdir = create_project(args.name, args.type, args.labels)
        print(GREEN(f"✓ Created project '{args.name}'") + f" at {pdir}")
        print()
        print(f"  Type   : {args.type}  ({'tables' if args.type == 'A' else 'structured text'})")
        print(f"  Labels : {', '.join(args.labels) if args.labels else '(none — edit config.json to add)'}")
        print()
        print(f"Next: add your LabelMe JSONs and images to:")
        print(f"  {pdir / 'annotations'}")
        print()
        print(f"Then edit server settings in:")
        print(f"  {pdir / 'config.json'}")
    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args):
    projects = list_projects()
    if not projects:
        print("No projects yet. Create one with:")
        print("  python econai.py new-project <name> --type A --labels label1 label2")
        return

    print(BOLD(f"{'NAME':<20} {'TYPE':<6} {'STAGE':<20} {'PAGES':>6}  LAST UPDATED"))
    print("─" * 80)
    for p in projects:
        updated = p["updated"][:19].replace("T", " ") if p["updated"] else ""
        stage_col = YELLOW(p["stage"]) if p["stage"] not in ("done",) else GREEN(p["stage"])
        print(f"{p['name']:<20} {p['type']:<6} {stage_col:<28} {p['pages']:>6}  {updated}")


def cmd_status(args):
    try:
        cfg   = load_config(args.name)
        state = load_pipeline(args.name)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    stages  = stages_for(cfg["type"])
    current = state["stage"]
    pdir    = project_dir(args.name)

    print(BOLD(f"\nProject: {args.name}"))
    print(f"  Type    : {cfg['type']}  ({'tables' if cfg['type'] == 'A' else 'structured text'})")
    print(f"  Labels  : {', '.join(cfg['labels']) if cfg['labels'] else '(none)'}")

    ann_dir = pdir / "annotations"
    n_json  = len(list(ann_dir.glob("*.json"))) if ann_dir.exists() else 0
    n_img   = len(list(ann_dir.glob("*.png")) + list(ann_dir.glob("*.jpg"))) if ann_dir.exists() else 0
    print(f"  Pages   : {n_json} JSONs, {n_img} images in annotations/")

    server = cfg.get("server", {})
    if server.get("host"):
        print(f"  Server  : {server['user']}@{server['host']}")
    else:
        print(f"  Server  : (not configured — edit config.json)")

    updated = state.get("updated", "")[:19].replace("T", " ")
    print(f"  Updated : {updated}")
    print()
    print(BOLD("Pipeline:"))
    for s in stages:
        print(_stage_line(s, current, stages))
    print()

    if state.get("notes"):
        print(f"  Notes: {state['notes']}")


def cmd_advance(args):
    try:
        cfg  = load_config(args.name)
        old  = load_pipeline(args.name)["stage"]
        new  = advance_stage(args.name)
        print(f"{args.name}: {YELLOW(old)} → {GREEN(new)}")
        print(f"  {STAGE_DESCRIPTIONS.get(new, '')}")
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_set_stage(args):
    try:
        set_stage(args.name, args.stage)
        print(f"{args.name}: stage set to {YELLOW(args.stage)}")
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_stages(args):
    """List valid stages for a project type."""
    t = args.type if hasattr(args, "type") else "A"
    stages = stages_for(t)
    print(BOLD(f"Stages for Type {t}:"))
    for i, s in enumerate(stages):
        print(f"  {i:2d}.  {s:<20} {DIM(STAGE_DESCRIPTIONS.get(s, ''))}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="econai",
        description="EconAI — unified historical document digitization pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # new-project
    p_new = sub.add_parser("new-project", help="Create a new project")
    p_new.add_argument("name", help="Project name (no spaces)")
    p_new.add_argument("--type", choices=["A", "B"], default="A",
                       help="A = tables, B = structured text (default: A)")
    p_new.add_argument("--labels", nargs="*", default=[],
                       help="Layout label names (e.g. tablazatelem tablazatfejlec)")

    # list
    sub.add_parser("list", help="List all projects")

    # status
    p_st = sub.add_parser("status", help="Show pipeline status for a project")
    p_st.add_argument("name", help="Project name")

    # advance
    p_adv = sub.add_parser("advance", help="Advance project to the next pipeline stage")
    p_adv.add_argument("name", help="Project name")

    # set-stage
    p_ss = sub.add_parser("set-stage", help="Jump to a specific pipeline stage")
    p_ss.add_argument("name", help="Project name")
    p_ss.add_argument("stage", help="Stage name (see: econai stages)")

    # stages
    p_stg = sub.add_parser("stages", help="List valid stages for a project type")
    p_stg.add_argument("--type", choices=["A", "B"], default="A")

    # serve
    p_srv = sub.add_parser("serve", help="Start the web server and open the browser")
    p_srv.add_argument("--port", type=int, default=8000)
    p_srv.add_argument("--host", default="127.0.0.1",
                       help="Bind address. Anything other than 127.0.0.1 REQUIRES "
                            "the ECONAI_TOKEN environment variable (remote auth).")
    p_srv.add_argument("--no-browser", action="store_true",
                       help="Don't open a browser on start (for background / startup use).")
    p_srv.add_argument("--no-reload", action="store_true",
                       help="Disable the autoreload file-watcher (for background / startup use).")

    # push-project — sync a local project to a remote Dedust instance over SFTP
    p_push = sub.add_parser(
        "push-project",
        help="Upload a local project to a remote Dedust server (SFTP, incremental)")
    p_push.add_argument("name", help="Project name (folder under projects/)")
    p_push.add_argument("--host", help="Remote host/IP (remembered after first use)")
    p_push.add_argument("--user", help="SSH user (remembered)")
    p_push.add_argument("--key",  help="SSH private key path (remembered)")
    p_push.add_argument("--remote-projects", dest="remote_projects",
                        help="Remote projects dir (remembered), e.g. /home/dedust/dedust/projects")
    p_push.add_argument("--all", action="store_true",
                        help="Also push intermediate/ and output/ (default: annotations, "
                             "predictions, config.json, pipeline.json, schema files)")
    p_push.add_argument("--as-name", dest="as_name",
                        help="Store under a different project name on the remote")

    return parser


def cmd_serve(args):
    import socket, subprocess, webbrowser, time
    port = args.port

    # Best-effort: kill anything already on the port (may be skipped on macOS without root)
    try:
        import psutil
        try:
            conns = psutil.net_connections(kind='inet')
        except (psutil.AccessDenied, AttributeError, OSError):
            conns = []
        for conn in conns:
            if getattr(conn.laddr, 'port', None) == port and conn.pid:
                try:
                    psutil.Process(conn.pid).kill()
                    time.sleep(0.5)
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
    except Exception:
        pass  # non-critical — uvicorn will report a clear error if port is busy

    import os
    host = getattr(args, "host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and not os.environ.get("ECONAI_TOKEN"):
        print("REFUSED: binding to a non-local address without ECONAI_TOKEN set.")
        print("Set a token first, e.g.:  set ECONAI_TOKEN=<long random string>")
        print("Remote requests will then require it (login page / Bearer header),")
        print("and remote sessions are confined to the projects/ folder.")
        return

    url = f"http://localhost:{port}"
    print(f"Starting Dedust server at {url}" + ("" if host in ("127.0.0.1", "localhost")
          else f"  (also listening on {host}:{port} — token required remotely)"))
    print("Press Ctrl+C to stop.\n")
    if not getattr(args, "no_browser", False):
        webbrowser.open(url)

    import uvicorn
    uvicorn.run("app.server:app", host=host, port=port,
                reload=not getattr(args, "no_reload", False))


def cmd_push_project(args):
    """Sync a local project to a remote Dedust instance over SFTP.

    Designed for the local-transform / cloud-work flow: import + render PDFs
    locally (fast CPU, no tunnel limits), then push the resulting files
    straight to the server, bypassing the browser upload path entirely.
    Incremental: unchanged files (same size + mtime) are skipped on re-push.
    Connection settings are remembered in ~/.dedust_push.json after first use.
    """
    import json, os, stat, time
    from pathlib import Path

    pdir = project_dir(args.name)
    if not pdir.exists():
        print(f"Error: no local project '{args.name}' at {pdir}", file=sys.stderr)
        sys.exit(1)

    # ── remembered connection defaults ──────────────────────────────────────
    cfg_path = Path.home() / ".dedust_push.json"
    saved = {}
    if cfg_path.exists():
        try:
            saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
    host = args.host or saved.get("host")
    user = args.user or saved.get("user")
    key  = args.key  or saved.get("key")
    remote_projects = args.remote_projects or saved.get("remote_projects")
    missing = [n for n, v in [("--host", host), ("--user", user), ("--key", key),
                              ("--remote-projects", remote_projects)] if not v]
    if missing:
        print(f"Error: missing {', '.join(missing)} (given once, they are remembered).",
              file=sys.stderr)
        print('Example:\n  python econai.py push-project myproj --host 20.71.94.231 '
              '--user dedust --key %USERPROFILE%\\.ssh\\id_rsa '
              '--remote-projects /home/dedust/dedust/projects', file=sys.stderr)
        sys.exit(1)

    # ── what to send ────────────────────────────────────────────────────────
    SKIP_DIRS_DEFAULT = {"intermediate", "output"}
    skip_dirs = set() if args.all else SKIP_DIRS_DEFAULT

    files = []
    for p in sorted(pdir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(pdir)
        if rel.parts and rel.parts[0] in skip_dirs:
            continue
        files.append((p, rel))
    total_bytes = sum(p.stat().st_size for p, _ in files)
    dest_name = args.as_name or args.name
    print(f"Pushing '{args.name}' → {user}@{host}:{remote_projects}/{dest_name}")
    print(f"  {len(files)} files, {total_bytes/1e6:.1f} MB"
          + (f"  (skipping {', '.join(sorted(skip_dirs))}/)" if skip_dirs else ""))

    # ── connect ─────────────────────────────────────────────────────────────
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, key_filename=os.path.expanduser(key))
    sftp = c.open_sftp()

    def mkdir_p(remote_dir: str):
        parts = remote_dir.strip("/").split("/")
        cur = ""
        for part in parts:
            cur += "/" + part
            try:
                sftp.stat(cur)
            except FileNotFoundError:
                sftp.mkdir(cur)

    sent = skipped = 0
    sent_bytes = 0
    t0 = time.time()
    try:
        remote_root = f"{remote_projects.rstrip('/')}/{dest_name}"
        mkdir_p(remote_root)
        made_dirs = {remote_root}
        for i, (p, rel) in enumerate(files, 1):
            rdir = remote_root if len(rel.parts) == 1 else \
                   remote_root + "/" + "/".join(rel.parts[:-1])
            if rdir not in made_dirs:
                mkdir_p(rdir)
                made_dirs.add(rdir)
            rpath = f"{rdir}/{rel.name}"
            st = p.stat()
            try:
                rst = sftp.stat(rpath)
                if rst.st_size == st.st_size and abs(rst.st_mtime - st.st_mtime) < 2:
                    skipped += 1
                    continue
            except FileNotFoundError:
                pass
            sftp.put(str(p), rpath)
            sftp.utime(rpath, (int(st.st_atime), int(st.st_mtime)))  # enable skip on re-push
            sent += 1
            sent_bytes += st.st_size
            if sent % 20 == 0 or i == len(files):
                rate = sent_bytes / 1e6 / max(time.time() - t0, 0.1)
                print(f"  {i}/{len(files)}  ({sent_bytes/1e6:.1f} MB sent, {rate:.1f} MB/s)")
    finally:
        sftp.close()
        c.close()

    dt = time.time() - t0
    print(GREEN(f"✓ Done in {dt:.0f}s — {sent} uploaded, {skipped} unchanged."))
    cfg_path.write_text(json.dumps({"host": host, "user": user, "key": key,
                                    "remote_projects": remote_projects}, indent=2),
                        encoding="utf-8")
    print(f"(connection remembered in {cfg_path})")
    print(f"Open the project at the remote dashboard — it appears automatically.")
    print(YELLOW("Reminder: a project should have ONE home. Once you start working on it"))
    print(YELLOW("remotely, treat this local copy as read-only (or delete it)."))


COMMANDS = {
    "new-project": cmd_new_project,
    "list":        cmd_list,
    "status":      cmd_status,
    "advance":     cmd_advance,
    "set-stage":   cmd_set_stage,
    "stages":      cmd_stages,
    "serve":       cmd_serve,
    "push-project": cmd_push_project,
}


def main():
    parser = build_parser()
    args   = parser.parse_args()
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
