"""A short history of a GPU server's power state, as Dedust saw it.

One JSON line per event in `gpu_vm_events.jsonl` next to the profile store
(`DEDUST_PROFILES_PATH`, a volume in Docker): the start / stop requests sent
from the dashboard, and every change of state observed by a poll — so a VM
started or deallocated from the Azure portal shows up too, timed by Azure's
own provisioning timestamp when the poll carries one. The Models view shows
the log under the Azure block; `recent` adds how long each running span
lasted.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from app import gpu_profiles

_LOCK = threading.Lock()
_STEADY = ("running", "deallocated", "stopped", "failed")


def _path():
    # derived at call time: tests move the profile store to a temp dir
    return gpu_profiles._PROFILES_PATH.parent / "gpu_vm_events.jsonl"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _iso(at) -> str:
    if at is None:
        return _now()
    if isinstance(at, datetime):
        if at.tzinfo is not None:
            at = at.astimezone().replace(tzinfo=None)
        return at.strftime("%Y-%m-%dT%H:%M:%S")
    return str(at)[:19]


def _read_all() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def record(profile: str, event: str, source: str = "dashboard", at=None) -> dict:
    """Append one event: `event` is a state (running / deallocated / …) or a
    request ("start requested" / "stop requested"); `source` is "dashboard"
    (a button here) or "observed" (a poll saw the change)."""
    row = {"profile": profile, "event": event, "source": source, "at": _iso(at)}
    with _LOCK:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def last_state(profile: str) -> str | None:
    """The last steady state recorded for the profile (requests don't count)."""
    for r in reversed(_read_all()):
        if r.get("profile") == profile and r.get("event") in _STEADY:
            return r["event"]
    return None


def observe(profile: str, state: str, at=None, note: str | None = None) -> dict | None:
    """A poll saw `state`: record it when it differs from the last steady
    state on file (transient starting / deallocating are not logged — the
    steady state that follows is). `at` is Azure's provisioning time when
    known, so a change made in the portal is dated when it happened. A
    failed start is logged as "failed" with Azure's reason in `note`."""
    if state not in _STEADY:
        return None
    if last_state(profile) == state:
        return None
    row = {"profile": profile, "event": state, "source": "observed", "at": _iso(at)}
    if note:
        row["note"] = note[:300]
    with _LOCK:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def recent(profile: str, limit: int = 40) -> list[dict]:
    """The profile's last `limit` events, newest first; a `deallocated` /
    `stopped` event carries `hours` = the length of the running span it ended,
    and a `running` event that is the latest steady state carries
    `hours_so_far`."""
    rows = [r for r in _read_all() if r.get("profile") == profile]
    last_run = None
    for r in rows:
        ev = r.get("event")
        if ev == "running":
            last_run = r
        elif ev in ("deallocated", "stopped", "failed"):
            if last_run:
                r["hours"] = _hours_between(last_run["at"], r["at"])
            last_run = None
    if last_run is not None:
        last_run["hours_so_far"] = _hours_between(last_run["at"], _now())
    return list(reversed(rows))[:limit]


def _hours_between(a: str, b: str) -> float | None:
    try:
        d = datetime.fromisoformat(b) - datetime.fromisoformat(a)
        return round(d.total_seconds() / 3600, 2)
    except Exception:
        return None
