"""
Page import — split PDFs or copy images into a project's annotations/ folder.
Creates an empty LabelMe JSON for each page.
Requires: pymupdf (pip install pymupdf)
"""

from __future__ import annotations

import json
from pathlib import Path
from PIL import Image as PILImage


EMPTY_LABELME = {
    "version": "5.0.0",
    "flags": {},
    "shapes": [],
    "imageData": None,
}


def _next_page_index(ann_dir: Path) -> int:
    existing = [p.stem for p in ann_dir.glob("page_*.json")]
    nums = []
    for s in existing:
        try:
            nums.append(int(s.split("_")[1]))
        except (IndexError, ValueError):
            pass
    return max(nums, default=0) + 1


def _write_page(img_path: Path, ann_dir: Path, page_num: int) -> dict:
    """Save image + empty LabelMe JSON for one page. Returns info dict."""
    stem = f"page_{page_num}"
    dest_img = ann_dir / f"{stem}.jpg"

    # Convert to JPEG if needed, get dimensions
    img = PILImage.open(img_path).convert("RGB")
    w, h = img.size
    img.save(str(dest_img), "JPEG", quality=92)

    meta = {**EMPTY_LABELME, "imagePath": dest_img.name,
            "imageHeight": h, "imageWidth": w}
    (ann_dir / f"{stem}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {"stem": stem, "width": w, "height": h}


def import_pdf(pdf_bytes: bytes, ann_dir: Path, start_idx: int) -> list[dict]:
    """Split a PDF into page images. Returns list of page info dicts."""
    import fitz  # pymupdf
    import tempfile, os

    pages = []
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        doc = fitz.open(tmp_path)
        for i, page in enumerate(doc):
            mat = fitz.Matrix(2.0, 2.0)  # 2x zoom → ~150 dpi for typical A4 scan
            pix = page.get_pixmap(matrix=mat)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as ptmp:
                pix.save(ptmp.name)
                info = _write_page(Path(ptmp.name), ann_dir, start_idx + i)
                pages.append(info)
                os.unlink(ptmp.name)
        doc.close()
    finally:
        os.unlink(tmp_path)

    return pages


def import_image(img_bytes: bytes, ann_dir: Path, page_num: int) -> dict:
    """Import a single image file. Returns page info dict."""
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(img_bytes)
        tmp_path = tmp.name
    try:
        info = _write_page(Path(tmp_path), ann_dir, page_num)
    finally:
        os.unlink(tmp_path)
    return info


def import_files(files: list[tuple[str, bytes]], ann_dir: Path) -> list[dict]:
    """
    Import a list of (filename, bytes) pairs into ann_dir.
    PDFs are split page by page; images are imported directly.
    Returns list of page info dicts.
    """
    ann_dir.mkdir(parents=True, exist_ok=True)
    start = _next_page_index(ann_dir)
    results = []
    idx = start
    for filename, data in files:
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            pages = import_pdf(data, ann_dir, idx)
            results.extend(pages)
            idx += len(pages)
        elif ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"):
            info = import_image(data, ann_dir, idx)
            results.append(info)
            idx += 1
        else:
            results.append({"stem": filename, "error": f"Unsupported file type: {ext}"})
    return results
