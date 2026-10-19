"""
Re-render pages — re-rasterize a project's PDF-imported pages at another
scale and rescale every coordinate the page JSONs carry, so annotations
stay aligned and stems stay unchanged.

Why: PDFs are imported at `render_scale` (historically a fixed 2.0, ~144
dpi) while the embedded scans are often two or three times denser, so
every crop the editor and the record browser show is blurrier than the
source. Re-rendering replaces the page images with sharper ones without
losing a single hand correction.

What moves (page frame, pixels) — multiplied by the exact per-axis factor
(new_w / old_w, new_h / old_h):
  page        imageWidth / imageHeight, pdf_scale (→ the new scale)
  shape       points (any count), row_struct.rows[].y0 / y1 (absolute page
              Y), raw ([[x, y], …] detector box), mask (point lists),
              derived_from.bbox (fragment / region marker)
  predictions predictions/<stem>.json: the same shape fields + dimensions
  registry    intermediate/records.json fragments[].bbox of the pages
What stays: everything else (text layers, lattice indices, authority,
structured, clips, record fields, statuses). `flags.crop_source` on a crop
page describes the SOURCE page's frame and is left alone (a crop page is an
image page anyway: it has no PDF source and is reported, not touched).

Each re-rendered page gets `pdf_scale = <new scale>` plus a provenance note
`rerender = {from_scale, scale, factor, ts, backup}`; a page whose
`pdf_scale` already equals the target is up to date → a re-run is a no-op.

Refused (reported, never written): pages without pdf_source / pdf_page,
pages whose PDF cannot be found, pages whose image is missing, and pages
whose stored dimensions do not match a render at their recorded scale (the
image was perspective-corrected or otherwise edited after import — a
re-render would silently undo that).

Before writing, the old images + JSONs (+ predictions, records.json) go into
`<project>/backups/rerender_<ts>.zip` (stored JPEGs, deflated JSONs).

Long runs are a background job (`start_job` / `job_status`), like the inbox
import: a volume at scale 4 takes minutes and the remote deployment sits
behind a 100 s proxy timeout.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.page_import import (DEFAULT_SCALE, normalize_render_settings,
                             render_page, scale_for_page)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
DIM_TOL = 2          # px: stored dims vs. a render at the recorded scale
SCALE_EPS = 1e-6     # pdf_scale == target → up to date
NOTE_KEY = "rerender"


# ── pure geometry: scaling a page document ───────────────────────────────────

def _scale_pts(pts, fx: float, fy: float):
    """[[x, y], …] → scaled; anything malformed is returned unchanged."""
    out = []
    for p in pts:
        try:
            out.append([float(p[0]) * fx, float(p[1]) * fy])
        except (TypeError, ValueError, IndexError):
            return pts
    return out


def _looks_like_points(v) -> bool:
    return (isinstance(v, list) and len(v) > 0
            and all(isinstance(p, (list, tuple)) and len(p) >= 2
                    and all(isinstance(c, (int, float)) for c in p[:2]) for p in v))


def scale_shape(shape: dict, fx: float, fy: float) -> dict:
    """A deep copy of `shape` with every page-frame coordinate scaled."""
    new = json.loads(json.dumps(shape, ensure_ascii=False))
    if _looks_like_points(new.get("points")):
        new["points"] = _scale_pts(new["points"], fx, fy)
    rs = new.get("row_struct")
    if isinstance(rs, dict):
        for r in rs.get("rows") or []:
            if not isinstance(r, dict):
                continue
            for key in ("y0", "y1"):
                if isinstance(r.get(key), (int, float)):
                    r[key] = float(r[key]) * fy
    raw = new.get("raw")
    if _looks_like_points(raw):
        new["raw"] = _scale_pts(raw, fx, fy)
    mask = new.get("mask")
    if _looks_like_points(mask):
        new["mask"] = _scale_pts(mask, fx, fy)
    elif isinstance(mask, list) and mask and all(_looks_like_points(m) for m in mask):
        new["mask"] = [_scale_pts(m, fx, fy) for m in mask]
    df = new.get("derived_from")
    if isinstance(df, dict) and isinstance(df.get("bbox"), list) and len(df["bbox"]) == 4:
        try:
            x1, y1, x2, y2 = (float(v) for v in df["bbox"])
            df["bbox"] = [x1 * fx, y1 * fy, x2 * fx, y2 * fy]
        except (TypeError, ValueError):
            pass
    return new


def scale_doc(doc: dict, fx: float, fy: float, new_w: int, new_h: int) -> dict:
    """A deep copy of a LabelMe page (or prediction) document with every
    shape scaled and the dimensions replaced."""
    out = {k: v for k, v in doc.items() if k != "shapes"}
    out = json.loads(json.dumps(out, ensure_ascii=False))
    out["shapes"] = [scale_shape(s, fx, fy) if isinstance(s, dict) else s
                     for s in (doc.get("shapes") or [])]
    out["imageWidth"] = int(new_w)
    out["imageHeight"] = int(new_h)
    return out


# ── target resolution ────────────────────────────────────────────────────────

def target_settings(scale=None, longest_px=None, mode=None) -> dict:
    """Normalize a request into render settings (see page_import)."""
    if mode and str(mode).lower() == "native":
        return normalize_render_settings("native")
    return normalize_render_settings(scale, longest_px)


def describe_target(settings: dict) -> str:
    if settings["mode"] == "longest":
        return f"longest side {settings['longest_px']} px"
    if settings["mode"] == "native":
        return "native (embedded scan resolution)"
    return f"scale {settings['scale']:g}"


# ── file helpers ─────────────────────────────────────────────────────────────

def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(path: Path, obj) -> None:
    """Atomic write with the Dropbox / antivirus retry the server uses."""
    path = Path(path)
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        delay = 0.05
        for attempt in range(12):
            try:
                os.replace(tmp, str(path))
                return
            except PermissionError:
                if attempt == 11:
                    raise
                time.sleep(delay)
                delay = min(delay * 1.7, 2.0)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _find_image(folder: Path, stem: str) -> Path | None:
    for ext in IMAGE_EXTS:
        c = folder / f"{stem}{ext}"
        if c.exists():
            return c
    return None


def resolve_pdf_source(ann_dir: Path, pdf_source: str) -> Path | None:
    """Locate a page's source PDF on this machine (same strategy as the
    server's text-layer extraction: relative to the project, then the
    basename under sources/)."""
    if not pdf_source:
        return None
    proj = Path(ann_dir).parent
    base = Path(pdf_source).name
    p = Path(pdf_source)
    cands = []
    if p.is_absolute():
        cands.append(p)
    else:
        cands += [proj / pdf_source, Path(ann_dir) / pdf_source]
    cands += [proj / "sources" / base, Path(ann_dir) / "sources" / base,
              proj / base, Path(ann_dir) / base]
    for c in cands:
        try:
            if c.exists():
                return c
        except OSError:
            pass
    return None


def _pix_dims(page, scale: float) -> tuple[int, int]:
    """Pixmap size PyMuPDF produces for `page` at `scale` without rendering
    (the integer rect of rect × matrix: a ceiling with a small tolerance)."""
    import fitz
    r = page.rect * fitz.Matrix(scale, scale)
    ir = r.irect
    return int(ir.width), int(ir.height)


# ── plan ─────────────────────────────────────────────────────────────────────

def page_jsons(pdir: Path) -> list[Path]:
    """Every page JSON under annotations/ (batch subfolders included)."""
    ann = Path(pdir) / "annotations"
    if not ann.exists():
        return []
    return sorted(p for p in ann.rglob("*.json") if p.is_file()
                  and not p.name.startswith("."))


def plan(pdir: Path, settings: dict, stems: list | None = None) -> dict:
    """Classify every page; nothing is written.

    Returns {"target", "pages": [...to re-render: {stem, rel, old_scale,
    new_scale, old_w, old_h, new_w, new_h, fx, fy, pdf, pdf_page}],
    "up_to_date": [stem], "no_source": [stem], "missing_pdf": [{stem, pdf}],
    "no_image": [stem], "altered": [{stem, expected, stored}],
    "errors": [{stem, error}], "counts": {...}}."""
    import fitz
    pdir = Path(pdir)
    want = set(stems) if stems else None
    out = {"target": describe_target(settings), "settings": settings,
           "pages": [], "up_to_date": [], "no_source": [], "missing_pdf": [],
           "no_image": [], "altered": [], "errors": []}
    docs: dict = {}
    try:
        for jf in page_jsons(pdir):
            stem = jf.stem
            if want is not None and stem not in want:
                continue
            ann_dir = jf.parent
            rel = str(jf.relative_to(pdir)).replace("\\", "/")
            try:
                data = _read_json(jf)
            except Exception as e:
                out["errors"].append({"stem": stem, "error": f"unreadable JSON: {e}"})
                continue
            src, pno = data.get("pdf_source"), data.get("pdf_page")
            if not src or pno is None:
                out["no_source"].append(stem)
                continue
            pdf = resolve_pdf_source(ann_dir, src)
            if pdf is None:
                out["missing_pdf"].append({"stem": stem, "pdf": Path(src).name})
                continue
            img = _find_image(ann_dir, stem)
            if img is None:
                out["no_image"].append(stem)
                continue
            try:
                doc = docs.get(pdf)
                if doc is None:
                    doc = docs[pdf] = fitz.open(str(pdf))
                page = doc[int(pno)]
            except Exception as e:
                out["errors"].append({"stem": stem, "error": f"cannot open PDF page: {e}"})
                continue
            old_scale = float(data.get("pdf_scale") or DEFAULT_SCALE)
            old_w, old_h = data.get("imageWidth"), data.get("imageHeight")
            if not old_w or not old_h:
                out["errors"].append({"stem": stem, "error": "JSON has no image dimensions"})
                continue
            exp_w, exp_h = _pix_dims(page, old_scale)
            if abs(exp_w - int(old_w)) > DIM_TOL or abs(exp_h - int(old_h)) > DIM_TOL:
                out["altered"].append({"stem": stem, "expected": [exp_w, exp_h],
                                       "stored": [int(old_w), int(old_h)]})
                continue
            new_scale = scale_for_page(page, settings)
            if abs(new_scale - old_scale) < SCALE_EPS:
                out["up_to_date"].append(stem)
                continue
            new_w, new_h = _pix_dims(page, new_scale)
            out["pages"].append({
                "stem": stem, "rel": rel, "ann_dir": str(ann_dir), "image": str(img),
                "pdf": str(pdf), "pdf_page": int(pno),
                "old_scale": old_scale, "new_scale": new_scale,
                "old_w": int(old_w), "old_h": int(old_h), "new_w": new_w, "new_h": new_h,
                "fx": new_w / float(old_w), "fy": new_h / float(old_h),
            })
    finally:
        for d in docs.values():
            try:
                d.close()
            except Exception:
                pass
    out["counts"] = {
        "to_render": len(out["pages"]), "up_to_date": len(out["up_to_date"]),
        "no_source": len(out["no_source"]), "missing_pdf": len(out["missing_pdf"]),
        "no_image": len(out["no_image"]), "altered": len(out["altered"]),
        "errors": len(out["errors"]),
    }
    if out["pages"]:
        fx = [p["fx"] for p in out["pages"]]
        out["factor"] = {"min": min(fx), "max": max(fx)}
        out["scales"] = sorted({p["new_scale"] for p in out["pages"]})
    return out


# ── backup ───────────────────────────────────────────────────────────────────

def make_backup(pdir: Path, pages: list, ts: str) -> Path:
    """Zip the current images + JSONs (+ predictions, records.json) of
    `pages` under <project>/backups/rerender_<ts>.zip and return the path."""
    pdir = Path(pdir)
    bdir = pdir / "backups"
    bdir.mkdir(exist_ok=True)
    zpath = bdir / f"rerender_{ts}.zip"
    with zipfile.ZipFile(str(zpath), "w") as zf:
        def add(p: Path, compress):
            if p.exists():
                zf.write(str(p), arcname=str(p.relative_to(pdir)).replace("\\", "/"),
                         compress_type=compress)
        for pg in pages:
            ann_dir = Path(pg["ann_dir"])
            add(ann_dir / f"{pg['stem']}.json", zipfile.ZIP_DEFLATED)
            add(Path(pg["image"]), zipfile.ZIP_STORED)
            add(pdir / "predictions" / f"{pg['stem']}.json", zipfile.ZIP_DEFLATED)
        add(pdir / "intermediate" / "records.json", zipfile.ZIP_DEFLATED)
    return zpath


# ── apply ────────────────────────────────────────────────────────────────────

def apply(pdir: Path, the_plan: dict, backup: bool = True,
          progress=None) -> dict:
    """Re-render every page of `the_plan["pages"]`. `progress(i, n, stem)`
    is called before each page. Returns {rendered: [stem], failed: [{stem,
    error}], backup: path | None, predictions_scaled: n, records_updated: n}."""
    import fitz
    pdir = Path(pdir)
    pages = list(the_plan.get("pages") or [])
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    result = {"rendered": [], "failed": [], "backup": None,
              "predictions_scaled": 0, "records_updated": 0, "ts": ts}
    if not pages:
        return result
    if backup:
        result["backup"] = str(make_backup(pdir, pages, ts).relative_to(pdir)).replace("\\", "/")
    pred_dir = pdir / "predictions"
    factors: dict = {}
    docs: dict = {}
    try:
        for i, pg in enumerate(pages):
            if progress:
                progress(i, len(pages), pg["stem"])
            try:
                pdf = Path(pg["pdf"])
                doc = docs.get(pdf)
                if doc is None:
                    doc = docs[pdf] = fitz.open(str(pdf))
                page = doc[pg["pdf_page"]]
                pix = render_page(page, pg["new_scale"])
                new_w, new_h = pix.width, pix.height
                old_w, old_h = pg["old_w"], pg["old_h"]
                fx, fy = new_w / float(old_w), new_h / float(old_h)
                jf = Path(pg["ann_dir"]) / f"{pg['stem']}.json"
                data = _read_json(jf)          # fresh: the editor may have saved since the plan
                img = Path(pg["image"])
                pix.save(str(img))
                new = scale_doc(data, fx, fy, new_w, new_h)
                new["pdf_scale"] = pg["new_scale"]
                new[NOTE_KEY] = {"from_scale": pg["old_scale"], "scale": pg["new_scale"],
                                 "factor": [fx, fy], "ts": ts, "backup": result["backup"]}
                _write_json(jf, new)
                factors[pg["stem"]] = (fx, fy)
                pj = pred_dir / f"{pg['stem']}.json"
                if pj.exists():
                    try:
                        pd = _read_json(pj)
                        _write_json(pj, scale_doc(pd, fx, fy, new_w, new_h))
                        result["predictions_scaled"] += 1
                    except Exception as e:
                        result["failed"].append({"stem": pg["stem"],
                                                 "error": f"prediction JSON: {e}"})
                result["rendered"].append(pg["stem"])
            except Exception as e:
                result["failed"].append({"stem": pg["stem"], "error": str(e)})
    finally:
        for d in docs.values():
            try:
                d.close()
            except Exception:
                pass
    # derived registry: keep its fragment boxes aligned until the next sweep
    reg = pdir / "intermediate" / "records.json"
    if factors and reg.exists():
        try:
            r = _read_json(reg)
            n = 0
            for rec in (r.get("records") or {}).values():
                for fr in (rec or {}).get("fragments") or []:
                    f = factors.get(fr.get("stem"))
                    b = fr.get("bbox")
                    if f and isinstance(b, list) and len(b) == 4:
                        fr["bbox"] = [b[0] * f[0], b[1] * f[1], b[2] * f[0], b[3] * f[1]]
                        n += 1
            if n:
                _write_json(reg, r)
            result["records_updated"] = n
        except Exception as e:
            result["failed"].append({"stem": "intermediate/records.json", "error": str(e)})
    return result


# ── background job (one per project) ─────────────────────────────────────────

_JOBS: dict = {}
_LOCK = threading.Lock()


def active_job(project: str) -> dict | None:
    with _LOCK:
        j = _JOBS.get(project)
    return j if j and not j.get("done") else None


def job_status(project: str) -> dict | None:
    with _LOCK:
        j = _JOBS.get(project)
    return dict(j) if j else None


def start_job(project: str, pdir: Path, the_plan: dict, backup: bool = True,
              on_done=None) -> dict:
    """Run `apply` on a daemon thread; the job dict is polled via
    job_status. Refuses while another job runs for the same project."""
    with _LOCK:
        cur = _JOBS.get(project)
        if cur and not cur.get("done"):
            raise RuntimeError("a re-render is already running for this project")
        job = {"id": uuid.uuid4().hex[:8], "project": project, "phase": "starting",
               "page_i": 0, "page_n": len(the_plan.get("pages") or []),
               "current": "", "done": False, "started": time.time(),
               "target": the_plan.get("target"), "result": None, "error": None}
        _JOBS[project] = job

    def progress(i, n, stem):
        job.update(phase="rendering", page_i=i, page_n=n, current=stem)

    def run():
        try:
            if backup:
                job["phase"] = "backing up"
            res = apply(pdir, the_plan, backup=backup, progress=progress)
            job["result"] = res
            job["phase"] = "done"
        except Exception as e:
            job["error"] = str(e)
            job["phase"] = "failed"
        finally:
            job["done"] = True
            job["finished"] = time.time()
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass

    threading.Thread(target=run, daemon=True, name=f"rerender-{project}").start()
    return dict(job)
