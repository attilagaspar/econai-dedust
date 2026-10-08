"""
Training metadata: the frozen test set and the training ledger (P10.1–P10.3).

Frozen test set (`intermediate/test_stems.json`): a per-project list of
verified pages that the training export always EXCLUDES from training data
and always evaluates on. Without it cocosplit re-rolls the 80/20 split every
run and no two trainings are comparable. Each stem is stored with a hash of
its shapes at freeze time, so later edits to a frozen page can be surfaced
as a warning (the test set silently drifting is worse than no test set).

Training ledger (`training_log.json` in the project root): one row appended
per Train / fine-tune launch, updated on completion with duration and the
evaluation scores parsed from detectron2's metrics.json. Turns every
training run into a data point instead of an anecdote.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

TEST_SET_FILE = "test_stems.json"     # under <project>/intermediate/
LEDGER_FILE   = "training_log.json"   # under <project>/


# ── page helpers ─────────────────────────────────────────────────────────────

def page_status(data: dict) -> str:
    return (data.get("flags") or {}).get("status") or "predicted"


def shapes_hash(data: dict) -> str:
    """Stable hash of a page's shapes (label + geometry). Point coords are
    rounded so a no-op save doesn't read as an edit."""
    items = []
    for s in data.get("shapes", []):
        pts = [[round(float(x), 1), round(float(y), 1)]
               for x, y in (p[:2] for p in s.get("points", []))]
        items.append((s.get("label", ""), pts))
    items.sort(key=lambda t: (t[0], json.dumps(t[1])))
    blob = json.dumps(items, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _read_page(ann_dir: Path, stem: str) -> dict | None:
    f = ann_dir / f"{stem}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── frozen test set ──────────────────────────────────────────────────────────

def test_set_path(pdir: Path) -> Path:
    return pdir / "intermediate" / TEST_SET_FILE


def load_test_set(pdir: Path) -> dict | None:
    p = test_set_path(pdir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def test_set_status(pdir: Path) -> dict:
    """The stored set plus drift info: stems whose annotations changed since
    freezing, and stems whose page no longer exists."""
    ts = load_test_set(pdir)
    if not ts:
        return {"exists": False}
    ann_dir = pdir / "annotations"
    changed, missing = [], []
    for stem in ts.get("stems", []):
        data = _read_page(ann_dir, stem)
        if data is None:
            missing.append(stem)
        elif shapes_hash(data) != (ts.get("hashes") or {}).get(stem):
            changed.append(stem)
    return {"exists": True, "frozen_at": ts.get("frozen_at"),
            "n": len(ts.get("stems", [])), "stems": ts.get("stems", []),
            "changed": changed, "missing": missing}


def freeze_test_set(pdir: Path, n: int = 0,
                    stems: list[str] | None = None,
                    force: bool = False) -> dict:
    """Create the frozen set: either an explicit stem list, or n pages picked
    evenly across the sorted VERIFIED pages (deterministic; spreads the set
    over the whole volume, which stratifies by position/era for sources that
    evolve through the volume). Refuses to overwrite unless force."""
    if load_test_set(pdir) and not force:
        raise ValueError("A frozen test set already exists — unfreeze it first "
                         "(replacing it makes earlier eval scores incomparable).")
    ann_dir = pdir / "annotations"
    if stems:
        chosen = list(dict.fromkeys(stems))          # dedupe, keep order
        for stem in chosen:
            if _read_page(ann_dir, stem) is None:
                raise ValueError(f"Page not found: {stem}")
    else:
        verified = sorted(
            jf.stem for jf in ann_dir.glob("*.json")
            if page_status(json.loads(jf.read_text(encoding="utf-8"))) == "verified"
        )
        if n < 1:
            raise ValueError("Give the number of pages to freeze (n ≥ 1).")
        if len(verified) < n:
            raise ValueError(f"Only {len(verified)} verified page(s) — "
                             f"cannot freeze {n}. Verify more pages first.")
        if n == len(verified):
            chosen = verified
        else:
            step = (len(verified) - 1) / (n - 1) if n > 1 else 0
            chosen = sorted({verified[round(i * step)] for i in range(n)})
    hashes = {}
    for stem in chosen:
        data = _read_page(ann_dir, stem)
        hashes[stem] = shapes_hash(data or {})
    ts = {"frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "stems": chosen, "hashes": hashes}
    path = test_set_path(pdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ts, indent=2, ensure_ascii=False), encoding="utf-8")
    return ts


def unfreeze_test_set(pdir: Path) -> bool:
    p = test_set_path(pdir)
    if p.exists():
        p.unlink()
        return True
    return False


# ── training ledger ──────────────────────────────────────────────────────────

def ledger_path(pdir: Path) -> Path:
    return pdir / LEDGER_FILE


def _finite(obj):
    """Replace non-finite floats (NaN/inf) with None, recursively. Detectron2
    reports NaN for empty AP buckets (e.g. APs with no small boxes); Python's
    json module writes and reads it, but strict JSON (FastAPI responses)
    refuses it — so the ledger must never carry one."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_finite(v) for v in obj]
    return obj


def load_ledger(pdir: Path) -> list[dict]:
    p = ledger_path(pdir)
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
        return _finite(rows) if isinstance(rows, list) else []
    except Exception:
        return []


def _save_ledger(pdir: Path, rows: list[dict]):
    ledger_path(pdir).write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def ledger_append(pdir: Path, row: dict) -> str:
    """Append a run row; returns its id (launch timestamp-based)."""
    rows = load_ledger(pdir)
    row = dict(row)
    row.setdefault("id", time.strftime("%Y%m%d-%H%M%S"))
    row.setdefault("started", time.strftime("%Y-%m-%dT%H:%M:%S"))
    row.setdefault("status", "running")
    rows.append(row)
    _save_ledger(pdir, rows)
    return row["id"]


def ledger_update(pdir: Path, row_id: str, **fields):
    rows = load_ledger(pdir)
    for row in reversed(rows):
        if row.get("id") == row_id:
            row.update(fields)
            _save_ledger(pdir, rows)
            return True
    return False


# ── correction telemetry (P10.6) ─────────────────────────────────────────────
# When a page transitions predicted → corrected/verified, diff the model's
# stored prediction against the human-corrected result and log it: the true
# corrections-per-page curve, measured on real work, no GPU time spent.

CORRECTIONS_LOG = "corrections_log.json"   # under <project>/


def record_correction(pdir: Path, stem: str,
                      old_status: str, new_status: str) -> dict | None:
    """Call on any page-status change; logs only the meaningful transition
    (predicted → corrected/verified with a prediction file present).
    Never raises — telemetry must not break a save."""
    try:
        if old_status != "predicted" or new_status not in ("corrected", "verified"):
            return None
        pred_f = pdir / "predictions" / f"{stem}.json"
        ann_f  = pdir / "annotations" / f"{stem}.json"
        if not pred_f.exists() or not ann_f.exists():
            return None
        from app.eval_diff import diff_page
        gt   = json.loads(ann_f.read_text(encoding="utf-8")).get("shapes", [])
        pred = json.loads(pred_f.read_text(encoding="utf-8")).get("shapes", [])
        per_label = diff_page(gt, pred)
        tot = {"added": 0, "deleted": 0, "moved": 0, "ok": 0}
        for c in per_label.values():
            for k in tot:
                tot[k] += c[k]
        row = {"stem": stem, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "to": new_status, **tot,
               "corrections": tot["added"] + tot["deleted"] + tot["moved"]}
        log_f = pdir / CORRECTIONS_LOG
        rows = []
        if log_f.exists():
            try:
                rows = json.loads(log_f.read_text(encoding="utf-8"))
            except Exception:
                rows = []
        rows = [r for r in rows if r.get("stem") != stem]   # re-correction replaces
        rows.append(row)
        log_f.write_text(json.dumps(rows, indent=2, ensure_ascii=False),
                         encoding="utf-8")
        return row
    except Exception:
        return None


def corrections_summary(pdir: Path, window: int = 20) -> dict:
    log_f = pdir / CORRECTIONS_LOG
    rows = []
    if log_f.exists():
        try:
            rows = json.loads(log_f.read_text(encoding="utf-8"))
        except Exception:
            rows = []
    recent = rows[-window:]
    avg = (round(sum(r["corrections"] for r in recent) / len(recent), 2)
           if recent else None)
    return {"n": len(rows), "recent_n": len(recent),
            "recent_avg_corrections_per_page": avg, "rows": rows}


def parse_d2_metrics(text: str) -> dict | None:
    """Pull the LAST evaluation scores out of detectron2's metrics.json
    content (one JSON object per line; eval lines carry bbox/AP keys)."""
    best = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "bbox/AP" not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if "bbox/AP" in d:
            best = d
    if not best:
        return None
    out = {}
    for k, v in best.items():
        if (k.startswith("bbox/") and isinstance(v, (int, float))
                and math.isfinite(v)):
            out[k[len("bbox/"):]] = round(float(v), 2)
    out["iteration"] = best.get("iteration")
    return out or None
