"""Auto-tags: facts about a project the dashboard shows as chips next to the
user's own tags, computed from the project folder rather than typed in.

* ``all-verified`` — every page of the project is either verified or
  skipped (clutter), and at least one page is verified.
* ``has-model`` — a trained layout model exists for the project: its own
  training ledger has a finished training run, the model was seen in the
  last listing of its GPU server (the Models view), or its scripts use
  another project's model (a clone that kept its source's scripts) and
  that model exists.

Page statuses are read from the annotation JSONs, which can be large
(``imageData``) and many: only the head of each file is read (the flags
object sits near the top; a full parse is the fallback), each file's status
is cached on its size + mtime, and a folder's FIRST pass in a process runs
on a background thread — until it is done the project reports
``auto_tags_pending`` and no verified tag, so a dashboard load never waits
on the scan. After that, only edited pages are read again. The last model
listing per GPU server profile is kept in ``models_seen.json`` beside the
profile store (a volume in Docker) so the tag survives restarts.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from pathlib import Path

RULES_FILENAME = "econai_rules.cfg"        # the one non-page JSON in annotations/
_FLAGS_RE = re.compile(rb'"flags"\s*:\s*(\{[^{}]*\})')
_SCRIPT_MODEL_RE = re.compile(r"outputs/([A-Za-z0-9_.\-]+)/")

_file_cache: dict[str, tuple[int, int, str]] = {}     # path → (size, mtime_ns, status)
_LOCK = threading.Lock()
_HEAD_BYTES = 16384

# the first scan of a folder in this process runs in the background
_scanned: set[str] = set()
_pending: set[str] = set()
_bg_lock = threading.Lock()
_bg_queue: "queue.Queue[Path]" = queue.Queue()
_bg_thread: threading.Thread | None = None
SYNC = False          # tests: scan synchronously


def _page_status(path: Path) -> str:
    """The page's review status. The flags object is small and usually near
    the top of the file, so a regex finds it without parsing the whole
    (image-carrying) JSON; a full parse is the fallback."""
    try:
        with open(path, "rb") as fh:
            b = fh.read(_HEAD_BYTES)
            m = _FLAGS_RE.search(b)
            if m is None:                      # flags not in the head: read it all
                b += fh.read()
                m = _FLAGS_RE.search(b)
    except OSError:
        return "predicted"
    if m:
        try:
            return json.loads(m.group(1).decode("utf-8", errors="replace")).get("status") or "predicted"
        except Exception:
            pass
    try:
        return (json.loads(b).get("flags") or {}).get("status") or "predicted"
    except Exception:
        return "predicted"


def _bg_worker():
    while True:
        d = _bg_queue.get()
        try:
            status_counts(d, block=True)
        except Exception:
            pass
        finally:
            with _bg_lock:
                _pending.discard(str(d))


def _schedule_scan(ann_dir: Path) -> None:
    global _bg_thread
    key = str(ann_dir)
    with _bg_lock:
        if key in _pending or key in _scanned:
            return
        _pending.add(key)
        if _bg_thread is None or not _bg_thread.is_alive():
            _bg_thread = threading.Thread(target=_bg_worker, name="auto-tags-scan", daemon=True)
            _bg_thread.start()
    _bg_queue.put(ann_dir)


def status_counts(ann_dir: Path, block: bool = False) -> dict[str, int] | None:
    """{status: number of pages} for an annotation folder; statuses are
    cached per file on size + mtime, so repeated calls cost one directory
    scan. The folder's first scan in this process runs in the background:
    until it is done the call returns None (unless `block` or SYNC)."""
    key = str(ann_dir)
    if key not in _scanned and not (block or SYNC):
        _schedule_scan(ann_dir)
        return None
    counts: dict[str, int] = {}
    try:
        it = os.scandir(ann_dir)
    except OSError:
        _scanned.add(key)
        return counts
    with it:
        for e in it:
            if not e.name.endswith(".json") or e.name.endswith(RULES_FILENAME):
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            c = _file_cache.get(e.path)
            if c is None or c[0] != st.st_size or c[1] != st.st_mtime_ns:
                status = _page_status(Path(e.path))
                _file_cache[e.path] = (st.st_size, st.st_mtime_ns, status)
            else:
                status = c[2]
            counts[status] = counts.get(status, 0) + 1
    _scanned.add(key)
    return counts


# ── the models seen on each GPU server (written by the Models view listing) ──

def _seen_path() -> Path:
    from app import gpu_profiles
    return gpu_profiles._PROFILES_PATH.parent / "models_seen.json"


def _read_seen() -> dict:
    try:
        return json.loads(_seen_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_seen(d: dict) -> None:
    p = _seen_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def remember_models(profile: str, names: list[str]) -> None:
    """The Models view listed these models on the profile's server: replace
    what was known for the profile."""
    with _LOCK:
        d = _read_seen()
        d[profile] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "models": sorted(set(names))}
        _write_seen(d)


def forget_model(profile: str, name: str) -> None:
    with _LOCK:
        d = _read_seen()
        ent = d.get(profile)
        if ent and name in ent.get("models", []):
            ent["models"] = [m for m in ent["models"] if m != name]
            _write_seen(d)


def seen_models(profile: str | None) -> set[str]:
    if not profile:
        return set()
    return set((_read_seen().get(profile) or {}).get("models") or [])


# ── the tags ──

def model_of(pdir: Path, name: str) -> str | None:
    """Another project whose model this project's train / infer scripts use
    (a clone that kept its source's scripts), else None."""
    for script in ("train.sh", "infer.sh"):
        try:
            m = _SCRIPT_MODEL_RE.search((pdir / "intermediate" / script).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if m and m.group(1) != name:
            return m.group(1)
    return None


def _trained_here(pdir: Path) -> bool:
    from app import training_meta
    try:
        rows = training_meta.load_ledger(pdir)
    except Exception:
        return False
    return any(r.get("mode") in ("train", "finetune") and r.get("status") == "finished" for r in rows)


def model_facts(name: str, pdir: Path, cfg: dict) -> dict | None:
    """Why the project has a model: {"how": ...} and, for a borrowed model,
    {"model_of": ...}; None when no trained model is known."""
    prof = cfg.get("server_profile")
    seen = seen_models(prof)
    if _trained_here(pdir):
        return {"how": "trained from this project (training ledger)"}
    if name in seen:
        return {"how": f"listed on the GPU server '{prof}'"}
    src = model_of(pdir, name)
    if src:
        src_dir = pdir.parent / src
        if src in seen or (src_dir.exists() and _trained_here(src_dir)):
            return {"how": f"uses the model of {src}", "model_of": src}
    return None


def auto_tags(name: str, pdir: Path, cfg: dict, block: bool = False) -> tuple[list[dict], bool]:
    """The project's auto-tags: ([{"tag", "title"}], pending) — each tag with
    a tooltip saying what it is based on; `pending` while the pages' first
    scan is still running (then the verified tag is not known yet)."""
    out: list[dict] = []
    c = status_counts(pdir / "annotations", block=block)
    pending = c is None
    if c:
        total = sum(c.values())
        work = total - c.get("skip", 0)
        if work > 0 and c.get("verified", 0) == work:
            skip = c.get("skip", 0)
            out.append({"tag": "all-verified",
                        "title": f"every page is verified ({work})" + (f", {skip} skipped" if skip else "")})
    mf = model_facts(name, pdir, cfg)
    if mf:
        out.append({"tag": "has-model", "title": f"a trained layout model exists: {mf['how']}",
                    **({"model_of": mf["model_of"]} if mf.get("model_of") else {})})
    return out, pending
