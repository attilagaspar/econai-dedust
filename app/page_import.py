"""
Page import — read PDFs or images from a local path into a project's annotations/ folder.
Reads files directly from disk; no browser upload or temp files.
Requires: pymupdf (pip install pymupdf)

Naming convention:
  xyz.pdf  (N pages)  →  xyz_1.jpg, xyz_2.jpg, …, xyz_N.jpg
  abc.png  (image)    →  abc.jpg  (stem preserved as-is)
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


def _import_pdf(pdf_path: Path, ann_dir: Path, base: str | None = None) -> list[dict]:
    import fitz
    if base is None:
        base = _sanitize(pdf_path.stem)
    pages = []
    doc   = fitz.open(str(pdf_path))
    n_digits = len(str(len(doc)))   # zero-pad to keep natural sort
    for i, page in enumerate(doc):
        pix  = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        stem = f"{base}_{str(i + 1).zfill(n_digits)}"
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
