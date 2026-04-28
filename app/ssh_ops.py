"""
SSH / SFTP operations for the EconAI pipeline.

Used to push/pull annotation folders and submit GPU jobs on the remote server.
Requires: paramiko  (pip install paramiko)
"""

from __future__ import annotations

import posixpath
import stat
import time
from pathlib import Path
from typing import Optional


def _client(host: str, user: str, key_path: str):
    """Return an authenticated paramiko SSHClient."""
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        hostname=host,
        username=user,
        key_filename=str(Path(key_path).expanduser()),
        timeout=15,
    )
    return c


def test_connection(host: str, user: str, key_path: str) -> dict:
    try:
        c = _client(host, user, key_path)
        _, stdout, _ = c.exec_command("uname -n && nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo '(no GPU info)'")
        out = stdout.read().decode().strip()
        c.close()
        lines = out.splitlines()
        return {"ok": True, "hostname": lines[0] if lines else "", "gpu": lines[1] if len(lines) > 1 else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# SFTP helpers
# ---------------------------------------------------------------------------

def _sftp_mkdir_p(sftp, remote_dir: str):
    """Recursively create remote directory (like mkdir -p)."""
    parts = remote_dir.replace("\\", "/").split("/")
    path = ""
    for part in parts:
        if not part:
            path = "/"
            continue
        path = posixpath.join(path, part) if path not in ("", "/") else "/" + part
        try:
            sftp.stat(path)
        except IOError:
            sftp.mkdir(path)


def _sftp_put_dir(sftp, local_dir: Path, remote_dir: str):
    """Recursively upload a local directory via SFTP. Returns file count."""
    _sftp_mkdir_p(sftp, remote_dir)
    count = 0
    for item in local_dir.iterdir():
        rpath = posixpath.join(remote_dir, item.name)
        if item.is_dir():
            count += _sftp_put_dir(sftp, item, rpath)
        else:
            sftp.put(str(item), rpath)
            count += 1
    return count


def _sftp_get_dir(sftp, remote_dir: str, local_dir: Path):
    """Recursively download a remote directory via SFTP. Returns file count."""
    local_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in sftp.listdir_attr(remote_dir):
        rpath = posixpath.join(remote_dir, entry.filename)
        lpath = local_dir / entry.filename
        if stat.S_ISDIR(entry.st_mode):
            count += _sftp_get_dir(sftp, rpath, lpath)
        else:
            sftp.get(rpath, str(lpath))
            count += 1
    return count


def push_folder(host: str, user: str, key_path: str,
                local_path: Path, remote_path: str) -> dict:
    """Upload local_path → remote_path via SFTP."""
    try:
        c = _client(host, user, key_path)
        sftp = c.open_sftp()
        count = _sftp_put_dir(sftp, local_path, remote_path)
        sftp.close()
        c.close()
        return {"ok": True, "files_uploaded": count, "remote_path": remote_path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def pull_folder(host: str, user: str, key_path: str,
                remote_path: str, local_path: Path) -> dict:
    """Download remote_path → local_path via SFTP."""
    try:
        c = _client(host, user, key_path)
        sftp = c.open_sftp()
        count = _sftp_get_dir(sftp, remote_path, local_path)
        sftp.close()
        c.close()
        return {"ok": True, "files_downloaded": count, "local_path": str(local_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Remote job submission
# ---------------------------------------------------------------------------

def run_command(host: str, user: str, key_path: str, cmd: str) -> dict:
    """Run a command synchronously and return stdout/stderr/returncode."""
    try:
        c = _client(host, user, key_path)
        _, stdout, stderr = c.exec_command(cmd)
        rc  = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        c.close()
        return {"ok": rc == 0, "returncode": rc, "stdout": out, "stderr": err}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def submit_job(host: str, user: str, key_path: str,
               cmd: str, log_path: str) -> dict:
    """
    Run cmd in the background on the server, redirecting output to log_path.
    Returns the PID so status can be polled later.
    """
    wrapped = f"nohup bash -c {repr(cmd)} > {log_path} 2>&1 & echo $!"
    try:
        c = _client(host, user, key_path)
        _, stdout, stderr = c.exec_command(wrapped)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        c.close()
        pid = int(out) if out.isdigit() else None
        return {"ok": pid is not None, "pid": pid, "log_path": log_path,
                "raw_stdout": out, "raw_stderr": err}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def job_status(host: str, user: str, key_path: str,
               pid: Optional[int], log_path: str) -> dict:
    """
    Check whether a background job is still running and return the last lines
    of its log.
    """
    try:
        c = _client(host, user, key_path)

        # Check if process is alive
        running = False
        if pid is not None:
            _, stdout, _ = c.exec_command(f"kill -0 {pid} 2>/dev/null && echo running || echo stopped")
            running = stdout.read().decode().strip() == "running"

        # Tail the log
        _, stdout, _ = c.exec_command(f"tail -n 60 {log_path} 2>/dev/null || echo '(log not found)'")
        log_tail = stdout.read().decode(errors="replace")

        c.close()
        return {"ok": True, "running": running, "pid": pid,
                "log_path": log_path, "log_tail": log_tail}
    except Exception as e:
        return {"ok": False, "error": str(e)}
