"""
SSH / SFTP operations for the EconAI pipeline.
Requires: paramiko >= 3.0  (pip install --upgrade paramiko)
"""

from __future__ import annotations

import posixpath
import stat
import time
from pathlib import Path
from typing import Optional


def _load_key(kp: Path, passphrase: str = None):
    import paramiko
    pwd = passphrase.encode() if isinstance(passphrase, str) and passphrase else None
    header = kp.read_text(encoding="utf-8", errors="ignore").splitlines()[0] if kp.exists() else ""
    if "RSA" in header:
        return paramiko.RSAKey.from_private_key_file(str(kp), password=pwd)
    if "EC" in header or "ECDSA" in header:
        return paramiko.ECDSAKey.from_private_key_file(str(kp), password=pwd)
    if "DSA" in header or "DSS" in header:
        return paramiko.DSSKey.from_private_key_file(str(kp), password=pwd)
    if "OPENSSH" in header:
        for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                return cls.from_private_key_file(str(kp), password=pwd)
            except (paramiko.SSHException, ValueError):
                continue
    raise paramiko.SSHException(f"Unrecognised key format: {header!r}")


def _client(host: str, user: str, key_path: str, passphrase: str = None):
    """SSH connection. key_path set → key auth (passphrase unlocks the key).
    key_path EMPTY → password auth: the passphrase field IS the login password
    (e.g. VPN-protected workplace servers with plain username/password SSH)."""
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if not (key_path or "").strip():
        if not passphrase:
            raise ValueError(
                "No key file configured — password login: enter the account "
                "password in the passphrase field (or store it in the GPU profile).")
        c.connect(hostname=host, username=user, password=passphrase,
                  timeout=15, allow_agent=False, look_for_keys=False)
        return c
    kp = Path(key_path.strip().strip('"').strip("'")).expanduser()
    if kp.suffix.lower() == ".ppk":
        raise ValueError(
            "PuTTY .ppk keys are not supported. "
            "In PuTTYgen: load the key → Conversions → Export OpenSSH key → save as .pem"
        )
    pkey = _load_key(kp, passphrase)
    c.connect(hostname=host, username=user, pkey=pkey, timeout=15)
    return c


def test_connection(host: str, user: str, key_path: str, passphrase: str = None) -> dict:
    try:
        c = _client(host, user, key_path, passphrase)
        _, stdout, _ = c.exec_command(
            "uname -n && nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo '(no GPU info)'"
        )
        out = stdout.read().decode().strip()
        c.close()
        lines = out.splitlines()
        return {"ok": True, "hostname": lines[0] if lines else "", "gpu": lines[1] if len(lines) > 1 else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _sftp_mkdir_p(sftp, remote_dir: str):
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
                local_path: Path, remote_path: str, passphrase: str = None) -> dict:
    try:
        c = _client(host, user, key_path, passphrase)
        sftp = c.open_sftp()
        count = _sftp_put_dir(sftp, local_path, remote_path)
        sftp.close(); c.close()
        return {"ok": True, "files_uploaded": count, "remote_path": remote_path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def pull_folder(host: str, user: str, key_path: str,
                remote_path: str, local_path: Path, passphrase: str = None) -> dict:
    try:
        c = _client(host, user, key_path, passphrase)
        sftp = c.open_sftp()
        count = _sftp_get_dir(sftp, remote_path, local_path)
        sftp.close(); c.close()
        return {"ok": True, "files_downloaded": count, "local_path": str(local_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_command(host: str, user: str, key_path: str, cmd: str, passphrase: str = None) -> dict:
    try:
        c = _client(host, user, key_path, passphrase)
        _, stdout, stderr = c.exec_command(cmd)
        rc  = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        c.close()
        return {"ok": rc == 0, "returncode": rc, "stdout": out, "stderr": err}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def submit_job(host: str, user: str, key_path: str,
               cmd: str, log_path: str, passphrase: str = None) -> dict:
    wrapped = f"nohup bash -c {repr(cmd)} > {log_path} 2>&1 & echo $!"
    try:
        c = _client(host, user, key_path, passphrase)
        _, stdout, stderr = c.exec_command(wrapped)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        c.close()
        pid = int(out) if out.isdigit() else None
        return {"ok": pid is not None, "pid": pid, "log_path": log_path,
                "raw_stdout": out, "raw_stderr": err}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def stream_command(host: str, user: str, key_path: str, cmd: str,
                   passphrase: str = None):
    """
    Generator — yields output lines from a remote command in real time.
    Uses get_pty=True so stdout/stderr are merged and unbuffered.
    """
    c = _client(host, user, key_path, passphrase)
    _, stdout, _ = c.exec_command(cmd, get_pty=True)
    for line in iter(lambda: stdout.readline(), ""):
        yield line
    c.close()


def job_status(host: str, user: str, key_path: str,
               pid: Optional[int], log_path: str, passphrase: str = None) -> dict:
    try:
        c = _client(host, user, key_path, passphrase)
        check = f"kill -0 {pid} 2>/dev/null && echo running || echo stopped" if pid else "echo stopped"
        _, stdout, _ = c.exec_command(f"{check}; tail -n 60 {log_path} 2>/dev/null || echo '(log not found)'")
        lines = stdout.read().decode(errors="replace").splitlines()
        running = lines[0].strip() == "running" if lines else False
        log_tail = "\n".join(lines[1:]) if len(lines) > 1 else "(empty)"
        c.close()
        return {"ok": True, "running": running, "pid": pid, "log_path": log_path, "log_tail": log_tail}
    except Exception as e:
        return {"ok": False, "error": str(e)}
