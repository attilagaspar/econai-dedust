"""
Page import — read PDFs or images from a local path into a project's annotations/ folder.
Reads files directly from disk; no browser upload or temp files.
Requires: pymupdf (pip install pymupdf)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EMPTY_LABELME = {
    "version": "5.0.0",
    "flags": {},
    "shapes": [],
    "imageData": None,
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def _next_page_index(ann_dir: Path) -> int:
    nums = []
    for p in ann_dir.glob("page_*.json"):
        m = re.match(r"page_(\d+)", p.stem)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


def _save_page(img_path: Path, ann_dir: Path, page_num: int) -> dict:
    """Copy/convert an image file into ann_dir as page_N.jpg + empty JSON."""
    from PIL import Image as PILImage
    stem = f"page_{page_num}"
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


def _import_pdf(pdf_path: Path, ann_dir: Path, start_idx: int) -> list[dict]:
    import fitz
    pages = []
    doc   = fitz.open(str(pdf_path))
    for i, page in enumerate(doc):
        pix  = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        stem = f"page_{start_idx + i}"
        dest = ann_dir / f"{stem}.jpg"
        pix.save(str(dest))
        w, h = pix.width, pix.height
        meta = {**EMPTY_LABELME, "imagePath": dest.name,
                "imageHeight": h, "imageWidth": w}
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

    # Collect files to import
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
    idx     = _next_page_index(ann_dir)

    for f in files:
        ext = f.suffix.lower()
        try:
            if ext == ".pdf":
                pages = _import_pdf(f, ann_dir, idx)
                results.extend(pages)
                idx += len(pages)
            elif ext in IMAGE_EXTS:
                info = _save_page(f, ann_dir, idx)
                results.append(info)
                idx += 1
        except Exception as e:
            results.append({"stem": f.name, "error": str(e)})

    return results
