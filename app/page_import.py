"""
Page import — read PDFs or images from a local path into a project's annotations/ folder.
Reads files directly from disk; no browser upload or temp files.
Requires: pymupdf (pip install pymupdf)

Naming convention:
  xyz.pdf  (N pages)  →  xyz_1.jpg, xyz_2.jpg, …, xyz_N.jpg
  abc.png  (image)    →  abc.jpg  (stem preserved as-is)

Render scale (project-level setting, config.json):
  render_scale       number (PDF points → pixels; 2.0 = the historical
                     default, ~144 dpi) or "native" (per page: the pixel
                     density of the largest embedded image, so a scanned
                     PDF is rendered at the resolution of its scan)
  render_longest_px  integer; when set it wins: the longest page side is
                     rendered to this many pixels
Every PDF import path (upload, server-side path, inbox) resolves the scale
through `render_settings_for(ann_dir)`; each page JSON records the scale it
was rendered at as `pdf_scale` (text-layer extraction and re-rendering
depend on it).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_SCALE = 2.0
MIN_SCALE, MAX_SCALE = 0.5, 12.0
MIN_LONGEST_PX, MAX_LONGEST_PX = 200, 20000
NATIVE = "native"

EMPTY_LABELME = {
    "version": "5.0.0",
    "flags": {},
    "shapes": [],
    "imageData": None,
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def _sanitize(name: str) -> str:
    """Replace characters unsafe in filenames with underscores."""
    return re.sub(r'[\\/:*?"<>|]', '_', name)


def _save_image_file(img_path: Path, ann_dir: Path, stem: str) -> dict:
    """Copy/convert an image file into ann_dir with the given stem."""
    from PIL import Image as PILImage
    dest = ann_dir / f"{stem}.jpg"
    img  = PILImage.open(str(img_path)).convert("RGB")
    w, h = img.size
    img.save(str(dest), "JPEG", quality=92)
    meta = {**EMPTY_LABELME, "imagePath": dest.name,
            "imageHeight": h, "imageWidth": w}
    (ann_dir / f"{stem}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {"stem": stem, "width": w, "height": h}


# ── render scale ─────────────────────────────────────────────────────────────

def normalize_render_settings(render_scale=None, render_longest_px=None) -> dict:
    """Validate the two config keys → {"mode": "scale" | "longest" | "native",
    "scale": float | None, "longest_px": int | None}. Raises ValueError with a
    user-facing message. Missing keys → the default (scale 2.0)."""
    out = {"mode": "scale", "scale": DEFAULT_SCALE, "longest_px": None}
    if render_longest_px not in (None, "", 0):
        try:
            px = int(render_longest_px)
        except (TypeError, ValueError):
            raise ValueError("render_longest_px must be an integer number of pixels")
        if not (MIN_LONGEST_PX <= px <= MAX_LONGEST_PX):
            raise ValueError(f"render_longest_px must be between {MIN_LONGEST_PX} "
                             f"and {MAX_LONGEST_PX}")
        return {"mode": "longest", "scale": None, "longest_px": px}
    if render_scale in (None, ""):
        return out
    if isinstance(render_scale, str) and render_scale.strip().lower() == NATIVE:
        return {"mode": NATIVE, "scale": None, "longest_px": None}
    try:
        sc = float(render_scale)
    except (TypeError, ValueError):
        raise ValueError(f"render_scale must be a number or '{NATIVE}'")
    if not (MIN_SCALE <= sc <= MAX_SCALE):
        raise ValueError(f"render_scale must be between {MIN_SCALE} and {MAX_SCALE}")
    out["scale"] = sc
    return out


def render_settings_from_config(cfg: dict | None) -> dict:
    """The project's render settings; an invalid config falls back to the
    default rather than breaking imports."""
    cfg = cfg or {}
    try:
        return normalize_render_settings(cfg.get("render_scale"),
                                         cfg.get("render_longest_px"))
    except ValueError:
        return normalize_render_settings()


def render_settings_for(ann_dir: Path) -> dict:
    """Render settings of the project owning `ann_dir` (annotations/ → parent
    with config.json); the default when there is no config."""
    cfgp = Path(ann_dir).parent / "config.json"
    try:
        cfg = json.loads(cfgp.read_text(encoding="utf-8")) if cfgp.exists() else {}
    except Exception:
        cfg = {}
    return render_settings_from_config(cfg)


def native_scale_of(page) -> float | None:
    """Pixel density (image px per PDF point) of the largest raster image
    on a PyMuPDF page — the scale at which a scanned PDF's page renders at
    the resolution of its scan. None when the page has no usable image."""
    best = None
    try:
        infos = page.get_image_info()
    except Exception:
        return None
    page_area = max(float(page.rect.width * page.rect.height), 1.0)
    for info in infos or []:
        try:
            x0, y0, x1, y1 = info["bbox"]
            bw, bh = float(x1 - x0), float(y1 - y0)
            if bw <= 0 or bh <= 0 or (bw * bh) / page_area < 0.1:
                continue          # a logo / ornament, not the page scan
            sc = max(float(info["width"]) / bw, float(info["height"]) / bh)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if sc > 0 and (best is None or sc > best):
            best = sc
    return best


def scale_for_page(page, settings: dict) -> float:
    """The render scale for one PyMuPDF page under normalized `settings`,
    clamped to [MIN_SCALE, MAX_SCALE] and rounded to 4 decimals (so the
    value recorded in the JSON reproduces the rendering exactly)."""
    mode = settings.get("mode", "scale")
    if mode == "longest":
        long_side = max(float(page.rect.width), float(page.rect.height), 1.0)
        sc = float(settings["longest_px"]) / long_side
    elif mode == NATIVE:
        sc = native_scale_of(page) or DEFAULT_SCALE
    else:
        sc = float(settings.get("scale") or DEFAULT_SCALE)
    sc = min(max(sc, MIN_SCALE), MAX_SCALE)
    return round(sc, 4)


def render_page(page, scale: float):
    """Rasterize a PyMuPDF page at `scale` (points → pixels, both axes)."""
    import fitz
    return page.get_pixmap(matrix=fitz.Matrix(scale, scale))


def _import_pdf(pdf_path: Path, ann_dir: Path, base: str | None = None,
                render: dict | None = None) -> list[dict]:
    """Render every page of `pdf_path` into ann_dir. `render` = normalized
    render settings (see normalize_render_settings); None → the owning
    project's config (default scale 2.0)."""
    import fitz
    if base is None:
        base = _sanitize(pdf_path.stem)
    if render is None:
        render = render_settings_for(ann_dir)
    # Store pdf_source RELATIVE to the project root when the PDF lives inside it
    # (the upload flow copies it to <project>/sources/), so text-layer extraction
    # works on any machine the shared project is opened on. Fall back to absolute
    # only for PDFs imported from outside the project (not copied in).
    proj = ann_dir.parent
    try:
        pdf_src = str(pdf_path.resolve().relative_to(proj.resolve())).replace("\\", "/")
    except ValueError:
        pdf_src = str(pdf_path.resolve())
    pages = []
    doc      = fitz.open(str(pdf_path))
    n_digits = len(str(len(doc)))   # zero-pad to keep natural sort
    for i, page in enumerate(doc):
        scale = scale_for_page(page, render)
        pix  = render_page(page, scale)
        stem = f"{base}_{str(i + 1).zfill(n_digits)}"
        dest = ann_dir / f"{stem}.jpg"
        pix.save(str(dest))
        w, h = pix.width, pix.height
        meta = {**EMPTY_LABELME, "imagePath": dest.name,
                "imageHeight": h, "imageWidth": w,
                "pdf_source": pdf_src,
                "pdf_page":   i,
                "pdf_scale":  scale}
        (ann_dir / f"{stem}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        pages.append({"stem": stem, "width": w, "height": h})
    doc.close()
    return pages


def import_from_path(source: Path, ann_dir: Path) -> list[dict]:
    """
    Import all PDFs/images from a file or folder into ann_dir.
    source: path to a single file, or a folder containing files.
    """
    ann_dir.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        raise FileNotFoundError(f"Path not found: {source}")

    if source.is_dir():
        files = sorted(
            p for p in source.iterdir()
            if p.suffix.lower() in IMAGE_EXTS | {".pdf"}
        )
    else:
        files = [source]

    if not files:
        return []

    results = []
    for f in files:
        ext = f.suffix.lower()
        try:
            if ext == ".pdf":
                pages = _import_pdf(f, ann_dir)
                results.extend(pages)
            elif ext in IMAGE_EXTS:
                stem = _sanitize(f.stem)
                info = _save_image_file(f, ann_dir, stem)
                results.append(info)
        except Exception as e:
            results.append({"stem": f.name, "error": str(e)})

    return results
