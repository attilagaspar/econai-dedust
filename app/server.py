"""
EconAI FastAPI backend.

Serves LabelMe JSON + images from any folder on disk.
Accepts corrections and writes them back to the JSON.

Run:
  python -m uvicorn app.server:app --reload --port 8000
or via econai.py (coming soon).
"""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List

from app.pipeline import (
    list_projects,
    load_config,
    load_pipeline,
    save_pipeline,
    stages_for,
    project_dir,
    advance_stage,
    set_stage,
)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    # Warm up EasyOCR on startup so the first user request is fast
    import asyncio, concurrent.futures
    def _warmup():
        try:
            _get_easyocr_reader(["en", "hu"])
            print("EasyOCR warmed up (GPU)", flush=True)
        except Exception as e:
            print(f"EasyOCR warmup failed: {e}", flush=True)
    asyncio.get_event_loop().run_in_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=1), _warmup
    )
    yield

app = FastAPI(title="EconAI", version="0.1.0", lifespan=lifespan)

from app.validator import router as _validator_router
app.include_router(_validator_router)

import os as _os

# Allow the browser (same host, any port) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend from app/static/
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_folder(folder: str) -> Path:
    """Resolve a folder path that may be absolute or relative to cwd."""
    p = Path(folder)
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {p}")
    return p


def _find_image(folder: Path, stem: str) -> Optional[Path]:
    for ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        candidate = folder / (stem + ext)
        if candidate.exists():
            return candidate
    return None


def _page_sort_key(name: str) -> tuple:
    """Sort page_1 < page_2 < ... < page_10 (natural sort)."""
    parts = re.split(r"(\d+)", name)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


# ---------------------------------------------------------------------------
# Routes — page listing
# ---------------------------------------------------------------------------

@app.get("/api/pages")
def list_pages(folder: str = Query(..., description="Absolute or relative path to the annotations folder")):
    """Return a sorted list of page stems that have both a JSON and an image."""
    d = _resolve_folder(folder)
    pages = []
    for jf in sorted(d.glob("*.json"), key=lambda p: _page_sort_key(p.stem)):
        img = _find_image(d, jf.stem)
        if img:
            pages.append({
                "stem": jf.stem,
                "json": jf.name,
                "image": img.name,
            })
    return {"folder": str(d), "pages": pages}


# ---------------------------------------------------------------------------
# Routes — serving files
# ---------------------------------------------------------------------------

@app.get("/api/image")
def get_image(folder: str = Query(...), stem: str = Query(...)):
    """Serve the image file for a given page stem."""
    d = _resolve_folder(folder)
    img = _find_image(d, stem)
    if img is None:
        raise HTTPException(status_code=404, detail=f"No image found for '{stem}'")
    return FileResponse(str(img), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/page")
def get_page(folder: str = Query(...), stem: str = Query(...)):
    """Return the LabelMe JSON for a page, with shape indices added."""
    d = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")

    data = json.loads(jf.read_text(encoding="utf-8"))

    # Inject a stable index into each shape so the frontend can reference them
    for i, shape in enumerate(data.get("shapes", [])):
        shape["_idx"] = i

    return data


# ---------------------------------------------------------------------------
# Routes — saving corrections
# ---------------------------------------------------------------------------

class ShapeUpdate(BaseModel):
    human_corrected_text: Optional[str] = None
    points: Optional[list] = None          # [[x1,y1],[x2,y2]] for box edits
    label: Optional[str] = None            # label rename


@app.patch("/api/page/shape")
def update_shape(
    folder: str = Query(...),
    stem:   str = Query(...),
    idx:    int = Query(..., description="Shape index (_idx from GET /api/page)"),
    body:   ShapeUpdate = ...,
):
    """Patch one shape in the JSON and write the file back."""
    d  = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])

    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail=f"Shape index {idx} out of range (0–{len(shapes)-1})")

    shape = shapes[idx]

    if body.human_corrected_text is not None:
        if "human_output" not in shape:
            shape["human_output"] = {}
        shape["human_output"]["human_corrected_text"] = body.human_corrected_text

    if body.points is not None:
        shape["points"] = body.points

    if body.label is not None:
        shape["label"] = body.label

    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "idx": idx}


# ---------------------------------------------------------------------------
# Routes — add / delete shapes
# ---------------------------------------------------------------------------

class NewShape(BaseModel):
    label: str
    points: list
    shape_type: str = "rectangle"


@app.post("/api/page/shape")
def add_shape(
    folder: str = Query(...),
    stem:   str = Query(...),
    body:   NewShape = ...,
):
    """Append a new shape to the JSON and return its index."""
    d  = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")

    data = json.loads(jf.read_text(encoding="utf-8"))
    new_shape = {
        "label":      body.label,
        "points":     body.points,
        "group_id":   None,
        "shape_type": body.shape_type,
        "flags":      {},
    }
    data["shapes"].append(new_shape)
    new_idx = len(data["shapes"]) - 1
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "idx": new_idx}


class ShapesReplace(BaseModel):
    shapes: list


@app.put("/api/page/shapes")
def replace_shapes(
    folder: str = Query(...),
    stem:   str = Query(...),
    body:   ShapesReplace = ...,
):
    """Replace the entire shapes array (used by undo)."""
    d  = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")
    data = json.loads(jf.read_text(encoding="utf-8"))
    data["shapes"] = body.shapes
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "count": len(body.shapes)}


@app.delete("/api/page/shape")
def delete_shape(
    folder: str = Query(...),
    stem:   str = Query(...),
    idx:    int = Query(...),
):
    """Remove one shape from the JSON by index."""
    d  = _resolve_folder(folder)
    jf = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail=f"Shape index {idx} out of range")

    shapes.pop(idx)
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "remaining": len(shapes)}


# ---------------------------------------------------------------------------
# Routes — PDF text layer extraction
# ---------------------------------------------------------------------------

@app.post("/api/page/pdf-text-layer")
def api_pdf_text_layer(
    folder: str = Query(...),
    stem:   str = Query(...),
):
    """Extract text from the PDF text layer for every shape on a page.

    Requires the page JSON to have pdf_source / pdf_page / pdf_scale fields,
    which are written automatically when importing from a PDF.
    Saves pdf_text on each shape and returns the updated shapes list.
    """
    import fitz

    d   = _resolve_folder(folder)
    jf  = d / f"{stem}.json"
    if not jf.exists():
        raise HTTPException(status_code=404, detail=f"JSON not found: {jf}")

    data = json.loads(jf.read_text(encoding="utf-8"))

    pdf_path  = data.get("pdf_source")
    pdf_page  = data.get("pdf_page")
    pdf_scale = data.get("pdf_scale", 2.0)

    if not pdf_path or pdf_page is None:
        raise HTTPException(
            status_code=400,
            detail="No PDF source recorded for this page. "
                   "Only pages imported from a PDF with the current version "
                   "of the app have this information."
        )

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Source PDF not found at: {pdf_path}\n"
                   "It may have been moved or deleted."
        )

    doc  = fitz.open(str(pdf_path))
    page = doc[int(pdf_page)]

    shapes  = data.get("shapes", [])
    updated = 0

    for shape in shapes:
        pts = shape.get("points", [])
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # Convert image pixel coords → PDF point coords
        x0, y0 = min(xs) / pdf_scale, min(ys) / pdf_scale
        x1, y1 = max(xs) / pdf_scale, max(ys) / pdf_scale
        rect = fitz.Rect(x0, y0, x1, y1)
        text = page.get_text("text", clip=rect).strip()
        shape["pdf_text"] = text
        updated += 1

    doc.close()

    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "updated": updated, "shapes": shapes}


# ---------------------------------------------------------------------------
# Routes — cell image crop
# ---------------------------------------------------------------------------

@app.get("/api/cell")
def get_cell(
    folder: str  = Query(...),
    stem:   str  = Query(...),
    idx:    int  = Query(...),
    pad:    int  = Query(4, description="Padding in pixels"),
    shadow: bool = Query(False, description="Return shadow (line-erased) version"),
):
    """Return a cropped image of a single cell (for the right panel zoom)."""
    from PIL import Image
    import io
    from fastapi.responses import StreamingResponse

    d   = _resolve_folder(folder)
    jf  = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    pts = shapes[idx]["points"]
    xs  = [p[0] for p in pts]
    ys  = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    img  = _get_shadow_page(folder, stem, img_path) if shadow else Image.open(str(img_path))
    w, h = img.size
    crop = img.crop((
        max(0, int(x1) - pad),
        max(0, int(y1) - pad),
        min(w, int(x2) + pad),
        min(h, int(y2) + pad),
    ))

    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg")


@app.post("/api/page/shape/ocr")
def api_ocr_cell(
    folder: str = Query(...),
    stem:   str = Query(...),
    idx:    int = Query(...),
    lang:   str = Query("hun", description="Tesseract language code"),
):
    """Run Tesseract OCR on a single cell and store the result in the page JSON."""
    import pytesseract
    from PIL import Image
    # Ensure Tesseract binary is found on Windows even if not on PATH
    import shutil
    if not shutil.which("tesseract"):
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    d        = _resolve_folder(folder)
    jf       = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    pts = shapes[idx]["points"]
    xs  = [p[0] for p in pts]
    ys  = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    shadow     = _get_shadow_page(folder, stem, img_path)
    sw, sh     = shadow.size
    pad        = 4
    crop_grey  = shadow.crop((
        max(0, int(x1) - pad), max(0, int(y1) - pad),
        min(sw, int(x2) + pad), min(sh, int(y2) + pad),
    )).convert("L")

    tess = pytesseract.image_to_data(
        crop_grey, lang=lang,
        config="--psm 4",          # single column, preserves line breaks
        output_type=pytesseract.Output.DICT,
    )

    # Group confident words by (block, paragraph, line) to preserve layout.
    # Also track the minimum top-coordinate of each line so we can sort by
    # actual pixel position (Tesseract block_num order ≠ visual top-to-bottom order).
    from collections import defaultdict
    line_words: dict = defaultdict(list)
    line_top:   dict = defaultdict(lambda: float("inf"))
    conf_values: list = []
    for txt, conf, blk, par, ln, top in zip(
        tess["text"], tess["conf"],
        tess["block_num"], tess["par_num"], tess["line_num"],
        tess["top"],
    ):
        c = int(conf)
        if txt.strip() and c > 0:
            key = (blk, par, ln)
            line_words[key].append(txt)
            line_top[key] = min(line_top[key], int(top))
            conf_values.append(c)

    # Sort lines by their top pixel coordinate (visual reading order)
    sorted_keys = sorted(line_words.keys(), key=lambda k: line_top[k])
    lines = [" ".join(line_words[k]) for k in sorted_keys]
    ocr_text  = "\n".join(lines).strip()
    mean_conf = round(sum(conf_values) / len(conf_values), 1) if conf_values else 0.0

    shapes[idx]["tesseract_output"] = {
        "ocr_text":  ocr_text,
        "mean_conf": mean_conf,
        "lang":      lang,
    }
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"ocr_text": ocr_text, "mean_conf": mean_conf}


# ---------------------------------------------------------------------------
# Shadow page cache — long table-line removal for cleaner OCR input
# ---------------------------------------------------------------------------
# OCR settings — tunable preprocessing parameters, persisted to disk
# ---------------------------------------------------------------------------
_OCR_SETTINGS_PATH = Path(__file__).parent / "ocr_settings.json"

_OCR_SETTINGS_DEFAULTS: dict = {
    "line_removal_fraction":  0.22,
    "v_line_fraction":        0.0,   # vertical line min-length override (0 = use line_removal_fraction)
    "use_adaptive_threshold": True,
    "adaptive_block_size":    15,
    "adaptive_c":             10,
    "morph_open_kernel":      2,
    # Line detection tuning
    "line_close_kernel":      0,     # horizontal close before detection (bridges dashes, 0=off)
    "line_dilate_thickness":  3,     # dilation after detection to cover line thickness
    # Post-line-removal output processing
    "blur_sigma":             0,     # Gaussian blur σ applied to output (0 = off)
    "output_binarize":        False, # binarize the final output image
    "output_open_kernel":     0,     # morphological opening on output (0 = off)
}


def _load_ocr_settings() -> dict:
    if _OCR_SETTINGS_PATH.exists():
        try:
            return {**_OCR_SETTINGS_DEFAULTS,
                    **json.loads(_OCR_SETTINGS_PATH.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(_OCR_SETTINGS_DEFAULTS)


_ocr_settings: dict = _load_ocr_settings()


class OcrSettingsBody(BaseModel):
    line_removal_fraction:  Optional[float] = None
    use_adaptive_threshold: Optional[bool]  = None
    adaptive_block_size:    Optional[int]   = None
    adaptive_c:             Optional[int]   = None
    morph_open_kernel:      Optional[int]   = None
    v_line_fraction:        Optional[float] = None
    line_close_kernel:      Optional[int]   = None
    line_dilate_thickness:  Optional[int]   = None
    blur_sigma:             Optional[int]   = None
    output_binarize:        Optional[bool]  = None
    output_open_kernel:     Optional[int]   = None


@app.get("/api/ocr-settings")
def api_get_ocr_settings():
    return _ocr_settings


@app.post("/api/ocr-settings")
def api_set_ocr_settings(body: OcrSettingsBody, save: bool = Query(False)):
    global _ocr_settings
    if body.line_removal_fraction is not None:
        _ocr_settings["line_removal_fraction"] = max(0.01, min(0.9, body.line_removal_fraction))
    if body.use_adaptive_threshold is not None:
        _ocr_settings["use_adaptive_threshold"] = body.use_adaptive_threshold
    if body.adaptive_block_size is not None:
        bs = max(3, body.adaptive_block_size)
        if bs % 2 == 0: bs += 1
        _ocr_settings["adaptive_block_size"] = bs
    if body.adaptive_c is not None:
        _ocr_settings["adaptive_c"] = max(0, min(50, body.adaptive_c))
    if body.morph_open_kernel is not None:
        _ocr_settings["morph_open_kernel"] = max(0, min(10, body.morph_open_kernel))
    if body.v_line_fraction is not None:
        _ocr_settings["v_line_fraction"] = max(0.0, min(0.9, body.v_line_fraction))
    if body.line_close_kernel is not None:
        _ocr_settings["line_close_kernel"] = max(0, min(30, body.line_close_kernel))
    if body.line_dilate_thickness is not None:
        _ocr_settings["line_dilate_thickness"] = max(1, min(15, body.line_dilate_thickness))
    if body.blur_sigma is not None:
        _ocr_settings["blur_sigma"] = max(0, min(10, body.blur_sigma))
    if body.output_binarize is not None:
        _ocr_settings["output_binarize"] = body.output_binarize
    if body.output_open_kernel is not None:
        _ocr_settings["output_open_kernel"] = max(0, min(10, body.output_open_kernel))
    _shadow_page_cache.clear()
    if save:
        _OCR_SETTINGS_PATH.write_text(
            json.dumps(_ocr_settings, indent=2), encoding="utf-8"
        )
    return _ocr_settings


# ---------------------------------------------------------------------------
# Shadow page cache — table line removal for cleaner OCR input
# ---------------------------------------------------------------------------
_shadow_page_cache: dict = {}


def _build_shadow_page(pil_image, settings: dict):
    """
    Line removal only — the original grayscale is preserved intact.

    Binarisation (adaptive threshold or Otsu) is used solely to make line
    detection reliable.  After the line mask is found, we revert to the
    original grayscale and paint only the detected line pixels white.
    EasyOCR therefore sees the natural scan, minus the table rules.
    """
    import cv2
    import numpy as np
    from PIL import Image as _PIL

    img = np.array(pil_image.convert("L"))   # original grayscale — final output base
    h, w = img.shape

    # ── Step 1: binarise for line detection only ──────────────────────────────
    if settings.get("use_adaptive_threshold", True):
        block = settings.get("adaptive_block_size", 15)
        if block % 2 == 0:
            block += 1
        binary = cv2.adaptiveThreshold(
            img, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            block, settings.get("adaptive_c", 10),
        )
        k_sz = settings.get("morph_open_kernel", 0)
        if k_sz > 0:
            _k     = cv2.getStructuringElement(cv2.MORPH_RECT, (k_sz, k_sz))
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, _k)
        morph_in = cv2.bitwise_not(binary)   # text/lines = 255, bg = 0
    else:
        # Otsu for line detection only
        _, morph_in = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # ── Step 2: detect long lines in the binarised image ─────────────────────
    frac   = settings.get("line_removal_fraction", 0.22)
    v_frac = settings.get("v_line_fraction", 0.0) or frac  # 0 means "use same as horizontal"

    # Optional: close gaps in dashed/dotted lines before detection
    close_k = settings.get("line_close_kernel", 0)
    detect_in = morph_in
    if close_k > 0:
        hc = cv2.getStructuringElement(cv2.MORPH_RECT, (close_k, 1))
        vc = cv2.getStructuringElement(cv2.MORPH_RECT, (1, close_k))
        detect_in = cv2.morphologyEx(detect_in, cv2.MORPH_CLOSE, hc)
        detect_in = cv2.morphologyEx(detect_in, cv2.MORPH_CLOSE, vc)

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, int(w * frac)),  1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(1, int(h * v_frac))))

    dilate_t = settings.get("line_dilate_thickness", 3)
    mask = cv2.dilate(
        cv2.bitwise_or(
            cv2.morphologyEx(detect_in, cv2.MORPH_OPEN, h_kernel),
            cv2.morphologyEx(detect_in, cv2.MORPH_OPEN, v_kernel),
        ),
        np.ones((dilate_t, dilate_t), np.uint8),
    )

    # ── Step 3: revert to original grayscale, paint line pixels white ─────────
    result = img.copy()
    result[mask > 0] = 255

    # ── Step 4 (optional): Gaussian blur to smooth noise ─────────────────────
    blur_sigma = settings.get("blur_sigma", 0)
    if blur_sigma > 0:
        result = cv2.GaussianBlur(result, (0, 0), blur_sigma)

    # ── Step 5 (optional): binarize the output image ──────────────────────────
    if settings.get("output_binarize", False):
        block = settings.get("adaptive_block_size", 15)
        if block % 2 == 0:
            block += 1
        result = cv2.adaptiveThreshold(
            result, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            block, settings.get("adaptive_c", 10),
        )

    # ── Step 6 (optional): morphological opening on output (speckle removal) ─
    out_open = settings.get("output_open_kernel", 0)
    if out_open > 0:
        _k = cv2.getStructuringElement(cv2.MORPH_RECT, (out_open, out_open))
        result = cv2.morphologyEx(result, cv2.MORPH_OPEN, _k)

    return _PIL.fromarray(result).convert("RGB")


def _get_shadow_page(folder: str, stem: str, img_path):
    """Return (and cache) the fully-preprocessed shadow page.
    Cache key includes every setting that affects the output."""
    from PIL import Image as _PIL
    s   = _ocr_settings
    key = (
        folder, stem,
        s["line_removal_fraction"],
        s.get("v_line_fraction", 0.0),
        s["use_adaptive_threshold"],
        s["adaptive_block_size"],
        s["adaptive_c"],
        s["morph_open_kernel"],
        s.get("line_close_kernel", 0),
        s.get("line_dilate_thickness", 3),
        s.get("blur_sigma", 0),
        s.get("output_binarize", False),
        s.get("output_open_kernel", 0),
    )
    if key not in _shadow_page_cache:
        if len(_shadow_page_cache) >= 10:
            _shadow_page_cache.clear()
        full = _PIL.open(str(img_path)).convert("RGB")
        _shadow_page_cache[key] = _build_shadow_page(full, dict(s))
    return _shadow_page_cache[key]


@app.get("/api/shadow-preview")
def api_shadow_preview(folder: str = Query(...), stem: str = Query(...)):
    """Return the fully preprocessed shadow page as JPEG (OCR view preview)."""
    import io as _io

    d        = _resolve_folder(folder)
    img_path = _find_image(d, stem)
    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    shadow = _get_shadow_page(folder, stem, img_path)

    buf = _io.BytesIO()
    shadow.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg",
                             headers={"Cache-Control": "no-cache, no-store"})


# ---------------------------------------------------------------------------
# EasyOCR — cached reader (initialisation takes a few seconds the first time)
# ---------------------------------------------------------------------------
_easyocr_reader = None

def _get_easyocr_reader(langs: list[str]):
    global _easyocr_reader
    import easyocr, io, sys
    key = tuple(sorted(langs))
    if _easyocr_reader is None or _easyocr_reader[0] != key:
        # Suppress stdout/stderr during init to avoid cp1250 UnicodeEncodeError
        # from EasyOCR's progress-bar block characters on Windows
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = io.StringIO()
        try:
            reader = easyocr.Reader(langs, gpu=True)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        _easyocr_reader = (key, reader)
    return _easyocr_reader[1]


@app.post("/api/page/shape/ocr/easyocr")
def api_ocr_easyocr(
    folder: str = Query(...),
    stem:   str = Query(...),
    idx:    int = Query(...),
    langs:  str = Query("en,hu", description="Comma-separated EasyOCR language codes"),
):
    """Run EasyOCR on a single cell and store the result in the page JSON."""
    import numpy as np
    from PIL import Image as PILImage

    d        = _resolve_folder(folder)
    jf       = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    data_doc = json.loads(jf.read_text(encoding="utf-8"))
    shapes   = data_doc.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    pts = shapes[idx]["points"]
    xs  = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    shadow = _get_shadow_page(folder, stem, img_path)
    iw, ih = shadow.size
    pad    = 4
    crop   = shadow.crop((
        max(0, int(x1) - pad), max(0, int(y1) - pad),
        min(iw, int(x2) + pad), min(ih, int(y2) + pad),
    )).convert("RGB")

    lang_list = [l.strip() for l in langs.split(",") if l.strip()]
    reader    = _get_easyocr_reader(lang_list)

    results = reader.readtext(
        np.array(crop),
        allowlist="0123456789-",
        min_size=6,          # catch single-digit cells (default 20 misses them)
        text_threshold=0.5,  # slightly more permissive for sparse content
        low_text=0.3,
    )
    # results: [([[x1,y1],[x2,y2],[x3,y3],[x4,y4]], text, confidence), ...]
    # Sort top-to-bottom by the minimum y of each bounding box
    results.sort(key=lambda r: min(pt[1] for pt in r[0]))

    lines      = [text for _, text, _ in results]
    confs      = [conf for _, _, conf in results]
    ocr_text   = "\n".join(lines).strip()
    mean_conf  = round(sum(confs) / len(confs) * 100, 1) if confs else 0.0

    shapes[idx]["tesseract_output"] = {
        "ocr_text":  ocr_text,
        "mean_conf": mean_conf,
        "engine":    "easyocr",
        "langs":     lang_list,
    }
    jf.write_text(json.dumps(data_doc, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"ocr_text": ocr_text, "mean_conf": mean_conf}


@app.post("/api/page/shape/ocr/linebyline")
async def api_ocr_linebyline(
    folder:      str = Query(...),
    stem:        str = Query(...),
    idx:         int = Query(...),
    cell_height: int = Query(26),
    lang:        str = Query("hun"),
):
    """
    Row-by-row Tesseract OCR with digit/dash whitelist.
    Uses the same row-detection and SSE streaming as the LLM line-by-line endpoint.
    Each row is read with --psm 7 and tessedit_char_whitelist=0123456789-
    Results are saved to shape['tesseract_output'] and streamed as SSE.
    """
    import asyncio, concurrent.futures, shutil
    import json as _json
    import pytesseract
    from PIL import Image as PILImage, ImageOps

    if not shutil.which("tesseract"):
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    d        = _resolve_folder(folder)
    jf       = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    data_doc = json.loads(jf.read_text(encoding="utf-8"))
    shapes   = data_doc.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    shape = shapes[idx]
    pts   = shape["points"]
    xs    = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    shadow   = _get_shadow_page(folder, stem, img_path)
    iw, ih   = shadow.size
    pad      = 4
    crop     = shadow.crop((
        max(0, int(x1) - pad), max(0, int(y1) - pad),
        min(iw, int(x2) + pad), min(ih, int(y2) + pad),
    ))

    rows = _detect_text_rows(crop, cell_height)

    tess_config = "--psm 7 -c tessedit_char_whitelist=0123456789-"

    def gen():
        yield _json.dumps({"type": "lines_detected", "count": len(rows),
                           "lines": [list(r) for r in rows]})

        line_texts: list[str] = []
        conf_values: list[float] = []

        for i, (top, bottom) in enumerate(rows):
            row_pad = max(4, cell_height // 6)
            rt = max(0, top - row_pad)
            rb = min(crop.height, bottom + row_pad)
            row_img = crop.crop((0, rt, crop.width, rb))

            # Upscale small rows
            if row_img.height < 48:
                scale   = 48 / row_img.height
                row_img = row_img.resize(
                    (int(row_img.width * scale), 48), PILImage.LANCZOS
                )

            grey = ImageOps.autocontrast(row_img.convert("L"))

            try:
                tess = pytesseract.image_to_data(
                    grey, lang=lang, config=tess_config,
                    output_type=pytesseract.Output.DICT,
                )
                words = [
                    (txt, int(conf))
                    for txt, conf in zip(tess["text"], tess["conf"])
                    if txt.strip() and int(conf) > 0
                ]
                text = " ".join(t for t, _ in words).strip() or "-"
                conf = round(sum(c for _, c in words) / len(words), 1) if words else 0.0
            except Exception as exc:
                text = f"[err: {exc}]"
                conf = 0.0

            line_texts.append(text)
            conf_values.append(conf)
            yield _json.dumps({"type": "row_result", "row": i, "text": text,
                               "top": top, "bottom": bottom})

        combined  = "\n".join(line_texts)
        mean_conf = round(sum(conf_values) / len(conf_values), 1) if conf_values else 0.0

        shape["tesseract_output"] = {
            "ocr_text":  combined,
            "mean_conf": mean_conf,
            "lang":      lang,
            "mode":      "linebyline",
        }
        jf.write_text(_json.dumps(data_doc, indent=2, ensure_ascii=False),
                      encoding="utf-8")

        yield _json.dumps({"type": "done", "ocr_text": combined, "mean_conf": mean_conf})

    async def event_gen():
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            it = iter(gen())
            while True:
                item = await loop.run_in_executor(pool, _safe_next, it)
                if item is _SSE_DONE:
                    break
                yield f"data: {item}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/page/shape/ocr/easyocr/linebyline")
async def api_ocr_easyocr_linebyline(
    folder:      str = Query(...),
    stem:        str = Query(...),
    idx:         int = Query(...),
    cell_height: int = Query(26),
    langs:       str = Query("en,hu"),
):
    """Row-by-row EasyOCR using the comb-filter row detector. Streams SSE."""
    import asyncio, concurrent.futures
    import json as _json
    import numpy as np
    from PIL import Image as PILImage, ImageOps

    d        = _resolve_folder(folder)
    jf       = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    data_doc = json.loads(jf.read_text(encoding="utf-8"))
    shapes   = data_doc.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    shape = shapes[idx]
    pts   = shape["points"]
    xs    = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    shadow   = _get_shadow_page(folder, stem, img_path)
    iw, ih   = shadow.size
    pad      = 4
    crop     = shadow.crop((
        max(0, int(x1) - pad), max(0, int(y1) - pad),
        min(iw, int(x2) + pad), min(ih, int(y2) + pad),
    ))

    rows      = _detect_text_rows(crop, cell_height)
    lang_list = [l.strip() for l in langs.split(",") if l.strip()]
    reader    = _get_easyocr_reader(lang_list)

    # crop is already fully preprocessed (threshold + line removal) by _get_shadow_page
    crop_bin = crop.convert("L")

    def gen():
        yield _json.dumps({"type": "lines_detected", "count": len(rows),
                           "lines": [list(r) for r in rows]})

        line_texts:  list[str]   = []
        conf_values: list[float] = []

        for i, (top, bottom) in enumerate(rows):
            row_pad = max(4, cell_height // 6)
            # Clamp padding to the midpoint between adjacent rows so we never
            # bleed content from the row above or below into this slice
            prev_bottom = rows[i - 1][1] if i > 0 else 0
            next_top    = rows[i + 1][0] if i < len(rows) - 1 else crop_bin.height
            rt = max(top    - row_pad, (prev_bottom + top)    // 2)
            rb = min(bottom + row_pad, (bottom      + next_top) // 2)
            rt = max(0, rt)
            rb = min(crop_bin.height, rb)
            row_img = crop_bin.crop((0, rt, crop_bin.width, rb))

            # Upscale with LANCZOS — soft edges match EasyOCR's training distribution
            # better than blocky NEAREST, which can make digit strokes look like
            # separate characters to the CRAFT detector
            target_h = 128
            scale    = target_h / row_img.height
            row_img  = row_img.resize(
                (int(row_img.width * scale), target_h), PILImage.LANCZOS
            ).convert("RGB")

            # Encode preprocessed image for the frontend preview panel
            import io as _io2, base64 as _b64
            _buf = _io2.BytesIO()
            row_img.save(_buf, format="PNG")
            img_b64 = _b64.b64encode(_buf.getvalue()).decode("ascii")

            try:
                results = reader.readtext(
                    np.array(row_img),
                    allowlist="0123456789-",
                    min_size=4,
                    text_threshold=0.4,
                    low_text=0.3,
                )
                results.sort(key=lambda r: min(pt[1] for pt in r[0]))
                texts = [t for _, t, _ in results]
                confs = [c for _, _, c in results]
                text  = " ".join(texts).strip()
                conf  = round(sum(confs) / len(confs) * 100, 1) if confs else 0.0
            except Exception as exc:
                text = ""
                conf = 0.0

            text = text or "-"

            line_texts.append(text)
            conf_values.append(conf)
            yield _json.dumps({"type": "row_result", "row": i, "text": text,
                               "img_b64": img_b64,
                               "top": top, "bottom": bottom})

        combined  = "\n".join(line_texts)
        mean_conf = round(sum(conf_values) / len(conf_values), 1) if conf_values else 0.0

        shape["tesseract_output"] = {
            "ocr_text":  combined,
            "mean_conf": mean_conf,
            "engine":    "easyocr",
            "mode":      "linebyline",
        }
        jf.write_text(_json.dumps(data_doc, indent=2, ensure_ascii=False),
                      encoding="utf-8")

        yield _json.dumps({"type": "done", "ocr_text": combined, "mean_conf": mean_conf})

    async def event_gen():
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            it = iter(gen())
            while True:
                item = await loop.run_in_executor(pool, _safe_next, it)
                if item is _SSE_DONE:
                    break
                yield f"data: {item}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Routes — LLM cleaner
# ---------------------------------------------------------------------------

def _detect_text_rows(crop_image, cell_height: int = 28) -> list[tuple[int, int]]:
    """
    Comb-filter phase search + sequential gap snap.

    Pass 1 — comb filter finds the best global starting offset (phase).
    Pass 2 — each subsequent boundary is placed at prev + cell_height, then
    nudged ±snap_r pixels to the nearest local ink minimum (inter-row gap).
    Because each snap is relative to the previous boundary (not the ideal
    grid), corrections compound and any amount of cumulative drift is tracked,
    while the small snap_r prevents jumping over sparse rows (dashes/blanks).
    """
    import numpy as np

    gray       = np.array(crop_image.convert("L"))
    binary     = (gray < 180).astype(np.float32)
    projection = binary.sum(axis=1)

    roi_h = len(projection)
    if roi_h < cell_height:
        return [(0, roi_h)] if roi_h > 0 else []

    # Smooth projection — damps isolated ink blobs without washing out gaps
    k        = max(3, cell_height // 6)
    smoothed = np.convolve(projection, np.ones(k) / k, mode='same')

    cum = np.concatenate(([0.0], np.cumsum(projection)))

    def window_sum(t: int, b: int) -> float:
        t = max(0, t); b = min(roi_h, b)
        return float(cum[b] - cum[t]) if b > t else 0.0

    # Pass 1: find best phase via comb filter
    best_score, best_start = -1.0, 0
    for start in range(cell_height):
        score = 0.0; top = start
        while top + cell_height <= roi_h:
            score += window_sum(top, top + cell_height); top += cell_height
        if score > best_score:
            best_score = score; best_start = start

    # Pass 2: sequential snap — small radius, relative to previous boundary
    snap_r   = max(2, cell_height // 6)   # ~4 px at default 26 px height
    snapped  = [best_start]               # first boundary: trust comb filter

    while True:
        expected = snapped[-1] + cell_height
        if expected > roi_h:
            break
        lo  = max(0,        expected - snap_r)
        hi  = min(roi_h - 1, expected + snap_r)
        new = (lo + int(np.argmin(smoothed[lo: hi + 1]))) if lo < hi else expected
        new = max(new, snapped[-1] + 1)   # strict monotonicity
        snapped.append(new)

    # Partial last row only if ≥ 60 % of cell_height remains
    if snapped and roi_h - snapped[-1] >= cell_height * 0.6:
        snapped.append(roi_h)

    rows = [
        (snapped[i], min(snapped[i + 1], roi_h))
        for i in range(len(snapped) - 1)
        if snapped[i + 1] > snapped[i] and snapped[i] < roi_h
    ]
    return rows


class LlmRequest(BaseModel):
    prompt: str


# Models served locally via ollama (OpenAI-compatible endpoint)
_LOCAL_MODELS: set[str] = {"qwen2.5vl:7b"}

# Always appended to every LLM prompt to suppress hallucinations on empty/dash cells
_EMPTY_CELL_GUARD = (
    "\nIf the cell is empty, contains only a dash, or the image shows no readable content, "
    "return exactly -."
)

def _make_llm_client(model: str):
    """Return an OpenAI-compatible client for the given model.
    Local models are routed to the ollama server; all others go to OpenAI."""
    import os
    from openai import OpenAI
    if model in _LOCAL_MODELS:
        host = os.environ.get("OLLAMA_HOST", "http://gpu.koren.work:11434")
        return OpenAI(api_key="ollama", base_url=f"{host}/v1")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY environment variable is not set")
    return OpenAI(api_key=api_key)


@app.post("/api/page/shape/llm")
def api_llm_cell(
    folder:     str  = Query(...),
    stem:       str  = Query(...),
    idx:        int  = Query(...),
    model:      str  = Query("gpt-4o-mini"),
    mode:       str  = Query("image", description="image | image+ocr | ocr | linebyline"),
    use_shadow: bool = Query(False, description="Use OCR shadow (line-erased) image instead of original"),
    dry_run:    bool = Query(False, description="Return result without writing to JSON (for testing)"),
    body:       LlmRequest = ...,
):
    """Send a cell to an LLM and store the result in the page JSON."""
    import os, base64, io, datetime
    from PIL import Image as PILImage

    d        = _resolve_folder(folder)
    jf       = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None and mode in ("image", "image+ocr"):
        raise HTTPException(status_code=404, detail="Image not found")

    data   = json.loads(jf.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    shape = shapes[idx]
    pts   = shape["points"]
    xs    = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    # Build the message content
    content: list = []

    if mode in ("image", "image+ocr"):
        if use_shadow:
            img = _get_shadow_page(folder, stem, img_path).convert("RGB")
        else:
            img = PILImage.open(str(img_path)).convert("RGB")
        w, h = img.size
        pad  = 4
        crop = img.crop((
            max(0, int(x1) - pad), max(0, int(y1) - pad),
            min(w, int(x2) + pad), min(h, int(y2) + pad),
        ))
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=92)
        b64 = base64.b64encode(buf.getvalue()).decode()
        content.append({
            "type":      "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
        })

    prompt_text = body.prompt + _EMPTY_CELL_GUARD
    if mode == "image+ocr":
        ocr_text = shape.get("tesseract_output", {}).get("ocr_text", "")
        if ocr_text:
            prompt_text = f"{body.prompt}\n\nOCR text:\n{ocr_text}"
    elif mode == "ocr":
        ocr_text = shape.get("tesseract_output", {}).get("ocr_text", "")
        prompt_text = f"{body.prompt}\n\n{ocr_text}"

    content.append({"type": "text", "text": prompt_text})

    # For text-only mode, skip the content list and use a plain string
    if mode == "ocr":
        messages = [{"role": "user", "content": prompt_text}]
    else:
        messages = [{"role": "user", "content": content}]

    print(f"[LLM] model={model} mode={mode} dry_run={dry_run} "
          f"prompt={prompt_text!r}", flush=True)

    try:
        client   = _make_llm_client(model)
        response = client.chat.completions.create(
            model=model, messages=messages, max_tokens=1024,
            # temperature=0 → fully deterministic; re-running the same cell always
            # yields the same output.  Use a small positive value so that:
            #   (a) repeated calls can differ, proving the API is actually invoked,
            #   (b) the model has a tiny bit of freedom to self-correct.
            temperature=0.2,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    raw_content = response.choices[0].message.content
    result      = (raw_content or "").strip()
    tokens_in   = response.usage.prompt_tokens     if response.usage else 0
    tokens_out  = response.usage.completion_tokens if response.usage else 0
    print(f"[LLM] result={result!r}  tokens={tokens_in}→{tokens_out}", flush=True)
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"

    if not dry_run:
        shape["openai_output"] = {
            "response":  result,
            "model":     model,
            "mode":      mode,
            "timestamp": timestamp,
        }
        jf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"response": result, "model": model, "mode": mode,
            "timestamp": timestamp, "prompt_sent": prompt_text,
            "tokens_in": tokens_in, "tokens_out": tokens_out}


@app.post("/api/page/shape/llm/linebyline")
async def api_llm_linebyline(
    folder:      str  = Query(...),
    stem:        str  = Query(...),
    idx:         int  = Query(...),
    model:       str  = Query("gpt-4o-mini"),
    cell_height: int  = Query(28, description="Expected height of one text row in pixels"),
    use_shadow:  bool = Query(False, description="Use OCR shadow (line-erased) image instead of original"),
    dry_run:     bool = Query(False, description="Return result without writing to JSON (for testing)"),
    body:        LlmRequest = ...,
):
    """
    Line-by-line LLM: slice the cell into rows using pixel projection, send each
    row image to OpenAI individually, and stream results as SSE.

    SSE message types:
      {"type": "lines_detected", "count": N, "lines": [[top,bottom], ...]}
      {"type": "row_result",     "row": i,  "text": "...", "top": t, "bottom": b}
      {"type": "done",           "response": "...", "model": "...", "timestamp": "..."}
    """
    import os, asyncio, base64, io as _io, datetime, concurrent.futures
    import json as _json
    from PIL import Image as PILImage

    d        = _resolve_folder(folder)
    jf       = d / f"{stem}.json"
    img_path = _find_image(d, stem)

    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    data_doc = json.loads(jf.read_text(encoding="utf-8"))
    shapes   = data_doc.get("shapes", [])
    if idx < 0 or idx >= len(shapes):
        raise HTTPException(status_code=400, detail="Shape index out of range")

    shape = shapes[idx]
    pts   = shape["points"]
    xs    = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)

    if use_shadow:
        full_img = _get_shadow_page(folder, stem, img_path).convert("RGB")
    else:
        full_img = PILImage.open(str(img_path)).convert("RGB")
    iw, ih   = full_img.size
    pad      = 4
    crop     = full_img.crop((
        max(0, int(x1) - pad), max(0, int(y1) - pad),
        min(iw, int(x2) + pad), min(ih, int(y2) + pad),
    ))

    rows = _detect_text_rows(crop, cell_height)
    prompt_text = body.prompt + _EMPTY_CELL_GUARD

    def gen():
        yield _json.dumps({"type": "lines_detected", "count": len(rows),
                           "lines": [list(r) for r in rows]})

        print(f"[LLM/linebyline] model={model} rows={len(rows)} dry_run={dry_run} "
              f"prompt={prompt_text!r}", flush=True)

        client = _make_llm_client(model)
        line_responses: list[str] = []

        for i, (top, bottom) in enumerate(rows):
            # Add a few pixels of vertical breathing room so ascenders /
            # descenders aren't clipped, then upscale very small rows so the
            # LLM can read digits reliably.
            row_pad = max(4, cell_height // 6)
            rt = max(0, top    - row_pad)
            rb = min(crop.height, bottom + row_pad)
            row_img = crop.crop((0, rt, crop.width, rb))
            # Upscale: aim for at least 48 px tall
            if row_img.height < 48:
                scale   = 48 / row_img.height
                row_img = row_img.resize(
                    (int(row_img.width * scale), 48), PILImage.LANCZOS
                )
            buf = _io.BytesIO()
            row_img.save(buf, format="JPEG", quality=92)
            b64 = base64.b64encode(buf.getvalue()).decode()

            messages = [{
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": prompt_text},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                   "detail": "high"}},
                ],
            }]
            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=64, temperature=0,
                )
                text = (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                text = f"[error: {exc}]"

            line_responses.append(text)
            yield _json.dumps({"type": "row_result", "row": i, "text": text,
                               "top": top, "bottom": bottom})

        combined  = "\n".join(line_responses)
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"

        if not dry_run:
            shape["openai_output"] = {
                "response":       combined,
                "model":          model,
                "mode":           "linebyline",
                "timestamp":      timestamp,
                "lines_detected": len(rows),
            }
            jf.write_text(_json.dumps(data_doc, indent=2, ensure_ascii=False),
                          encoding="utf-8")

        yield _json.dumps({"type": "done", "response": combined,
                           "model": model, "timestamp": timestamp,
                           "prompt_sent": prompt_text})

    # Stream the sync generator as SSE
    async def event_gen():
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            it = iter(gen())
            while True:
                item = await loop.run_in_executor(pool, _safe_next, it)
                if item is _SSE_DONE:
                    break
                yield f"data: {item}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Routes — project management
# ---------------------------------------------------------------------------

@app.get("/api/projects")
def api_list_projects():
    return {"projects": list_projects()}


@app.get("/api/project/{name}")
def api_get_project(name: str):
    try:
        cfg   = load_config(name)
        state = load_pipeline(name)
        pdir  = project_dir(name)
        ann   = pdir / "annotations"
        n_json = len(list(ann.glob("*.json"))) if ann.exists() else 0
        n_img  = len(list(ann.glob("*.png")) + list(ann.glob("*.jpg"))) if ann.exists() else 0
        return {
            "config":  cfg,
            "pipeline": state,
            "stats":   {"json": n_json, "images": n_img},
            "stages":  stages_for(cfg["type"]),
            "annotations_path": str(ann),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ConfigUpdate(BaseModel):
    server: Optional[dict] = None
    llm:    Optional[dict] = None
    labels: Optional[list] = None


@app.patch("/api/project/{name}/config")
def api_update_config(name: str, body: ConfigUpdate):
    try:
        cfg  = load_config(name)
        pdir = project_dir(name)
        if body.server is not None:
            cfg["server"] = {**cfg.get("server", {}), **body.server}
        if body.llm is not None:
            cfg["llm"] = {**cfg.get("llm", {}), **body.llm}
        if body.labels is not None:
            cfg["labels"] = body.labels
        (pdir / "config.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {"ok": True, "config": cfg}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


class NewProject(BaseModel):
    name:   str
    type:   str = "A"
    labels: list = []

@app.post("/api/project/new")
def api_new_project(body: NewProject):
    from app.pipeline import create_project
    try:
        pdir = create_project(body.name, body.type, body.labels)
        return {"ok": True, "path": str(pdir)}
    except (FileExistsError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


class CloneProject(BaseModel):
    new_name: str

@app.post("/api/project/{name}/clone")
def api_clone_project(name: str, body: CloneProject):
    from app.pipeline import clone_project
    try:
        pdir = clone_project(name, body.new_name)
        return {"ok": True, "path": str(pdir)}
    except (FileNotFoundError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/project/{name}/advance")
def api_advance(name: str):
    try:
        new_stage = advance_stage(name)
        return {"ok": True, "stage": new_stage}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/project/{name}/set-stage")
def api_set_stage(name: str, stage: str = Query(...)):
    try:
        set_stage(name, stage)
        return {"ok": True, "stage": stage}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Docker container configuration
# ---------------------------------------------------------------------------
from app import docker_config as _docker_cfg_mod

def _docker_cfg() -> dict:
    return _docker_cfg_mod.load()

def _predict_container() -> str:
    return _docker_cfg()["predict_container"]

def _train_container() -> str:
    return _docker_cfg()["train_container"]


class DockerConfigBody(BaseModel):
    predict_container: Optional[str] = None
    train_container:   Optional[str] = None
    image_name:        Optional[str] = None


@app.get("/api/docker-config")
def api_get_docker_config():
    return _docker_cfg()


@app.post("/api/docker-config")
def api_set_docker_config(body: DockerConfigBody):
    update = {k: v for k, v in body.dict().items() if v is not None}
    return _docker_cfg_mod.save(update)


@app.get("/api/docker-config/status")
def api_docker_container_status(body: SshRequest = None,
                                passphrase: Optional[str] = Query(None)):
    """Check whether predict/train containers exist on the GPU server.
    Returns exists/running status for each. Requires a project with server cfg."""
    # Use the first available project's server config
    from app import ssh_ops
    projects = list_projects()
    if not projects:
        raise HTTPException(status_code=400, detail="No projects configured")
    try:
        srv = _server_cfg(projects[0])
    except HTTPException:
        raise HTTPException(status_code=400, detail="Server not configured on any project")

    pw = passphrase or ""
    cfg = _docker_cfg()
    result = {}
    for role, cname in [("predict", cfg["predict_container"]),
                        ("train",   cfg["train_container"])]:
        try:
            c = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], pw)
            _, out, _ = c.exec_command(
                f"docker ps -a --filter name=^{cname}$ --format '{{{{.Status}}}}'"
            )
            status_str = out.read().decode().strip()
            c.close()
            if not status_str:
                result[role] = {"name": cname, "exists": False, "running": False}
            else:
                result[role] = {"name": cname, "exists": True,
                                "running": status_str.lower().startswith("up")}
        except Exception as e:
            result[role] = {"name": cname, "exists": None, "error": str(e)}
    return result


@app.post("/api/docker-config/build")
async def api_docker_build(passphrase: Optional[str] = Query(None),
                           role: str = Query("predict")):
    """Build the econai-layout image on the GPU server and create the named container.
    role: 'predict' | 'train' | 'both'
    Streams SSE log lines."""
    from app import ssh_ops
    import posixpath as pp

    projects = list_projects()
    if not projects:
        raise HTTPException(status_code=400, detail="No projects configured")
    try:
        srv = _server_cfg(projects[0])
    except HTTPException:
        raise HTTPException(status_code=400, detail="Server not configured")

    cfg      = _docker_cfg()
    image    = cfg["image_name"]
    pw       = passphrase or ""

    # Read the local Dockerfile and upload it
    dockerfile_path = Path(__file__).parent.parent / "Dockerfile"
    if not dockerfile_path.exists():
        raise HTTPException(status_code=500, detail="Dockerfile not found in repo root")
    dockerfile_content = dockerfile_path.read_text(encoding="utf-8")

    # Determine which containers to create
    roles_to_build = []
    if role in ("predict", "both"):
        roles_to_build.append(("predict", cfg["predict_container"]))
    if role in ("train", "both"):
        roles_to_build.append(("train", cfg["train_container"]))
    # Deduplicate — same name = only create once
    seen = set()
    roles_unique = []
    for r, n in roles_to_build:
        if n not in seen:
            seen.add(n)
            roles_unique.append((r, n))

    def build_gen():
        c = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], pw)
        sftp = c.open_sftp()
        try:
            # Upload Dockerfile to server
            remote_df = "/tmp/econai_Dockerfile"
            yield f"[docker] Uploading Dockerfile to {remote_df}..."
            with sftp.open(remote_df, "w") as f:
                f.write(dockerfile_content)
            sftp.close()

            # Build image
            yield f"[docker] Building image '{image}' (this can take 10-30 min)..."
            build_cmd = f"docker build -t {image} -f {remote_df} /tmp"
            yield from ssh_ops.stream_command(
                srv["host"], srv["user"], srv["key_path"], build_cmd, pw)

            # Detect workspace bind from existing predict container (if any)
            _, _out, _ = c.exec_command(
                f"docker inspect {cfg['predict_container']} "
                "--format '{{range .HostConfig.Binds}}{{println .}}{{end}}' 2>/dev/null"
            )
            workspace_host = None
            for _bind in _out.read().decode().strip().splitlines():
                _parts = _bind.strip().split(":")
                if len(_parts) >= 2 and _parts[1].rstrip("/") == "/workspace":
                    workspace_host = _parts[0].rstrip("/")
                    break
            if not workspace_host:
                workspace_host = srv.get("remote_path", "").rstrip("/")
            yield f"[docker] workspace host path: {workspace_host}"

            # Create each new container
            for _role, cname in roles_unique:
                _, chk, _ = c.exec_command(
                    f"docker ps -a --filter name=^{cname}$ --format '{{{{.Names}}}}'"
                )
                if chk.read().decode().strip():
                    yield f"[docker] Container '{cname}' already exists — skipping create."
                    continue
                yield f"[docker] Creating container '{cname}'..."
                create_cmd = (
                    f"docker create --name {cname} --gpus all --shm-size=8g "
                    f"-v {workspace_host}:/workspace {image}"
                )
                _, co, ce = c.exec_command(create_cmd)
                out_txt = co.read().decode().strip()
                err_txt = ce.read().decode().strip()
                if out_txt:
                    yield f"[docker] Created: {out_txt[:64]}"
                if err_txt:
                    yield f"[docker] {err_txt}"

            yield "[docker] Done."
        finally:
            try:
                c.close()
            except Exception:
                pass

    return await _sse_stream(build_gen())


# ---------------------------------------------------------------------------
# Routes — SSH / server operations
# ---------------------------------------------------------------------------

def _server_cfg(name: str) -> dict:
    cfg = load_config(name)
    srv = cfg.get("server", {})
    if not srv.get("host"):
        raise HTTPException(status_code=400, detail="Server host not configured")
    if not srv.get("user"):
        raise HTTPException(status_code=400, detail="Server user not configured")
    if not srv.get("key_path"):
        raise HTTPException(status_code=400, detail="Server key_path not configured")
    return srv


class SshRequest(BaseModel):
    passphrase: Optional[str] = None

class SshPullRequest(BaseModel):
    passphrase: Optional[str] = None
    subfolder:  str = "annotations"

class JobSubmit(BaseModel):
    command:    str
    passphrase: Optional[str] = None


@app.post("/api/project/{name}/server/test")
def api_server_test(name: str, body: SshRequest = SshRequest()):
    from app import ssh_ops
    try:
        srv = _server_cfg(name)
        return ssh_ops.test_connection(srv["host"], srv["user"], srv["key_path"], body.passphrase)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/server/push")
def api_server_push(name: str, body: SshRequest = SshRequest()):
    """Upload the project's annotations/ folder to the server."""
    from app import ssh_ops
    try:
        srv  = _server_cfg(name)
        pdir = project_dir(name)
        local_ann  = pdir / "annotations"
        remote_ann = posixpath.join(srv["remote_path"], name, "annotations")
        if not local_ann.exists():
            raise HTTPException(status_code=400, detail="annotations/ folder does not exist")
        return ssh_ops.push_folder(
            srv["host"], srv["user"], srv["key_path"],
            local_ann, remote_ann, body.passphrase,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/server/pull")
def api_server_pull(name: str, body: SshPullRequest = SshPullRequest()):
    """Download a subfolder (default: annotations/) back from the server."""
    from app import ssh_ops
    try:
        srv  = _server_cfg(name)
        pdir = project_dir(name)
        remote = posixpath.join(srv["remote_path"], name, body.subfolder)
        local  = pdir / body.subfolder
        return ssh_ops.pull_folder(
            srv["host"], srv["user"], srv["key_path"],
            remote, local, body.passphrase,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/job/submit")
def api_job_submit(name: str, body: JobSubmit):
    """Submit an arbitrary shell command as a background job on the server."""
    from app import ssh_ops
    import time as _time
    try:
        srv      = _server_cfg(name)
        log_path = posixpath.join(
            srv["remote_path"], name, f"job_{int(_time.time())}.log"
        )
        result = ssh_ops.submit_job(
            srv["host"], srv["user"], srv["key_path"],
            body.command, log_path, body.passphrase,
        )
        if result["ok"]:
            state = load_pipeline(name)
            state["job"] = {"pid": result["pid"], "log_path": log_path,
                            "command": body.command}
            save_pipeline(name, state)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/project/{name}/job/status")
def api_job_status(name: str, passphrase: Optional[str] = Query(None)):
    """Poll the status of the last submitted background job."""
    from app import ssh_ops
    try:
        srv   = _server_cfg(name)
        state = load_pipeline(name)
        job   = state.get("job")
        if not job:
            return {"ok": True, "running": False, "log_tail": "(no job submitted yet)"}
        return ssh_ops.job_status(
            srv["host"], srv["user"], srv["key_path"],
            job.get("pid"), job["log_path"], passphrase,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Routes — page import
# ---------------------------------------------------------------------------

class ImportRequest(BaseModel):
    source_path: str  # local folder or single file path

@app.post("/api/project/{name}/import")
def api_import_pages(name: str, body: ImportRequest):
    """Import PDFs/images from a local path directly — no upload needed."""
    from app.page_import import import_from_path
    try:
        pdir    = project_dir(name)
        ann_dir = pdir / "annotations"
        results = import_from_path(Path(body.source_path), ann_dir)
        return {"ok": True, "pages": results, "total": len(results)}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())


@app.post("/api/project/{name}/upload-pages")
async def api_upload_pages(name: str, files: List[UploadFile] = File(...)):
    """Accept uploaded PDF/image files, convert them and add to the project's annotations/ folder."""
    import tempfile
    from app.page_import import _import_pdf, _save_image_file, _sanitize, IMAGE_EXTS

    try:
        pdir    = project_dir(name)
        ann_dir = pdir / "annotations"
        ann_dir.mkdir(parents=True, exist_ok=True)

        src_dir = pdir / "sources"
        src_dir.mkdir(exist_ok=True)

        results = []
        for uf in files:
            filename  = uf.filename or "upload"
            orig_stem = Path(filename).stem
            ext       = Path(filename).suffix.lower()

            content  = await uf.read()
            tmp_path = None
            try:
                if ext == ".pdf":
                    # Save PDF permanently so pdf_source stays valid for text-layer extraction
                    saved_pdf = src_dir / filename
                    saved_pdf.write_bytes(content)
                    base  = _sanitize(orig_stem)
                    pages = _import_pdf(saved_pdf, ann_dir, base=base)
                    results.extend(pages)
                elif ext in IMAGE_EXTS:
                    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                        tmp.write(content)
                        tmp_path = Path(tmp.name)
                    stem = _sanitize(orig_stem)
                    info = _save_image_file(tmp_path, ann_dir, stem)
                    results.append(info)
                else:
                    results.append({"stem": filename, "error": f"Unsupported extension: {ext}"})
            except Exception as e:
                import traceback
                results.append({"stem": filename, "error": traceback.format_exc()})
            finally:
                if tmp_path and tmp_path.exists():
                    tmp_path.unlink()

        return {"ok": True, "pages": results, "total": len(results)}
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())


# ---------------------------------------------------------------------------
# Routes — prepare training data (LabelMe → COCO)
# ---------------------------------------------------------------------------

BASE_YAML = Path(__file__).parent.parent / "samples" / "ertesito2" / "fast_rcnn_R_50_FPN_3x.yaml"

@app.post("/api/project/{name}/prepare")
def api_prepare(name: str):
    """Convert annotated LabelMe JSONs → COCO JSON + generate training scripts."""
    from app.coco_convert import prepare_training_data
    try:
        cfg  = load_config(name)
        pdir = project_dir(name)
        result = prepare_training_data(
            project_name    = name,
            ann_dir         = pdir / "annotations",
            labels          = cfg["labels"],
            intermediate_dir= pdir / "intermediate",
            base_yaml_path  = BASE_YAML,
        )
        return {"ok": True, **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Routes — train / infer with SSE streaming
# ---------------------------------------------------------------------------

class TrainRequest(BaseModel):
    passphrase: Optional[str] = None

class InferRequest(BaseModel):
    passphrase:          Optional[str] = None
    skip_image_upload:   bool = False


_SSE_DONE = object()


def _safe_next(it):
    """Return next item, or _SSE_DONE sentinel — never raises StopIteration."""
    try:
        return next(it)
    except StopIteration:
        return _SSE_DONE


async def _sse_stream(gen):
    """Wrap a sync generator as an SSE response."""
    import asyncio
    import json as _json

    async def event_gen():
        loop = asyncio.get_event_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            it = iter(gen)
            while True:
                try:
                    line = await loop.run_in_executor(pool, _safe_next, it)
                    if line is _SSE_DONE:
                        yield "data: {\"done\": true}\n\n"
                        break
                    payload = _json.dumps({"line": line.rstrip("\n")})
                    yield f"data: {payload}\n\n"
                except Exception as e:
                    yield f"data: {_json.dumps({'error': str(e)})}\n\n"
                    break

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _push_training_data_gen(name: str, srv: dict, passphrase: str):
    """Generator: push images + COCO JSON + config + scripts, yielding progress lines."""
    from app import ssh_ops
    pdir   = project_dir(name)
    inter  = pdir / "intermediate"
    remote = srv["remote_path"].rstrip("/")

    yield f"[push] remote_path = {remote}"
    yield f"[push] Connecting to {srv['host']} as {srv['user']}..."
    c = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], passphrase)
    sftp = c.open_sftp()
    try:
        dirs = [
            f"{remote}/{name}/images",
            f"{remote}/layout-model-training/configs/{name}",
            f"{remote}/layout-model-training/tools",
            f"{remote}/layout-model-training/scripts",
        ]
        for d in dirs:
            yield f"[push] mkdir -p {d}"
            ssh_ops._sftp_mkdir_p(sftp, d)

        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
        images = [p for p in (pdir / "annotations").iterdir()
                  if p.suffix.lower() in IMAGE_EXTS]
        total = len(images)
        yield f"[push] Uploading {total} image(s) → {remote}/{name}/images/"
        for i, img in enumerate(images, 1):
            dest = f"{remote}/{name}/images/{img.name}"
            sftp.put(str(img), dest)
            if i % 5 == 0 or i == total:
                yield f"[push]   {i}/{total}  {img.name} → {dest}"

        dest = f"{remote}/{name}/annotations.json"
        yield f"[push] {inter/'annotations.json'} → {dest}"
        sftp.put(str(inter / "annotations.json"), dest)

        cfg_local = inter / "configs" / name / "fast_rcnn_R_50_FPN_3x.yaml"
        dest = f"{remote}/layout-model-training/configs/{name}/fast_rcnn_R_50_FPN_3x.yaml"
        yield f"[push] {cfg_local} → {dest}"
        sftp.put(str(cfg_local), dest)

        dest = f"{remote}/layout-model-training/tools/infer_layout.py"
        yield f"[push] infer_layout.py → {dest}"
        sftp.put(str(Path(__file__).parent / "infer_layout.py"), dest)

        def sftp_put_lf(local: Path, remote_dest: str):
            """Upload a text file with LF line endings (strip Windows CRLF)."""
            content = local.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            with sftp.open(remote_dest, "wb") as fh:
                fh.write(content)

        dest_train = f"{remote}/layout-model-training/scripts/{name}.sh"
        dest_infer = f"{remote}/layout-model-training/scripts/{name}_infer.sh"
        yield f"[push] train.sh → {dest_train}"
        sftp_put_lf(inter / "train.sh", dest_train)
        yield f"[push] infer.sh → {dest_infer}"
        sftp_put_lf(inter / "infer.sh", dest_infer)

        yield "[push] All files uploaded successfully."
        yield f"[push] Verifying: ls {remote}/layout-model-training/scripts/"
        _, stdout, _ = c.exec_command(f"ls -1 {remote}/layout-model-training/scripts/")
        for ln in stdout.read().decode().splitlines():
            yield f"[push]   {ln}"
    finally:
        sftp.close()
        c.close()


@app.post("/api/project/{name}/train")
async def api_train(name: str, body: TrainRequest = TrainRequest()):
    """Push data to server then run training inside Docker. Streams log via SSE.

    Training is launched detached (nohup + disown) so it survives webapp
    restarts and browser disconnects.  The log is streamed via tail -f;
    closing the browser does NOT kill training.  Clicking Train again while
    training is running re-attaches to the existing log instead of starting
    a second job.
    """
    from app import ssh_ops

    try:
        cfg = load_config(name)
        srv = _server_cfg(name)
        passphrase = body.passphrase

        pdir  = project_dir(name)
        inter = pdir / "intermediate"

        script_path = f"/workspace/layout-model-training/scripts/{name}.sh"
        log_path    = f"/workspace/layout-model-training/logs/{name}_train.log"
        pid_path    = f"/workspace/layout-model-training/logs/{name}_train.pid"

        def _quick(cmd: str) -> str:
            """Run a non-streaming SSH command, return stdout."""
            c = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], passphrase)
            _, out, _ = c.exec_command(cmd)
            result = out.read().decode(errors="replace").strip()
            c.close()
            return result

        def full_gen():
            # ── Check if training is already running ──────────────────────────
            already_running = False
            try:
                status = _quick(
                    f"docker exec {_train_container()} bash -c "
                    f"'[ -f {pid_path} ] && pid=$(cat {pid_path}) && "
                    f"kill -0 $pid 2>/dev/null && echo RUNNING || echo STOPPED'"
                    f" 2>/dev/null || echo STOPPED"
                )
                already_running = "RUNNING" in status
            except Exception:
                already_running = False

            if already_running:
                yield "[train] Training already running — re-attaching to log..."
            else:
                # ── Validate + push data ──────────────────────────────────────
                if not (inter / "annotations.json").exists():
                    yield "ERROR: Run 'Prepare training data' first."
                    return
                yield from _push_training_data_gen(name, srv, passphrase)

                # ── Launch detached training ──────────────────────────────────
                yield "[train] Launching detached training (safe to close browser)..."
                launch_result = _quick(
                    f"docker start {_train_container()} && "
                    f"docker exec {_train_container()} bash -c "
                    f"'mkdir -p /workspace/layout-model-training/logs && "
                    f"nohup bash {script_path} >{log_path} 2>&1 & echo $! >{pid_path}; "
                    f"echo LAUNCHED:$(cat {pid_path})'"
                )
                yield f"[train] {launch_result.strip()}"

            # ── Stream log, stop when process exits ───────────────────────────
            yield "[train] Streaming log — closing browser won't stop training..."
            tail_cmd = (
                f"docker exec {_train_container()} bash -c '"
                f"tail -n 0 -f {log_path} & TAIL=$!; "
                f"pid=$(cat {pid_path} 2>/dev/null); "
                f"[ -n \"$pid\" ] && while kill -0 $pid 2>/dev/null; do sleep 5; done; "
                f"sleep 2; kill $TAIL 2>/dev/null; "
                f"echo \"[Training complete — process has exited]\"'"
            )
            yield from ssh_ops.stream_command(
                srv["host"], srv["user"], srv["key_path"], tail_cmd, passphrase)

        return await _sse_stream(full_gen())

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _push_infer_data_gen(name: str, srv: dict, passphrase: str, skip_images: bool = False):
    """Generator: push images + scripts + config for inference (no annotations.json needed).

    Uses predict_remote_path (if set) as the Docker-container root for placing
    the infer script — necessary when the predict container mounts a parent
    directory compared to the training container.
    skip_images=True skips the (slow) image upload and only pushes config/scripts.
    """
    from app import ssh_ops
    import posixpath as pp
    pdir         = project_dir(name)
    inter        = pdir / "intermediate"
    remote       = srv["remote_path"].rstrip("/")        # where data lives on host
    predict_root = srv.get("predict_remote_path", remote).rstrip("/")  # predict container ws root on host

    yield f"[push] remote_path (data)    = {remote}"
    yield f"[push] predict_remote_path   = {predict_root}"
    yield f"[push] Connecting to {srv['host']} as {srv['user']}..."
    c = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], passphrase)

    # Detect the actual bind mount so we get the correct ws_prefix regardless
    # of how remote_path / predict_remote_path are configured.
    _, _out, _ = c.exec_command(
        f"docker inspect {_predict_container()} "
        "--format '{{range .HostConfig.Binds}}{{println .}}{{end}}'"
    )
    container_ws_host = predict_root
    for _bind in _out.read().decode().strip().splitlines():
        _parts = _bind.strip().split(':')
        if len(_parts) >= 2 and _parts[1].rstrip('/') == '/workspace':
            container_ws_host = _parts[0].rstrip('/')
            break
    if remote.startswith(container_ws_host + "/"):
        ws_prefix = remote[len(container_ws_host) + 1:]
    else:
        ws_prefix = ""

    yield f"[push] container /workspace ← {container_ws_host}"
    yield f"[push] container ws prefix   = '{ws_prefix}'"
    sftp = c.open_sftp()
    try:
        # All paths that must be visible inside the predict container go under predict_root.
        # Images live under remote (data workspace, mounted into both containers).
        dirs = [
            f"{remote}/{name}/images",
            f"{predict_root}/layout-model-training/configs/{name}",
            f"{predict_root}/layout-model-training/tools",
            f"{predict_root}/layout-model-training/scripts",
        ]
        for d in dirs:
            yield f"[push] mkdir -p {d}"
            ssh_ops._sftp_mkdir_p(sftp, d)

        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
        if skip_images:
            images = [p for p in (pdir / "annotations").iterdir()
                      if p.suffix.lower() in IMAGE_EXTS]
            yield f"[push] Skipping image upload ({len(images)} images already on server)."
        else:
            images = [p for p in (pdir / "annotations").iterdir()
                      if p.suffix.lower() in IMAGE_EXTS]
            total = len(images)
            yield f"[push] Uploading {total} image(s) → {remote}/{name}/images/"
            for i, img in enumerate(images, 1):
                dest = f"{remote}/{name}/images/{img.name}"
                sftp.put(str(img), dest)
                if i % 10 == 0 or i == total:
                    yield f"[push]   {i}/{total}  {img.name}"

        cfg_local = inter / "configs" / name / "fast_rcnn_R_50_FPN_3x.yaml"
        dest = f"{predict_root}/layout-model-training/configs/{name}/fast_rcnn_R_50_FPN_3x.yaml"
        yield f"[push] config YAML → {dest}"
        sftp.put(str(cfg_local), dest)

        dest = f"{predict_root}/layout-model-training/tools/infer_layout.py"
        yield f"[push] infer_layout.py → {dest}"
        sftp.put(str(Path(__file__).parent / "infer_layout.py"), dest)

        def sftp_put_lf(local: Path, remote_dest: str):
            content = local.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            with sftp.open(remote_dest, "wb") as fh:
                fh.write(content)

        def sftp_put_infer_sh(local: Path, remote_dest: str):
            """Upload infer.sh, patching all /workspace/ paths to include ws_prefix.
            Every path in the generated infer.sh lives under the prefixed workspace,
            so we replace all /workspace/ occurrences at once.
            """
            content = local.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if ws_prefix:
                content = content.replace(b"/workspace/",
                                          f"/workspace/{ws_prefix}/".encode())
            with sftp.open(remote_dest, "wb") as fh:
                fh.write(content)

        dest_infer = f"{predict_root}/layout-model-training/scripts/{name}_infer.sh"
        yield f"[push] infer.sh → {dest_infer}" + (f" (ws_prefix='{ws_prefix}')" if ws_prefix else "")
        sftp_put_infer_sh(inter / "infer.sh", dest_infer)

        yield "[push] All files uploaded successfully."
        yield f"[push] Verifying scripts dir ({predict_root}/layout-model-training/scripts/):"
        _, stdout, _ = c.exec_command(f"ls -la {predict_root}/layout-model-training/scripts/")
        for ln in stdout.read().decode().splitlines():
            yield f"[push]   {ln}"
    finally:
        sftp.close()
        c.close()


@app.post("/api/project/{name}/infer")
async def api_infer(name: str, body: InferRequest = InferRequest()):
    """Run inference on the server. Streams log via SSE."""
    from app import ssh_ops
    try:
        srv          = _server_cfg(name)
        passphrase   = body.passphrase
        remote       = srv["remote_path"].rstrip("/")
        predict_root = srv.get("predict_remote_path", remote).rstrip("/")

        # Detect bind mount to build the correct in-container script path.
        def _detect_script_path() -> str:
            try:
                c = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], passphrase)
                _, _out, _ = c.exec_command(
                    f"docker inspect {_predict_container()} "
                    "--format '{{range .HostConfig.Binds}}{{println .}}{{end}}'"
                )
                container_ws_host = predict_root
                for _bind in _out.read().decode().strip().splitlines():
                    _parts = _bind.strip().split(':')
                    if len(_parts) >= 2 and _parts[1].rstrip('/') == '/workspace':
                        container_ws_host = _parts[0].rstrip('/')
                        break
                c.close()
                if remote.startswith(container_ws_host + "/"):
                    pfx = remote[len(container_ws_host) + 1:] + "/"
                else:
                    pfx = ""
                return f"/workspace/{pfx}layout-model-training/scripts/{name}_infer.sh"
            except Exception:
                return f"/workspace/layout-model-training/scripts/{name}_infer.sh"

        def full_gen():
            yield from _push_infer_data_gen(name, srv, passphrase,
                                            skip_images=body.skip_image_upload)
            script_path = _detect_script_path()
            docker_cmd = (
                f"docker start {_predict_container()} && "
                f"docker exec {_predict_container()} "
                f"bash {script_path}"
            )
            yield f"[push] Starting Docker inference (script: {script_path})..."
            yield from ssh_ops.stream_command(srv["host"], srv["user"],
                                              srv["key_path"], docker_cmd, passphrase)

        return await _sse_stream(full_gen())

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _push_infer_from_gen(new_name: str, source_name: str,
                         srv: dict, passphrase: str,
                         skip_images: bool = False,
                         threshold: float = 0.3):
    """Generator: run inference on new_name's images using source_name's trained model.

    Identical to _push_infer_data_gen except the --weights and --config paths
    point at the source project, while --images and --output point at the new project.
    The source project's config YAML is expected to already be on the server
    (from a previous prepare+push cycle on that project).
    """
    from app import ssh_ops
    import posixpath as pp

    new_cfg  = load_config(new_name)
    src_cfg  = load_config(source_name)

    # Label compatibility check
    new_labels = new_cfg.get("labels", [])
    src_labels = src_cfg.get("labels", [])
    if set(new_labels) != set(src_labels):
        raise HTTPException(
            status_code=400,
            detail=(f"Label mismatch: '{new_name}' has {new_labels} "
                    f"but '{source_name}' was trained with {src_labels}. "
                    f"Projects must share the same label set."),
        )

    pdir         = project_dir(new_name)
    remote       = srv["remote_path"].rstrip("/")
    predict_root = srv.get("predict_remote_path", remote).rstrip("/")

    if remote.startswith(predict_root + "/"):
        ws_prefix = remote[len(predict_root) + 1:]
    else:
        ws_prefix = ""

    yield f"[infer-from] source model : {source_name}"
    yield f"[infer-from] target project: {new_name}"
    yield f"[infer-from] remote_path   : {remote}"
    yield f"[infer-from] ws_prefix     : '{ws_prefix}'"
    yield f"[infer-from] Connecting to {srv['host']} as {srv['user']}..."

    c    = ssh_ops._client(srv["host"], srv["user"], srv["key_path"], passphrase)
    sftp = c.open_sftp()
    try:
        # Determine the actual host directory that the predicting container mounts as /workspace.
        # This may differ from predict_root/remote if predict_remote_path isn't configured.
        _, _out, _ = c.exec_command(
            f"docker inspect {_predict_container()} "
            "--format '{{range .HostConfig.Binds}}{{println .}}{{end}}'"
        )
        container_ws_host = predict_root  # fallback
        for _bind in _out.read().decode().strip().splitlines():
            _parts = _bind.strip().split(':')
            if len(_parts) >= 2 and _parts[1].rstrip('/') == '/workspace':
                container_ws_host = _parts[0].rstrip('/')
                break
        # Recompute ws_prefix from the real container mount rather than config assumptions
        if remote.startswith(container_ws_host + "/"):
            ws_prefix = remote[len(container_ws_host) + 1:]
        else:
            ws_prefix = ""
        yield f"[infer-from] container /workspace ← {container_ws_host}"
        yield f"[infer-from] effective ws_prefix  : '{ws_prefix}'"

        # Ensure directories exist for the new project
        dirs = [
            f"{remote}/{new_name}/images",
            f"{remote}/{new_name}/predictions",
            f"{predict_root}/layout-model-training/tools",
            f"{predict_root}/layout-model-training/scripts",
        ]
        for d in dirs:
            yield f"[infer-from] mkdir -p {d}"
            ssh_ops._sftp_mkdir_p(sftp, d)

        # Verify source model weights exist on the server
        host_weights = (f"{remote}/layout-model-training/outputs/{source_name}/"
                        f"fast_rcnn_R_50_FPN_3x/model_final.pth")
        _, out, _ = c.exec_command(f"test -f {host_weights} && echo OK || echo MISSING")
        status = out.read().decode().strip()
        if status != "OK":
            raise HTTPException(
                status_code=404,
                detail=f"Model weights not found on server: {host_weights}\n"
                       f"Run 'Train model' on project '{source_name}' first.",
            )
        yield f"[infer-from] ✓ weights found: {host_weights}"

        # Upload images for the new project
        IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
        images = [p for p in (pdir / "annotations").iterdir()
                  if p.suffix.lower() in IMAGE_EXTS]
        if skip_images:
            yield f"[infer-from] Skipping image upload ({len(images)} images already on server)."
        else:
            total = len(images)
            yield f"[infer-from] Uploading {total} image(s) → {remote}/{new_name}/images/"
            for i, img in enumerate(images, 1):
                sftp.put(str(img), f"{remote}/{new_name}/images/{img.name}")
                if i % 10 == 0 or i == total:
                    yield f"[infer-from]   {i}/{total}  {img.name}"

        # Upload the inference tool
        dest = f"{predict_root}/layout-model-training/tools/infer_layout.py"
        yield f"[infer-from] infer_layout.py → {dest}"
        sftp.put(str(Path(__file__).parent / "infer_layout.py"), dest)

        # Build an infer script: source model weights/config, new project images/output
        pfx = (ws_prefix + "/") if ws_prefix else ""
        config_path  = f"/workspace/{pfx}layout-model-training/configs/{source_name}/fast_rcnn_R_50_FPN_3x.yaml"
        weights_path = f"/workspace/{pfx}layout-model-training/outputs/{source_name}/fast_rcnn_R_50_FPN_3x/model_final.pth"
        images_path  = f"/workspace/{pfx}{new_name}/images"
        output_path  = f"/workspace/{pfx}{new_name}/predictions"
        labels_str   = " ".join(src_labels)

        tool_path = f"/workspace/{pfx}layout-model-training/tools/infer_layout.py"
        script = (
            f"#!/bin/bash\nset -e\n"
            f"echo '=== EconAI: {new_name} inference from {source_name} model ==='\n"
            f"python3 {tool_path} \\\n"
            f"    --config  {config_path} \\\n"
            f"    --weights {weights_path} \\\n"
            f"    --images  {images_path} \\\n"
            f"    --output  {output_path} \\\n"
            f"    --labels  {labels_str} \\\n"
            f"    --threshold {threshold}\n"
            f"echo '=== Inference complete ==='\n"
        ).encode()

        script_dest = f"{predict_root}/layout-model-training/scripts/{new_name}_infer_from_{source_name}.sh"
        yield f"[infer-from] script → {script_dest}"
        with sftp.open(script_dest, "wb") as fh:
            fh.write(script)

        yield "[infer-from] All files uploaded successfully."
    finally:
        sftp.close()
        c.close()

    # Translate the host script path to the container path via the bind mount
    if script_dest.startswith(container_ws_host + "/"):
        container_script = "/workspace/" + script_dest[len(container_ws_host) + 1:]
    else:
        container_script = f"/workspace/{(ws_prefix + '/') if ws_prefix else ''}layout-model-training/scripts/{new_name}_infer_from_{source_name}.sh"
    yield f"[infer-from] container script path: {container_script}"
    yield f"__docker_cmd__:{container_script}"


class InferFromRequest(BaseModel):
    passphrase:        Optional[str] = None
    skip_image_upload: bool = False
    threshold:         float = 0.1


@app.post("/api/project/{name}/infer-from/{source}")
async def api_infer_from(name: str, source: str, body: InferFromRequest = InferFromRequest()):
    """Run inference on name's images using source project's trained model. Streams log via SSE."""
    from app import ssh_ops
    try:
        srv = _server_cfg(name)

        def full_gen():
            docker_cmd = None
            for line in _push_infer_from_gen(name, source, srv,
                                             body.passphrase, body.skip_image_upload,
                                             body.threshold):
                if line.startswith("__docker_cmd__:"):
                    docker_cmd = line[len("__docker_cmd__:"):]
                else:
                    yield line
            if docker_cmd:
                container_script = docker_cmd
                yield "[infer-from] Starting Docker inference..."
                cmd = (
                    f"docker start {_predict_container()} && "
                    f"docker exec {_predict_container()} bash {container_script}"
                )
                yield from ssh_ops.stream_command(
                    srv["host"], srv["user"], srv["key_path"], cmd, body.passphrase)

        return await _sse_stream(full_gen())

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/pull-predictions")
def api_pull_predictions(name: str, body: TrainRequest = TrainRequest()):
    """Pull predicted JSONs from server into local predictions/ folder (never touches annotations/)."""
    from app import ssh_ops
    try:
        srv       = _server_cfg(name)
        pdir      = project_dir(name)
        remote    = srv["remote_path"].rstrip("/")
        pred_dir  = pdir / "predictions"
        pred_dir.mkdir(parents=True, exist_ok=True)
        result = ssh_ops.pull_folder(
            srv["host"], srv["user"], srv["key_path"],
            f"{remote}/{name}/predictions",
            pred_dir,
            body.passphrase,
        )
        return {**result, "local_path": str(pred_dir)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/project/{name}/apply-predictions")
def api_apply_predictions(name: str):
    """Copy predicted shapes into annotation JSONs that have no shapes yet."""
    import json as _json
    pdir     = project_dir(name)
    pred_dir = pdir / "predictions"
    ann_dir  = pdir / "annotations"
    if not pred_dir.exists():
        raise HTTPException(status_code=400, detail="No predictions pulled yet")
    applied = skipped = 0
    for pred_file in pred_dir.glob("*.json"):
        ann_file = ann_dir / pred_file.name
        if not ann_file.exists():
            continue
        ann = _json.loads(ann_file.read_text(encoding="utf-8"))
        if ann.get("shapes"):  # already has hand annotations — skip
            skipped += 1
            continue
        pred = _json.loads(pred_file.read_text(encoding="utf-8"))
        ann["shapes"] = pred.get("shapes", [])
        ann_file.write_text(_json.dumps(ann, indent=2, ensure_ascii=False), encoding="utf-8")
        applied += 1
    return {"applied": applied, "skipped_had_annotations": skipped}


# ---------------------------------------------------------------------------
# Routes — perspective correction
# ---------------------------------------------------------------------------

class PerspectiveRequest(BaseModel):
    folder: str
    stem:   str
    points: list = []  # [[x,y]×4] for manual; empty [] triggers auto-detection
    save:   bool = False


def _auto_detect_page_quad(img_np):
    """
    Automatically find the four corners of a document page.
    Returns a list of four [x, y] points (any order) or None.

    Strategy 1 — Canny edges → largest quad-shaped contour.
      Works well when the page has a visible border against the scanner bed.
    Strategy 2 — HoughLinesP intersections.
      Works when the page fills the frame but has prominent table/text lines.
    """
    import cv2
    import numpy as np

    H, W = img_np.shape[:2]
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    # ── Strategy 1: largest quad contour ──────────────────────────────────────
    for lo, hi in [(30, 100), (50, 150), (10, 50)]:
        edges = cv2.Canny(blur, lo, hi)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        cnts, _ = cv2.findContours(edges, cv2.RETR_LIST,
                                   cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:10]:
            if cv2.contourArea(c) < 0.15 * H * W:
                break  # sorted, so no point continuing
            peri = cv2.arcLength(c, True)
            for eps in [0.01, 0.02, 0.03, 0.05]:
                approx = cv2.approxPolyDP(c, eps * peri, True)
                if len(approx) == 4:
                    return [p[0].tolist() for p in approx]

    # ── Strategy 2: Hough line intersections ──────────────────────────────────
    edges = cv2.Canny(blur, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180,
                            threshold=max(50, min(W, H) // 8),
                            minLineLength=min(W, H) // 5,
                            maxLineGap=40)
    if lines is None:
        return None

    segs = lines.reshape(-1, 4).tolist()
    h_segs, v_segs = [], []
    for x1, y1, x2, y2 in segs:
        ang = np.degrees(np.arctan2(abs(y2 - y1), abs(x2 - x1) + 1e-9))
        (h_segs if ang < 20 else v_segs if ang > 70 else []).append(
            (x1, y1, x2, y2))

    if len(h_segs) < 2 or len(v_segs) < 2:
        return None

    h_segs.sort(key=lambda s: (s[1] + s[3]) / 2)
    v_segs.sort(key=lambda s: (s[0] + s[2]) / 2)

    def _avg(segs):
        return (float(np.mean([s[0] for s in segs])),
                float(np.mean([s[1] for s in segs])),
                float(np.mean([s[2] for s in segs])),
                float(np.mean([s[3] for s in segs])))

    def _isect(a, b):
        x1, y1, x2, y2 = a
        x3, y3, x4, y4 = b
        d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(d) < 1e-8:
            return None
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / d
        return [x1 + t * (x2 - x1), y1 + t * (y2 - y1)]

    n_h = max(1, len(h_segs) // 5)
    n_v = max(1, len(v_segs) // 5)
    top_l  = _avg(h_segs[:n_h])
    bot_l  = _avg(h_segs[-n_h:])
    left_l = _avg(v_segs[:n_v])
    rgt_l  = _avg(v_segs[-n_v:])

    corners = [
        _isect(top_l, left_l), _isect(top_l, rgt_l),
        _isect(bot_l, rgt_l),  _isect(bot_l, left_l),
    ]
    if any(c is None for c in corners):
        return None
    # Sanity: all corners within ±30 % of the image bounds
    m = 0.3
    if not all(-W * m <= c[0] <= W * (1 + m) and
               -H * m <= c[1] <= H * (1 + m) for c in corners):
        return None

    return corners   # [TL, TR, BR, BL]


@app.post("/api/page/perspective")
def api_perspective(body: PerspectiveRequest):
    """
    Perspective (trapezoid→rectangle) correction.

    points=[]   → auto-detect page corners, return preview + detected_points.
    points=[×4] → use the supplied corners (manual or re-submit of detected).
    save=False  → base64 JPEG preview only.
    save=True   → overwrite image, clear shapes, update JSON dimensions.
    """
    import base64, io, math
    import cv2
    import numpy as np
    from PIL import Image

    d        = _resolve_folder(body.folder)
    img_path = _find_image(d, body.stem)
    jf       = d / f"{body.stem}.json"

    if img_path is None:
        raise HTTPException(status_code=404, detail="Image not found")
    if not jf.exists():
        raise HTTPException(status_code=404, detail="JSON not found")
    if body.points and len(body.points) != 4:
        raise HTTPException(status_code=400, detail="Supply 0 or exactly 4 points")

    try:
        img = Image.open(str(img_path)).convert("RGB")
    except Exception:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())

    img_np = np.array(img)
    H_img, W_img = img_np.shape[:2]

    # ── Determine the four source corners ─────────────────────────────────────
    if not body.points:
        raw = _auto_detect_page_quad(img_np)
        if raw is None:
            raise HTTPException(
                status_code=422,
                detail="Could not automatically detect the page boundary. "
                       "Try again or mark the four corners manually.")
        detected_points = [[float(v) for v in p] for p in raw]
    else:
        detected_points = [[float(v) for v in p] for p in body.points]

    # Sort into TL, TR, BR, BL regardless of supplied order
    pts = [tuple(p) for p in detected_points]
    by_y   = sorted(pts, key=lambda p: p[1])
    top    = sorted(by_y[:2], key=lambda p: p[0])
    bottom = sorted(by_y[2:], key=lambda p: p[0])
    tl, tr = top[0],    top[1]
    bl, br = bottom[0], bottom[1]

    w_top  = math.dist(tl, tr);  w_bot  = math.dist(bl, br)
    h_left = math.dist(tl, bl);  h_right = math.dist(tr, br)
    dst_w  = int(max(w_top, w_bot))
    dst_h  = int(max(h_left, h_right))

    if dst_w < 2 or dst_h < 2:
        raise HTTPException(status_code=400, detail="Degenerate quadrilateral")

    # ── Build homography and warp the full image ───────────────────────────────
    src_arr = np.float32([tl, tr, br, bl])
    dst_arr = np.float32([(0, 0), (dst_w, 0), (dst_w, dst_h), (0, dst_h)])
    H_mat   = cv2.getPerspectiveTransform(src_arr, dst_arr)

    # Project all four image corners through H to find the full output canvas.
    img_corners = np.float32(
        [[0, 0], [W_img, 0], [W_img, H_img], [0, H_img]]
    ).reshape(-1, 1, 2)
    wc = cv2.perspectiveTransform(img_corners, H_mat).reshape(-1, 2)

    min_x, min_y = float(wc[:, 0].min()), float(wc[:, 1].min())
    max_x, max_y = float(wc[:, 0].max()), float(wc[:, 1].max())

    # Translate so all pixels land in non-negative coordinates.
    T = np.array([[1, 0, -min_x], [0, 1, -min_y], [0, 0, 1]],
                 dtype=np.float64)
    H_eff = (T @ H_mat.astype(np.float64)).astype(np.float32)
    out_w  = int(round(max_x - min_x))
    out_h  = int(round(max_y - min_y))

    try:
        out_np = cv2.warpPerspective(img_np, H_eff, (out_w, out_h),
                                     flags=cv2.INTER_CUBIC)
        out = Image.fromarray(out_np)
    except Exception:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())

    # ── Save or preview ────────────────────────────────────────────────────────
    if body.save:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500,
                                detail=f"Annotation JSON corrupt: {exc}")
        fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG",
               "tif": "TIFF", "tiff": "TIFF"}.get(
            img_path.suffix.lstrip(".").lower(), "JPEG")
        save_kw = {"quality": 92} if fmt == "JPEG" else {}
        out.save(str(img_path), format=fmt, **save_kw)
        data["shapes"]      = []
        data["imageWidth"]  = out_w
        data["imageHeight"] = out_h
        jf.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                      encoding="utf-8")
        return {"ok": True, "width": out_w, "height": out_h}
    else:
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=88)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        return {"ok": True, "preview": b64,
                "width": out_w, "height": out_h,
                "detected_points": detected_points}


# ---------------------------------------------------------------------------
# Routes — Quality Audit
# ---------------------------------------------------------------------------

@app.get("/api/audit/random")
def api_audit_random(folder: str = Query(...), mode: str = Query("both")):
    """Return a randomly chosen shape that has OCR/LLM output but no human correction."""
    import random, base64, io
    from PIL import Image

    d = _resolve_folder(folder)
    json_files = list(d.glob("*.json"))
    if not json_files:
        raise HTTPException(status_code=404, detail="No pages found")
    random.shuffle(json_files)

    def _scan_page(jf):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            return []
        hits = []
        for idx, shape in enumerate(data.get("shapes", [])):
            has_ocr   = bool(
                shape.get("easyocr_output", {}).get("ocr_text") or
                shape.get("tesseract_output", {}).get("ocr_text")
            )
            has_llm   = bool(shape.get("openai_output", {}).get("response"))
            has_human = bool(shape.get("human_output", {}).get("human_corrected_text"))
            if has_human:
                continue
            if mode == "ocr" and not has_ocr:
                continue
            if mode == "llm" and not has_llm:
                continue
            if mode == "both" and not (has_ocr and has_llm):
                continue
            hits.append((jf.stem, idx))
        return hits

    # Fast path: try random pages one by one; stop as soon as we have candidates
    candidates = []
    for jf in json_files:
        candidates = _scan_page(jf)
        if candidates:
            break

    if not candidates:
        raise HTTPException(status_code=404, detail="No qualifying shapes found")

    stem, idx = random.choice(candidates)
    jf   = d / f"{stem}.json"
    data = json.loads(jf.read_text(encoding="utf-8"))
    shape = data["shapes"][idx]

    ocr_text = (
        shape.get("easyocr_output", {}).get("ocr_text") or
        shape.get("tesseract_output", {}).get("ocr_text") or ""
    )
    llm_text = shape.get("openai_output", {}).get("response") or ""
    label    = shape.get("label", "")

    # Crop cell image and detect row boundaries
    image_b64 = None
    line_rows  = []          # [[top, bottom], ...] in crop-pixel space
    img_path  = _find_image(d, stem)
    if img_path is not None:
        try:
            pts = shape.get("points", [])
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                pad  = 4
                x1   = max(0, int(min(xs)) - pad)
                y1   = max(0, int(min(ys)) - pad)
                x2   = int(max(xs)) + pad
                y2   = int(max(ys)) + pad
                img  = Image.open(str(img_path)).convert("RGB")
                crop = img.crop((x1, y1, x2, y2))
                buf  = io.BytesIO()
                crop.save(buf, format="JPEG", quality=90)
                buf.seek(0)
                image_b64 = base64.b64encode(buf.read()).decode()
                # Detect row boundaries using the shadow page (same as OCR line-by-line)
                try:
                    shadow = _get_shadow_page(folder, stem, img_path)
                    shadow_crop = shadow.crop((x1, y1, x2, y2))
                    rows = _detect_text_rows(shadow_crop, cell_height=26)
                    line_rows = [list(r) for r in rows]
                except Exception:
                    line_rows = []
        except Exception:
            image_b64 = None

    return {
        "stem":       stem,
        "idx":        idx,
        "label":      label,
        "ocr_text":   ocr_text,
        "llm_text":   llm_text,
        "image_b64":  image_b64,
        "line_rows":  line_rows,
    }


_AUDIT_STATS_DEFAULT = {
    "ocr": {"msd_sum": 0, "msd_count": 0, "cer_sum": 0, "cer_count": 0},
    "llm": {"msd_sum": 0, "msd_count": 0, "cer_sum": 0, "cer_count": 0},
}


@app.get("/api/audit/stats")
def api_audit_get_stats(folder: str = Query(...)):
    """Return accumulated audit stats for the project."""
    stats_file = _resolve_folder(folder).parent / "audit_stats.json"
    if not stats_file.exists():
        return _AUDIT_STATS_DEFAULT.copy()
    try:
        return json.loads(stats_file.read_text(encoding="utf-8"))
    except Exception:
        return _AUDIT_STATS_DEFAULT.copy()


@app.post("/api/audit/stats")
async def api_audit_update_stats(folder: str = Query(...), request: Request = None):
    """Save audit stats for the project."""
    body = await request.json()
    stats_file = _resolve_folder(folder).parent / "audit_stats.json"
    stats_file.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

@app.get("/api/export/excel")
def api_export_excel(
    folder:      str  = Query(...),
    scope:       str  = Query("page"),      # "page" | "document"
    stem:        str  = Query(None),        # required when scope="page"
    dual:        bool = Query(False),       # dual-page layout (odd left, even right)
    layer:       str  = Query("best_llm"), # "ocr"|"llm"|"human"|"best_ocr"|"best_llm"
    types:       str  = Query(""),         # comma-sep label types; empty = all
    col_headers: str  = Query(""),         # comma-sep column header labels; empty = no header row
    stems:       str  = Query(""),         # comma-sep stems to include; empty = all (document scope)
    col_filter:  str  = Query(""),         # comma-sep 1-indexed column numbers; empty = all columns
):
    """
    Generate an .xlsx preserving spatial layout.
    Each text line inside a lattice cell becomes its own Excel row.
    When cells in the same lattice row have different line counts, the
    shorter ones are padded with blank cells coloured light-red.
    """
    try:
        import openpyxl
        from openpyxl.styles import Alignment, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(status_code=500,
            detail="openpyxl not installed — run: pip install openpyxl")

    import io as _io, re
    from collections import defaultdict

    d = _resolve_folder(folder)
    selected_types = {t.strip() for t in types.split(",") if t.strip()} if types else set()

    # ── Styles ───────────────────────────────────────────────────────────────
    from openpyxl.styles import Font
    _align      = Alignment(wrap_text=True, vertical="top", horizontal="left")
    _red_fill   = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
    _blue_fill  = PatternFill(start_color="CCE5FF", end_color="CCE5FF", fill_type="solid")
    _src_fill   = PatternFill(start_color="D0D0D0", end_color="D0D0D0", fill_type="solid")
    _src_font   = Font(italic=True, bold=True, color="444444")

    def natural_key(p):
        return [int(c) if c.isdigit() else c.lower()
                for c in re.split(r'(\d+)', str(p))]

    def get_text(shape):
        human = (shape.get("human_output") or {}).get("human_corrected_text") or ""
        ocr   = ((shape.get("tesseract_output") or {}).get("ocr_text") or
                 (shape.get("easyocr_output")   or {}).get("ocr_text") or "")
        llm   = (shape.get("openai_output") or {}).get("response") or ""
        if layer == "human":    return human.strip()
        if layer == "ocr":      return ocr.strip()
        if layer == "llm":      return llm.strip()
        if layer == "best_ocr": return (human or ocr or llm).strip()
        return (human or llm or ocr).strip()   # best_llm (default)

    def text_to_lines(text):
        """Split text into lines, stripping trailing blank lines."""
        lines = text.split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        return lines or [""]

    def shapes_to_cells(shapes):
        """
        Return list of dicts: {row_idx, col_idx, lines[]}
        Lattice rows/cols are detected by spatial clustering.
        """
        raw = []
        for sh in shapes:
            if selected_types and sh.get("label", "") not in selected_types:
                continue
            text = get_text(sh)
            if not text:
                continue
            pts = sh.get("points", [])
            if not pts:
                continue
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            raw.append(dict(text=text,
                            cx=(x1+x2)/2, cy=(y1+y2)/2,
                            top_y=y1, bot_y=y2,
                            h=max(1, y2-y1), w=max(1, x2-x1),
                            label=sh.get("label", "?")))
        print(f"[EXCEL] shapes_to_cells: {len(shapes)} shapes in, {len(raw)} with text", flush=True)
        if not raw:
            return []

        # ── Row clustering — mirror the JS lattice detector logic ─────────
        # Sort by top edge.  For each shape check whether its centre-y falls
        # within [avgTop − TOL, avgBottom + TOL] of an existing row, where
        # avgTop/avgBottom are the mean top/bottom edges of shapes already in
        # that row.  This is identical to _latticeAssignCoords in index.html
        # and correctly separates pre-lattice headers (small, near the top of
        # the page) from tall cells whose centre-y happens to be far below.
        ROW_TOL = 10
        raw.sort(key=lambda c: c["top_y"])
        row_groups: list = []   # list of lists of raw-cell dicts
        for c in raw:
            placed = False
            for grp in row_groups:
                avg_top = sum(r["top_y"] for r in grp) / len(grp)
                avg_bot = sum(r["bot_y"] for r in grp) / len(grp)
                if avg_top - ROW_TOL <= c["cy"] <= avg_bot + ROW_TOL:
                    grp.append(c)
                    placed = True
                    break
            if not placed:
                row_groups.append([c])
        for row_idx, grp in enumerate(row_groups):
            for c in grp:
                c["row_idx"] = row_idx
            grp.sort(key=lambda c: c["cx"])

        # ── Column clustering (global) ───────────────────────────────────
        med_w    = sorted(c["w"] for c in raw)[len(raw)//2]
        thresh_x = max(3, med_w * 0.45)
        all_cx   = sorted({c["cx"] for c in raw})
        col_centers: list = []
        if all_cx:
            grp = [all_cx[0]]
            for cx in all_cx[1:]:
                if cx - grp[-1] <= thresh_x:
                    grp.append(cx)
                else:
                    col_centers.append(sum(grp)/len(grp))
                    grp = [cx]
            col_centers.append(sum(grp)/len(grp))
        for c in raw:
            c["col_idx"] = min(range(len(col_centers)),
                               key=lambda i: abs(col_centers[i] - c["cx"]))

        # ── Build final cell list (taller cell wins; loser is rescued) ─────
        # When two cells land on the same (row_idx, col_idx) — e.g. a short
        # county-name header and the tall craft-list text_cell both mapping to
        # the same column — keep the taller one in the slot AND relocate the
        # shorter one to row_idx-1 (one row above) so it is not lost.
        # After all cells are placed, row indices are normalised to start at 0.
        winner: dict = {}   # (row_idx, col_idx) → raw cell dict
        _dbg: list = []
        for c in raw:
            k = (c["row_idx"], c["col_idx"])
            if k in winner:
                prev = winner[k]
                if c["h"] > prev["h"]:
                    winner[k] = c          # taller takes the slot
                    loser = dict(prev)
                else:
                    loser = dict(c)        # prev stays, newcomer is the loser
                # Try to rescue the loser one row above
                rescue_k = (loser["row_idx"] - 1, loser["col_idx"])
                if rescue_k not in winner:
                    loser["row_idx"] -= 1
                    winner[rescue_k] = loser
                    _dbg.append(
                        f"COLLISION {k}: taller kept, "
                        f"rescued {loser['label']!r}(h={loser['h']:.0f}) "
                        f"-> row {loser['row_idx']}"
                    )
                else:
                    _dbg.append(
                        f"COLLISION {k}: taller kept, "
                        f"discarded {loser['label']!r}(h={loser['h']:.0f}) "
                        f"(rescue slot occupied)"
                    )
            else:
                winner[k] = c

        # Normalise row_idx so the minimum is 0 (relocated cells may be < 0)
        if winner:
            min_row = min(c["row_idx"] for c in winner.values())
            if min_row < 0:
                for c in winner.values():
                    c["row_idx"] -= min_row
        cells = [dict(row_idx=c["row_idx"],
                      col_idx=c["col_idx"],
                      lines=text_to_lines(c["text"]),
                      w_px=c["w"])
                 for c in winner.values()]
        return cells

    # ── Helpers for stacking / dual-page alignment ───────────────────────────

    def lattice_start_row(cells):
        """First lattice row_idx where column density >= 60% of the page maximum.
        Returns 0 if the page is sparse or empty."""
        if not cells:
            return 0
        rmap: dict = defaultdict(set)
        for c in cells:
            rmap[c["row_idx"]].add(c["col_idx"])
        max_cols  = max(len(s) for s in rmap.values())
        threshold = max(2, max_cols * 0.6)
        for ridx in sorted(rmap):
            if len(rmap[ridx]) >= threshold:
                return ridx
        return min(rmap)

    def excel_row_for_lattice(cells):
        """1-based Excel row (within-page, no external offset) where lattice begins."""
        if not cells:
            return 1
        target = lattice_start_row(cells)
        rmap: dict = defaultdict(list)
        for c in cells:
            rmap[c["row_idx"]].append(c)
        row = 1
        for ridx in sorted(rmap):
            if ridx >= target:
                return row
            row += max(len(c["lines"]) for c in rmap[ridx])
        return row

    def page_excel_height(cells, extra=0):
        """Total Excel rows a page occupies (expansion + optional alignment pad)."""
        if not cells:
            return extra
        rmap: dict = defaultdict(list)
        for c in cells:
            rmap[c["row_idx"]].append(c)
        return extra + sum(max(len(c["lines"]) for c in row)
                           for row in rmap.values())

    def write_cells(ws, cells, col_offset=0, base_row=1, align_pad=0):
        """
        Write cells to ws starting at base_row.
        align_pad blank rows (light-blue) are prepended before content — used to
        align the lattice with a paired page in dual-page mode.
        Within each lattice row, cells shorter than max_lines are padded with
        blank light-red cells to flag line-count mismatches.
        """
        if not cells:
            return

        # ── Alignment padding (blue) ─────────────────────────────────────
        # Data columns are now shifted +1 to make room for the meta (row#) column.
        if align_pad > 0:
            min_ci = min(c["col_idx"] for c in cells)
            max_ci = max(c["col_idx"] for c in cells)
            for pad_r in range(base_row, base_row + align_pad):
                for ci in range(min_ci, max_ci + 1):
                    ws.cell(row=pad_r,
                            column=ci + 2 + col_offset).fill = _blue_fill

        # ── Cell content ─────────────────────────────────────────────────
        rmap: dict = defaultdict(list)
        for c in cells:
            rmap[c["row_idx"]].append(c)

        excel_row = base_row + align_pad
        for row_idx in sorted(rmap):
            row_cells = rmap[row_idx]
            max_lines = max(len(c["lines"]) for c in row_cells)
            for line_i in range(max_lines):
                # Write lattice row number in meta column on every sub-row
                meta_c = ws.cell(row=excel_row + line_i, column=col_offset + 1)
                meta_c.value     = row_idx + 1   # 1-based
                meta_c.alignment = _align
                for c in row_cells:
                    col  = c["col_idx"] + 2 + col_offset   # +2: meta col shift
                    xcel = ws.cell(row=excel_row + line_i, column=col)
                    xcel.alignment = _align
                    if line_i < len(c["lines"]):
                        xcel.value = c["lines"][line_i]
                    else:
                        xcel.value = ""
                        xcel.fill  = _red_fill
            excel_row += max_lines

        # ── Column widths based on maximum content length ────────────────
        # Meta column: fit the largest row number
        meta_letter = get_column_letter(col_offset + 1)
        max_row_num = (max(rmap.keys()) + 1) if rmap else 1
        meta_w = max(4.0, float(len(str(max_row_num)) + 1))
        if ws.column_dimensions[meta_letter].width < meta_w:
            ws.column_dimensions[meta_letter].width = meta_w
        # Data columns: max character length of any line in that column
        col_max_len: dict = defaultdict(int)
        for c in cells:
            for line in c["lines"]:
                col_max_len[c["col_idx"]] = max(col_max_len[c["col_idx"]], len(str(line)))
        for col_idx, max_len in col_max_len.items():
            width  = max(4.0, min(60.0, float(max_len + 2)))
            letter = get_column_letter(col_idx + 2 + col_offset)   # +2: meta shift
            if ws.column_dimensions[letter].width < width:
                ws.column_dimensions[letter].width = width

    def write_source_row(ws, row, names: list, col_offset=0, right_col_offset=None):
        """
        Write source file name(s) in the meta column(s) before data rows.
        - col_offset+1       → left / single page meta column
        - right_col_offset+1 → right page meta column (dual layout only)
        """
        def _write_one(col, name):
            c = ws.cell(row=row, column=col)
            c.value     = name
            c.fill      = _src_fill
            c.font      = _src_font
            c.alignment = _align
        _write_one(col_offset + 1, names[0] if names else "")
        if right_col_offset is not None and len(names) > 1:
            _write_one(right_col_offset + 1, names[1])

    def max_col_of(cells):
        return max((c["col_idx"] for c in cells), default=-1)

    # ── Column-header support ────────────────────────────────────────────────
    hdr_list = [h.strip() for h in col_headers.split(",") if h.strip()] \
               if col_headers.strip() else []
    _hdr_font = Font(bold=True)
    _hdr_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    _hdr_font_white = Font(bold=True, color="FFFFFF")

    def write_header_row(ws, col_offset=0):
        """Write 'Row' meta header + hdr_list data headers at row 1."""
        # Meta column header
        mc = ws.cell(row=1, column=col_offset + 1)
        mc.value     = "Row"
        mc.font      = _hdr_font_white
        mc.fill      = _hdr_fill
        mc.alignment = _align
        # User-specified data column headers (shifted +1 for meta col)
        for i, label in enumerate(hdr_list):
            cell = ws.cell(row=1, column=i + 2 + col_offset)
            cell.value     = label
            cell.font      = _hdr_font_white
            cell.fill      = _hdr_fill
            cell.alignment = _align

    data_start = 2   # row 1 is always the header row (meta col "Row" + optional data headers)

    # ── Column filter ────────────────────────────────────────────────────────
    # col_filter is a comma-sep list of 1-indexed column numbers from the client.
    # We parse them to a 0-indexed set, then strip and re-index after shapes_to_cells.
    _col_filter_set: set | None = None
    if col_filter.strip():
        _col_filter_set = set()
        for part in col_filter.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                lo, _, hi = part.partition("-")
                try:
                    _col_filter_set.update(range(int(lo) - 1, int(hi)))
                except ValueError:
                    pass
            else:
                try:
                    _col_filter_set.add(int(part) - 1)
                except ValueError:
                    pass

    def apply_col_filter(cells):
        """Keep only columns in _col_filter_set and re-index col_idx to be contiguous."""
        if _col_filter_set is None:
            return cells
        kept = [c for c in cells if c["col_idx"] in _col_filter_set]
        # Re-index: sort the kept col indices, map old → new
        old_cols = sorted({c["col_idx"] for c in kept})
        remap = {old: new for new, old in enumerate(old_cols)}
        return [{**c, "col_idx": remap[c["col_idx"]]} for c in kept]

    def load_shapes(jf):
        try:
            return json.loads(jf.read_text(encoding="utf-8")).get("shapes", [])
        except Exception:
            return []

    # ── Collect page files ────────────────────────────────────────────────────
    if scope == "page":
        if not stem:
            raise HTTPException(status_code=400, detail="stem required for scope=page")
        jfiles = [d / f"{stem}.json"]
    else:
        jfiles = sorted(d.glob("*.json"), key=lambda f: natural_key(f.stem))
        if stems.strip():
            # Client sent an explicit ordered list of stems — filter and preserve that order
            stem_list  = [s.strip() for s in stems.split(",") if s.strip()]
            stem_index = {s: i for i, s in enumerate(stem_list)}
            jfiles = sorted(
                (jf for jf in jfiles if jf.stem in stem_index),
                key=lambda jf: stem_index[jf.stem],
            )

    wb  = openpyxl.Workbook()
    wb.remove(wb.active)

    if scope == "page":
        # ── Single page: one sheet (no source row needed) ─────────────────
        print(f"[EXCEL] processing page {jfiles[0].name}", flush=True)
        ws = wb.create_sheet(title=stem[:31])
        write_header_row(ws)
        write_cells(ws, apply_col_filter(shapes_to_cells(load_shapes(jfiles[0]))), base_row=data_start)

    elif not dual:
        # ── Whole document, single layout: all pages on one sheet ─────────
        ws      = wb.create_sheet(title="Export")
        write_header_row(ws)
        cur_row = data_start
        for jf in jfiles:
            print(f"[EXCEL] processing {jf.name}", flush=True)
            cells = apply_col_filter(shapes_to_cells(load_shapes(jf)))
            if not cells:
                continue
            # Source banner BEFORE the page data
            write_source_row(ws, cur_row, [jf.stem], col_offset=0)
            write_cells(ws, cells, col_offset=0, base_row=cur_row + 1)
            page_h = page_excel_height(cells)
            cur_row += 1 + page_h   # 1 source row + data rows

    else:
        # ── Whole document, dual layout: paired pages side-by-side ────────
        ws      = wb.create_sheet(title="Export")
        cur_row = data_start
        dual_headers_written = False
        i = 0
        while i < len(jfiles):
            left_jf  = jfiles[i]
            right_jf = jfiles[i+1] if i+1 < len(jfiles) else None
            i += 2

            left_cells  = apply_col_filter(shapes_to_cells(load_shapes(left_jf)))
            right_cells = apply_col_filter(shapes_to_cells(load_shapes(right_jf))) if right_jf else []

            # Align lattice rows between left and right pages
            left_latt  = excel_row_for_lattice(left_cells)   # within-page row
            right_latt = excel_row_for_lattice(right_cells)
            left_pad   = max(0, right_latt - left_latt)
            right_pad  = max(0, left_latt  - right_latt)

            # +3: meta col (1) + left data cols + gap col (1)
            right_col  = max_col_of(left_cells) + 3

            # Write column headers once (row 1) across both column groups
            if not dual_headers_written:
                write_header_row(ws, col_offset=0)
                write_header_row(ws, col_offset=right_col)
                dual_headers_written = True

            # Source banner BEFORE the pair data
            names = [left_jf.stem] + ([right_jf.stem] if right_jf else [])
            write_source_row(ws, cur_row, names,
                             col_offset=0, right_col_offset=right_col)

            write_cells(ws, left_cells,  col_offset=0,
                        base_row=cur_row + 1, align_pad=left_pad)
            write_cells(ws, right_cells, col_offset=right_col,
                        base_row=cur_row + 1, align_pad=right_pad)

            pair_height = max(
                page_excel_height(left_cells,  extra=left_pad),
                page_excel_height(right_cells, extra=right_pad),
            )
            cur_row += 1 + pair_height   # 1 source row + data rows

    if not wb.worksheets:
        wb.create_sheet("Empty")

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    fname = f"{stem}.xlsx" if (scope == "page" and stem) else "export.xlsx"
    import datetime as _dt
    headers = {
        "Content-Disposition": f'attachment; filename="{fname}"',
        "X-EconAI-Version": "taller-wins-probe",
        "X-EconAI-Time": _dt.datetime.utcnow().isoformat(),
    }
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Root — redirect to dashboard
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/dashboard.html")
