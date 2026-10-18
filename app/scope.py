"""
Detection scope — nested detection within parent shapes (plan doc
knowledge_base/09_hierarchical_layout_compass.md §H8).

A scope is a string: "page" (whole-page detection, the default) or
"within:<parent_label>" — the model is trained on crops of every
<parent_label> shape and run on crops of the current <parent_label> shapes.
Any label can be a parent; firm_area → parts and table_region → cells are
two instances of the same mechanism.

Parent–child containment is GEOMETRIC and computed on the fly (a child's
centre lies inside the parent box; smallest containing parent wins). There
is no stored parent field, so nothing can go stale when a parent is moved.

Everything here is pure (files in, files out, no server state); the crop
geometry is shared with infer_layout.py so training crops and inference
crops are framed identically.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.infer_layout import box_of, crop_rect, map_boxes_to_page, parents_in

PAGE_SCOPE = "page"
WITHIN_PREFIX = "within:"
DEFAULT_MARGIN = 12.0

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


# ── scope strings ────────────────────────────────────────────────────────────

def normalize_scope(scope) -> str:
    """'' / None / 'page' → 'page'; 'within:X' kept (whitespace-stripped)."""
    s = (scope or "").strip()
    if not s or s == PAGE_SCOPE:
        return PAGE_SCOPE
    if s.startswith(WITHIN_PREFIX):
        parent = s[len(WITHIN_PREFIX):].strip()
        if not parent:
            raise ValueError("scope 'within:' needs a parent label")
        return WITHIN_PREFIX + parent
    raise ValueError(f"unknown detection scope {scope!r} "
                     f"(expected 'page' or 'within:<label>')")


def parent_label_of(scope) -> str | None:
    """The parent label of a within-scope, None for page scope."""
    s = normalize_scope(scope)
    return s[len(WITHIN_PREFIX):] if s.startswith(WITHIN_PREFIX) else None


def is_nested(scope) -> bool:
    return parent_label_of(scope) is not None


def scope_labels(labels: list, scope) -> list:
    """The model's class list under a scope: all project labels minus the
    parent label (the parent is the input, never a prediction)."""
    parent = parent_label_of(scope)
    if parent is None:
        return list(labels)
    out = [l for l in labels if l != parent]
    if not out:
        raise ValueError(f"scope within:{parent} leaves no child labels")
    return out


def scope_slug(scope) -> str:
    """Filesystem/ledger-safe short form: 'page' or 'within_<label>'."""
    s = normalize_scope(scope)
    return s.replace(":", "_")


# ── label compatibility (cross-project operations) ───────────────────────────

def missing_labels(source_labels, target_labels) -> list:
    """Source labels absent from the target palette, in source order.

    Cross-project inference needs every label the SOURCE model predicts to
    exist in the TARGET project (a one-label parent detector onto a project
    whose labels are that parent plus its parts, for instance). The source's
    label order defines the model's class ids and travels unchanged; the
    target may hold any number of further labels."""
    tset = set(target_labels or [])
    return [l for l in (source_labels or []) if l not in tset]


# ── containment ──────────────────────────────────────────────────────────────

def _center(box):
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def _area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def parent_index_for(child_box, parent_boxes: list) -> int | None:
    """Index of the parent whose box contains the child's centre; the
    smallest such parent when several do; None when none does."""
    cx, cy = _center(child_box)
    best, best_area = None, None
    for i, pb in enumerate(parent_boxes):
        if pb[0] <= cx <= pb[2] and pb[1] <= cy <= pb[3]:
            a = _area(pb)
            if best is None or a < best_area:
                best, best_area = i, a
    return best


def parent_boxes_of(shapes: list, parent_label: str) -> list:
    """Parent boxes on a page in the canonical (y, x) order."""
    return parents_in({"shapes": shapes}, parent_label)


def children_of_parents(shapes: list, parent_boxes: list, child_labels) -> dict:
    """{parent_index: [shape, ...]} for shapes whose label is a child label
    and whose centre lies in that parent. Shapes outside every parent are
    not returned."""
    child_set = set(child_labels)
    out = {i: [] for i in range(len(parent_boxes))}
    for sh in shapes:
        if sh.get("label") not in child_set:
            continue
        pts = sh.get("points") or []
        if len(pts) < 2:
            continue
        idx = parent_index_for(box_of(pts), parent_boxes)
        if idx is not None:
            out[idx].append(sh)
    return out


# ── training crop export ─────────────────────────────────────────────────────

def translate_clip(shape: dict, crop: tuple) -> dict | None:
    """A child shape in crop coordinates, clipped to the crop; None when the
    clipped box is degenerate."""
    x1, y1, x2, y2 = box_of(shape["points"])
    cx1, cy1, cx2, cy2 = crop
    nx1 = max(x1, cx1) - cx1; ny1 = max(y1, cy1) - cy1
    nx2 = min(x2, cx2) - cx1; ny2 = min(y2, cy2) - cy1
    if nx2 - nx1 <= 0 or ny2 - ny1 <= 0:
        return None
    return {"label": shape.get("label"),
            "points": [[nx1, ny1], [nx2, ny2]],
            "group_id": None, "shape_type": "rectangle", "flags": {}}


def crop_stem(stem: str, k: int) -> str:
    return f"{stem}__crop{k}"


def find_page_image(ann_dir: Path, data: dict, stem: str) -> Path | None:
    cand = ann_dir / (data.get("imagePath") or "")
    if data.get("imagePath") and cand.exists():
        return cand
    for ext in IMAGE_EXTS:
        p = ann_dir / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def export_crops(pages: list, parent_label: str, child_labels: list,
                 ann_dir: Path, crops_dir: Path,
                 margin: float = DEFAULT_MARGIN) -> dict:
    """Expand page dicts (as from coco_convert.collect_pages) into one crop
    page per parent shape, writing the crop JPEGs under crops_dir.

    Returns {"pages": [crop page dicts], "n_parents", "n_negatives",
    "n_children", "n_pages_without_image"}. A crop page has the same keys
    as a page dict (stem, data, status, n_shapes) so pages_to_coco works on
    it unchanged; its data carries the crop's own imagePath / size and the
    translated + clipped children. A parent with no children is a negative
    example (zero shapes). Split inheritance is the caller's job: export the
    train pages and the test pages separately."""
    from PIL import Image

    crops_dir.mkdir(parents=True, exist_ok=True)
    out_pages = []
    n_parents = n_negatives = n_children = n_noimg = 0
    for page in pages:
        data = page["data"]
        shapes = data.get("shapes") or []
        pboxes = parent_boxes_of(shapes, parent_label)
        if not pboxes:
            continue
        img_path = find_page_image(ann_dir, data, page["stem"])
        if img_path is None:
            n_noimg += 1
            continue
        with Image.open(img_path) as im:
            im.load()
            w, h = im.size
            kids = children_of_parents(shapes, pboxes, child_labels)
            for k, pb in enumerate(pboxes):
                crop = crop_rect(pb, w, h, margin)
                if crop[2] - crop[0] <= 1 or crop[3] - crop[1] <= 1:
                    continue
                cstem = crop_stem(page["stem"], k)
                fname = cstem + ".jpg"
                im.crop(crop).convert("RGB").save(crops_dir / fname,
                                                  quality=92)
                cshapes = [s for s in (translate_clip(sh, crop) for sh in kids[k])
                           if s is not None]
                n_parents += 1
                n_children += len(cshapes)
                if not cshapes:
                    n_negatives += 1
                out_pages.append({
                    "stem": cstem,
                    "data": {
                        "version": "5.0.0",
                        "flags": {"status": page.get("status"),
                                  "crop_of": page["stem"], "parent_index": k,
                                  "crop_rect": list(crop)},
                        "shapes": cshapes,
                        "imagePath": fname,
                        "imageWidth": crop[2] - crop[0],
                        "imageHeight": crop[3] - crop[1],
                    },
                    "status": page.get("status"),
                    "n_shapes": len(cshapes),
                })
    return {"pages": out_pages, "n_parents": n_parents,
            "n_negatives": n_negatives, "n_children": n_children,
            "n_pages_without_image": n_noimg}


def clear_crops(crops_dir: Path):
    """Remove stale crop images from an earlier export (derived data only)."""
    if not crops_dir.exists():
        return
    for p in crops_dir.iterdir():
        if p.suffix.lower() in IMAGE_EXTS and "__crop" in p.stem:
            try:
                p.unlink()
            except OSError:
                pass


# ── inference-time parents export ────────────────────────────────────────────

def export_parents(ann_dir: Path, parents_dir: Path, parent_label: str) -> dict:
    """Write one slim JSON per page holding only the parent shapes (label +
    points) and the image size — what infer_layout.py --parents needs. No
    text layers leave the machine. Skip (clutter) pages are omitted: they
    never receive predictions. Stale files are removed first."""
    parents_dir.mkdir(parents=True, exist_ok=True)
    for old in parents_dir.glob("*.json"):
        try:
            old.unlink()
        except OSError:
            pass
    n_pages = n_parents = 0
    for jf in sorted(ann_dir.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (data.get("flags") or {}).get("status") == "skip":
            continue
        pshapes = [{"label": sh.get("label"), "points": sh.get("points")}
                   for sh in (data.get("shapes") or [])
                   if sh.get("label") == parent_label
                   and len(sh.get("points") or []) >= 2]
        if not pshapes:
            continue
        slim = {"imagePath": data.get("imagePath", jf.stem + ".jpg"),
                "imageWidth": data.get("imageWidth"),
                "imageHeight": data.get("imageHeight"),
                "shapes": pshapes}
        (parents_dir / jf.name).write_text(
            json.dumps(slim, ensure_ascii=False), encoding="utf-8")
        n_pages += 1
        n_parents += len(pshapes)
    return {"n_pages": n_pages, "n_parents": n_parents}


# ── merge-apply ──────────────────────────────────────────────────────────────

def merge_children(ann_shapes: list, pred_shapes: list, parent_label: str,
                   child_labels) -> dict:
    """Merge predicted children into a page that already has parents.

    Removes earlier PREDICTED children (shapes carrying a score, a child
    label, centre inside a parent) and inserts the predicted children.
    Shapes without a score (human work), the parents, and scored shapes
    outside every parent are never touched. Returns
    {"shapes", "removed", "inserted", "n_parents", "changed"}."""
    pboxes = parent_boxes_of(ann_shapes, parent_label)
    if not pboxes:
        return {"shapes": ann_shapes, "removed": 0, "inserted": 0,
                "n_parents": 0, "changed": False}
    child_set = set(child_labels)
    kept, removed = [], 0
    for sh in ann_shapes:
        if (sh.get("score") is not None and sh.get("label") in child_set
                and len(sh.get("points") or []) >= 2
                and parent_index_for(box_of(sh["points"]), pboxes) is not None):
            removed += 1
            continue
        kept.append(sh)
    new = []
    for sh in pred_shapes:
        if sh.get("label") not in child_set or len(sh.get("points") or []) < 2:
            continue
        s = dict(sh)
        s.setdefault("score", 0.0)   # merge inserts are model output by definition
        new.append(s)
    changed = bool(removed or new)
    return {"shapes": kept + new, "removed": removed, "inserted": len(new),
            "n_parents": len(pboxes), "changed": changed}


def merge_apply(ann_dir: Path, pred_dir: Path, parent_label: str,
                child_labels, write_json) -> dict:
    """Apply predictions/*.json into annotations/*.json with merge_children.
    write_json(path, obj) is injected (the server's atomic writer)."""
    stats = {"applied": 0, "skipped_unchanged": 0, "skipped_no_parents": 0,
             "skipped_clutter": 0, "skipped_no_page": 0,
             "inserted": 0, "removed": 0}
    for pf in sorted(pred_dir.glob("*.json")):
        af = ann_dir / pf.name
        if not af.exists():
            stats["skipped_no_page"] += 1
            continue
        ann = json.loads(af.read_text(encoding="utf-8"))
        if (ann.get("flags") or {}).get("status") == "skip":
            stats["skipped_clutter"] += 1
            continue
        try:
            pred = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            continue
        res = merge_children(ann.get("shapes") or [], pred.get("shapes") or [],
                             parent_label, child_labels)
        if res["n_parents"] == 0:
            stats["skipped_no_parents"] += 1
            continue
        if not res["changed"]:
            stats["skipped_unchanged"] += 1
            continue
        ann["shapes"] = res["shapes"]
        write_json(af, ann)
        stats["applied"] += 1
        stats["inserted"] += res["inserted"]
        stats["removed"] += res["removed"]
    return stats


# ── merge-apply, page scope (by label set) ───────────────────────────────────

def merge_labels(ann_shapes: list, pred_shapes: list, labels) -> dict:
    """Merge whole-page predictions that carry a LABEL SET into a page that
    may already hold shapes (the page-scope counterpart of merge_children).

    Removes earlier PREDICTED shapes (carrying a score) whose label is in
    the set and inserts the predictions whose label is in the set. Shapes
    without a score (human work) and scored shapes of other labels (another
    model's output) are never touched. The new shapes take the place of
    the first removed one (page order stays stable for the editor);
    `changed` compares the shape multisets, so re-applying the same
    predictions reports no change. Returns
    {"shapes", "removed", "inserted", "changed"}."""
    label_set = set(labels or [])
    kept, removed, at = [], 0, None
    for sh in ann_shapes:
        if sh.get("score") is not None and sh.get("label") in label_set:
            removed += 1
            if at is None:
                at = len(kept)      # new shapes take the removed ones' place
            continue
        kept.append(sh)
    new = []
    for sh in pred_shapes:
        if sh.get("label") not in label_set or len(sh.get("points") or []) < 2:
            continue
        s = dict(sh)
        s.setdefault("score", 0.0)   # merge inserts are model output by definition
        new.append(s)
    at = len(kept) if at is None else at
    out = kept[:at] + new + kept[at:]
    return {"shapes": out, "removed": removed, "inserted": len(new),
            "changed": _shape_multiset(out) != _shape_multiset(ann_shapes)}


def _shape_multiset(shapes) -> list:
    """Order-insensitive canonical form for change detection."""
    return sorted(json.dumps(s, sort_keys=True, ensure_ascii=False) for s in shapes)


def merge_apply_labels(ann_dir: Path, pred_dir: Path, labels,
                       write_json) -> dict:
    """Apply predictions/*.json into annotations/*.json with merge_labels.
    Skip (clutter) pages are never populated; a page is written only when
    its shape list actually changes, so the operation is idempotent.
    write_json(path, obj) is injected (the server's atomic writer)."""
    stats = {"applied": 0, "skipped_unchanged": 0, "skipped_clutter": 0,
             "skipped_no_page": 0, "inserted": 0, "removed": 0}
    for pf in sorted(pred_dir.glob("*.json")):
        af = ann_dir / pf.name
        if not af.exists():
            stats["skipped_no_page"] += 1
            continue
        ann = json.loads(af.read_text(encoding="utf-8"))
        if (ann.get("flags") or {}).get("status") == "skip":
            stats["skipped_clutter"] += 1
            continue
        try:
            pred = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:
            continue
        res = merge_labels(ann.get("shapes") or [], pred.get("shapes") or [], labels)
        if not res["changed"]:
            stats["skipped_unchanged"] += 1
            continue
        ann["shapes"] = res["shapes"]
        write_json(af, ann)
        stats["applied"] += 1
        stats["inserted"] += res["inserted"]
        stats["removed"] += res["removed"]
    return stats
