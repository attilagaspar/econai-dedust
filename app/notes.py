"""Notes (dashboard → Notes): topics of timestamped notes, a Keep-style board
per topic, for what is not a project — candidate data sources and their
URLs, decisions, reminders. One JSON file on the server, shared by everyone
who uses the instance: DEDUST_NOTES_PATH, else next to the GPU profiles file
(a volume in the container), else the repo root.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path


def _default_path() -> Path:
    p = os.environ.get("DEDUST_NOTES_PATH")
    if p:
        return Path(p)
    prof = os.environ.get("DEDUST_PROFILES_PATH")
    if prof:
        return Path(prof).parent / "notes.json"
    return Path(__file__).parent.parent / "notes.json"


NOTES_PATH = _default_path()
_LOCK = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load() -> dict:
    try:
        d = json.loads(NOTES_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("topics"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"topics": []}


def _save(d: dict) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = NOTES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, NOTES_PATH)


def _topic(d: dict, tid: str) -> dict:
    t = next((x for x in d["topics"] if x.get("id") == tid), None)
    if t is None:
        raise KeyError(tid)
    return t


def summary(t: dict) -> dict:
    notes = t.get("notes") or []
    return {"id": t["id"], "title": t.get("title") or "", "created": t.get("created"),
            "updated": max([t.get("created") or ""] + [n.get("updated") or "" for n in notes]) or None,
            "n_notes": len(notes)}


def list_topics() -> list:
    with _LOCK:
        return [summary(t) for t in _load()["topics"]]


def get_topic(tid: str) -> dict:
    with _LOCK:
        t = _topic(_load(), tid)
        notes = sorted(t.get("notes") or [], key=lambda n: n.get("created") or "", reverse=True)
        return {**summary(t), "notes": notes}


def add_topic(title: str) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("a topic needs a title")
    with _LOCK:
        d = _load()
        t = {"id": uuid.uuid4().hex[:10], "title": title, "created": _now(), "notes": []}
        d["topics"].append(t)
        _save(d)
        return summary(t)


def rename_topic(tid: str, title: str) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("a topic needs a title")
    with _LOCK:
        d = _load(); t = _topic(d, tid)
        t["title"] = title
        _save(d)
        return summary(t)


def delete_topic(tid: str) -> int:
    with _LOCK:
        d = _load(); t = _topic(d, tid)
        d["topics"] = [x for x in d["topics"] if x.get("id") != tid]
        _save(d)
        return len(t.get("notes") or [])


def add_note(tid: str, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("an empty note")
    with _LOCK:
        d = _load(); t = _topic(d, tid)
        now = _now()
        n = {"id": uuid.uuid4().hex[:10], "text": text, "created": now, "updated": now}
        t.setdefault("notes", []).append(n)
        _save(d)
        return n


def edit_note(tid: str, nid: str, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("an empty note")
    with _LOCK:
        d = _load(); t = _topic(d, tid)
        n = next((x for x in t.get("notes") or [] if x.get("id") == nid), None)
        if n is None:
            raise KeyError(nid)
        n["text"], n["updated"] = text, _now()
        _save(d)
        return n


def delete_note(tid: str, nid: str) -> None:
    with _LOCK:
        d = _load(); t = _topic(d, tid)
        before = len(t.get("notes") or [])
        t["notes"] = [x for x in t.get("notes") or [] if x.get("id") != nid]
        if len(t["notes"]) == before:
            raise KeyError(nid)
        _save(d)
