"""Record-level structured extraction (knowledge_base/13 §1b, step E1b).

One record = the fragments behind one `group_id` (app/records.py), in
reading order. This module turns a record into ONE schema-constrained LLM
call — every fragment's crop image plus whatever text the pages already hold
for it, in order — and stores the parsed object on the record's head
fragment under `shape["structured"]`, the same field the per-annotation
extraction uses (05 §2), so the editor, the Excel "Structured" sheet and the
JSON export all see record objects without change. The `record` key inside
marks it as covering the whole record.

Generic: any project with a record label and a schema file; nothing here
knows what a firm is.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

PAD = 4                 # px around a fragment crop, like the cell LLM call
JPEG_QUALITY = 92
MAX_FRAGMENTS_DEFAULT = 8

DEFAULT_PROMPT = (
    "You are given one record of a printed directory as a sequence of image "
    "fragments in reading order (a record may continue across columns and "
    "pages; later fragments are marked as continuations). Where text is "
    "attached to a fragment it was read from the same image by OCR or a "
    "human and may contain errors; the image is authoritative. Extract the "
    "fields of the schema for THIS record only. Copy names and numbers as "
    "printed; use null for a field the record does not print; never invent."
)


def _text_of(shape: dict) -> str:
    """Whole best text of a shape: Human > LLM > Tesseract > EasyOCR > PDF;
    row-structured shapes contribute their rows' best text, one per line."""
    rows = (shape.get("row_struct") or {}).get("rows") or []
    if rows:
        lines = []
        for r in rows:
            for k in ("human", "llm", "ocr", "pdf"):
                v = r.get(k)
                if isinstance(v, str) and v.strip():
                    lines.append(v.strip()); break
        if lines:
            return "\n".join(lines)
    for c in ((shape.get("human_output") or {}).get("human_corrected_text"),
              (shape.get("openai_output") or {}).get("response"),
              (shape.get("tesseract_output") or {}).get("ocr_text"),
              (shape.get("easyocr_output") or {}).get("ocr_text"),
              shape.get("pdf_text")):
        if isinstance(c, str) and c.strip():
            return c.strip()
    return ""


def _box(shape: dict):
    pts = shape.get("points") or []
    if len(pts) < 2:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def fragment_text(data: dict, idx: int, record_label: str) -> str:
    """Text for one fragment: the fragment shape's own text, then the text of
    every other shape whose centre lies inside it (children written back
    from a crop project, or drawn by hand), top-to-bottom, left-to-right."""
    shapes = data.get("shapes") or []
    sh = shapes[idx]
    bb = _box(sh)
    parts = []
    own = _text_of(sh)
    if own:
        parts.append(own)
    if bb is not None:
        kids = []
        for i, s in enumerate(shapes):
            if i == idx or s.get("label") == record_label:
                continue
            b = _box(s)
            if b is None:
                continue
            cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
            if bb[0] <= cx <= bb[2] and bb[1] <= cy <= bb[3]:
                t = _text_of(s)
                if t:
                    kids.append((b[1], b[0], s.get("label") or "", t))
        for _, _, lab, t in sorted(kids):
            parts.append(f"[{lab}] {t}" if lab else t)
    return "\n".join(parts).strip()


def fragment_image_b64(img_path: Path, bbox, pad: int = PAD):
    """JPEG crop of the fragment as a base64 string (None when no image)."""
    if img_path is None or not Path(img_path).exists():
        return None
    from PIL import Image
    im = Image.open(str(img_path)).convert("RGB")
    w, h = im.size
    x1, y1, x2, y2 = bbox
    crop = im.crop((max(0, int(x1) - pad), max(0, int(y1) - pad),
                    min(w, int(x2) + pad), min(h, int(y2) + pad)))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode()


def record_inputs(record: dict, pages: dict, ann_dir: Path, record_label: str,
                  max_fragments: int = MAX_FRAGMENTS_DEFAULT, with_images: bool = True,
                  with_text: bool = True) -> list:
    """[{stem, idx, continuation, text, image_b64, truncated}] for a record's
    fragments in reading order. Images are capped at `max_fragments` (the
    rest contribute text only, so a very long record still fits a call)."""
    from app import crops as _crops
    out = []
    for n, f in enumerate(record.get("fragments") or []):
        stem, idx = f["stem"], f["idx"]
        data = pages.get(stem)
        if not data:
            continue
        shapes = data.get("shapes") or []
        if idx < 0 or idx >= len(shapes):
            continue
        sh = shapes[idx]
        bb = _box(sh)
        item = {"stem": stem, "idx": idx, "continuation": bool(sh.get("continuation")),
                "text": fragment_text(data, idx, record_label) if with_text else "",
                "image_b64": None, "truncated": False}
        if with_images and bb is not None:
            if n < max_fragments:
                item["image_b64"] = fragment_image_b64(_crops.find_image(ann_dir, data, stem), bb)
            else:
                item["truncated"] = True
        out.append(item)
    return out


def build_messages(prompt: str, inputs: list) -> list:
    """One user message: the prompt, then per fragment a header line, its
    image (when present) and its attached text (when present)."""
    content = [{"type": "text", "text": (prompt or DEFAULT_PROMPT).strip()}]
    n = len(inputs)
    for k, it in enumerate(inputs, 1):
        head = f"--- fragment {k} of {n}" + (" (continuation)" if it["continuation"] else " (record head)")
        if it["truncated"]:
            head += " — image omitted, text only"
        content.append({"type": "text", "text": head})
        if it.get("image_b64"):
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{it['image_b64']}",
                                          "detail": "high"}})
        if it.get("text"):
            content.append({"type": "text", "text": "attached text:\n" + it["text"]})
    return [{"role": "user", "content": content}]


def structured_payload(parsed: dict, schema_name: str, model: str, ts: str,
                       record: dict, gid, prev: dict | None) -> dict:
    """The `structured` dict stored on the head fragment. A human-edited
    `data` survives a re-run (the fresh LLM output goes to `llm`)."""
    prev = prev or {}
    return {
        "schema_name": schema_name,
        "llm": parsed,
        "data": prev.get("data") if prev.get("edited") else parsed,
        "edited": bool(prev.get("edited")),
        "model": model, "ts": ts,
        "record": {"group_id": gid,
                   "fragments": [{"stem": f["stem"], "idx": f["idx"]}
                                 for f in (record.get("fragments") or [])],
                   "n_fragments": len(record.get("fragments") or [])},
    }


def summary(structured: dict | None) -> dict | None:
    """What the record browser shows: schema, when, by what, edited, the data."""
    if not isinstance(structured, dict):
        return None
    return {"schema_name": structured.get("schema_name"), "ts": structured.get("ts"),
            "model": structured.get("model"), "edited": bool(structured.get("edited")),
            "record": bool(structured.get("record")),
            "data": structured.get("data") if structured.get("data") is not None else structured.get("llm")}
