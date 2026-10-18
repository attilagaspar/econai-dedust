"""
Crop round trip — crops as pages (plan doc
knowledge_base/09_hierarchical_layout_compass.md §H8, "crops as pages").

Two operations on top of the detection-scope geometry in app/scope.py /
app/infer_layout.py:

  derive_crops   every box of a chosen PARENT label on the selected pages of
                 a SOURCE project becomes an image page of a TARGET project
                 (`<stem>__crop<k>.jpg` + LabelMe JSON). The crop page's
                 `flags.crop_source` records where it came from — the
                 PROVENANCE — including the record id (`group_id`) and the
                 `continuation` mark of the parent box, so fragments of one
                 record stay linked across crops. Idempotent: an unchanged
                 source box leaves its crop alone (only the provenance's
                 record fields are refreshed); a moved / vanished box makes
                 the crop STALE, replaced only when the policy says so, and
                 a crop page with human work is never overwritten unless
                 the policy is `force`.

  write_back     the content of a crop page (any shape, with every text
                 layer, lattice field, authority, structured, blank flag,
                 clip … intact) is translated to PAGE coordinates and
                 written onto the source page. Every written shape is
                 stamped `crop_origin = {project, stem}`, so a later
                 write-back of the same crop replaces exactly those shapes
                 and nothing else; shapes without a crop_origin (the parent
                 box, the page's own content) or with another origin are
                 never touched. Lattice `table` ids are renumbered when they
                 collide with other content on the page. A page is written
                 only when its shape multiset changes.

The page stays the source of truth; the crop is a working surface tied to it
by provenance. Any label can be the parent, any project the source — nothing
here knows about firms, tables or any one research project.

Everything is pure file-in / file-out with an injected JSON writer (the
server's atomic writer); no server state.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.infer_layout import box_of, crop_rect
from app.records import reading_order, normalize_layout

DEFAULT_MARGIN = 12.0
DEFAULT_SOURCE_STATUSES = ("corrected", "verified")
DEFAULT_WRITEBACK_STATUSES = ("verified",)
POLICIES = ("keep", "replace", "force")
CROP_SEP = "__crop"
BBOX_TOL = 0.5          # px: a box is "the same" when every edge is within this
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
PROV_KEY = "crop_source"      # page flag on a crop page
ORIGIN_KEY = "crop_origin"    # shape field on a source page


# ── small helpers ────────────────────────────────────────────────────────────

def natural_key(name: str) -> tuple:
    """page_1 < page_2 < page_10; `<stem>__crop<k>` sorts by stem then k."""
    return tuple(int(p) if p.isdigit() else p.lower()
                 for p in re.split(r"(\d+)", name))


def crop_stem(stem: str, k: int) -> str:
    return f"{stem}{CROP_SEP}{k}"


def parse_crop_stem(cstem: str):
    """('page_3', 2) for 'page_3__crop2'; None when not a crop stem."""
    m = re.match(r"^(.*)" + re.escape(CROP_SEP) + r"(\d+)$", cstem)
    return (m.group(1), int(m.group(2))) if m else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bbox_eq(a, b, tol: float = BBOX_TOL) -> bool:
    try:
        return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b)) and len(a) == len(b) == 4
    except (TypeError, ValueError):
        return False


def _page_size(data: dict, img_path: Path | None = None) -> tuple:
    w, h = data.get("imageWidth"), data.get("imageHeight")
    try:
        w, h = int(w), int(h)
        if w > 0 and h > 0:
            return w, h
    except (TypeError, ValueError):
        pass
    if img_path is not None:
        from PIL import Image
        with Image.open(img_path) as im:
            return im.size
    return 0, 0


def find_image(ann_dir: Path, data: dict, stem: str) -> Path | None:
    cand = ann_dir / (data.get("imagePath") or "")
    if data.get("imagePath") and cand.exists():
        return cand
    for ext in IMAGE_EXTS:
        p = ann_dir / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _status(data: dict) -> str:
    return (data.get("flags") or {}).get("status") or "predicted"


def is_human_shape(shape: dict) -> bool:
    """Unscored = made or touched by a person (the convention of scope.py)."""
    return shape.get("score") is None


def page_has_human_work(data: dict) -> bool:
    return any(is_human_shape(s) for s in (data.get("shapes") or []))


def _shape_multiset(shapes) -> list:
    return sorted(json.dumps(s, sort_keys=True, ensure_ascii=False) for s in shapes)


# ── geometry: shifting a shape with ALL its content ──────────────────────────

def shift_shape(shape: dict, dx: float, dy: float, clip=None) -> dict | None:
    """A deep copy of `shape` translated by (dx, dy): the box points and the
    internal row bands (`row_struct.rows[].y0/y1`, absolute page pixels).
    Every other field (text layers, lattice fields, authority, structured,
    blank flags, clips, record fields …) is kept verbatim; the served `_idx`
    is dropped. With clip=(x1, y1, x2, y2) the box is clipped to that
    rectangle (in the TARGET coordinate frame); a degenerate result → None."""
    pts = shape.get("points") or []
    if len(pts) < 2:
        return None
    x1, y1, x2, y2 = box_of(pts)
    x1 += dx; x2 += dx; y1 += dy; y2 += dy
    if clip is not None:
        x1 = max(x1, clip[0]); y1 = max(y1, clip[1])
        x2 = min(x2, clip[2]); y2 = min(y2, clip[3])
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        return None
    new = json.loads(json.dumps(shape, ensure_ascii=False))
    new.pop("_idx", None)
    new["points"] = [[x1, y1], [x2, y2]]
    rs = new.get("row_struct")
    if isinstance(rs, dict):
        for r in rs.get("rows") or []:
            for key in ("y0", "y1"):
                if r.get(key) is not None:
                    try:
                        r[key] = float(r[key]) + dy
                    except (TypeError, ValueError):
                        pass
    return new


# ── parents in reading order ─────────────────────────────────────────────────

def ordered_parents(data: dict, label: str, layout="auto") -> list:
    """[(shape_index, bbox)] of the parent-label boxes in canonical reading
    order (columns then y — the record sweep's order, app/records.py), so k
    in `<stem>__crop<k>` is stable and meaningful."""
    shapes = data.get("shapes") or []
    w, _ = _page_size(data)
    if w <= 0:
        w = max((box_of(s["points"])[2] for s in shapes
                 if len(s.get("points") or []) >= 2), default=1.0)
    order = reading_order(shapes, label, float(w), normalize_layout(layout))
    out = []
    for i in order:
        b = box_of(shapes[i]["points"])
        if b[2] - b[0] > 0 and b[3] - b[1] > 0:
            out.append((i, b))
    return out


def locate_source_shape(data: dict, prov: dict) -> int | None:
    """Index of the parent box a provenance record points at: the shape at
    `shape_idx` when label and bbox still match, else any shape of that
    label with the same bbox (indices shift when boxes are added or
    removed). None = the box moved or vanished → the provenance is stale."""
    shapes = data.get("shapes") or []
    label, bbox = prov.get("label"), prov.get("bbox")
    i = prov.get("shape_idx")
    if isinstance(i, int) and 0 <= i < len(shapes):
        s = shapes[i]
        if s.get("label") == label and len(s.get("points") or []) >= 2 \
                and _bbox_eq(box_of(s["points"]), bbox):
            return i
    for j, s in enumerate(shapes):
        if s.get("label") == label and len(s.get("points") or []) >= 2 \
                and _bbox_eq(box_of(s["points"]), bbox):
            return j
    return None


def provenance_for(src_project: str, stem: str, idx: int, shape: dict,
                   bbox, crop, label: str, margin: float, status: str) -> dict:
    gid = shape.get("group_id")
    return {"project": src_project, "stem": stem, "shape_idx": idx,
            "bbox": [float(v) for v in bbox], "crop_rect": [int(v) for v in crop],
            "label": label, "margin": float(margin), "source_status": status,
            "group_id": gid if isinstance(gid, int) and not isinstance(gid, bool) else None,
            "continuation": bool(shape.get("continuation")),
            "ts": _now()}


_REFRESHABLE = ("shape_idx", "group_id", "continuation", "source_status")


def provenance_matches(existing: dict | None, prov: dict) -> bool:
    """Same source box, same framing: project, stem, label, bbox, crop rect."""
    if not isinstance(existing, dict):
        return False
    return (existing.get("project") == prov["project"]
            and existing.get("stem") == prov["stem"]
            and existing.get("label") == prov["label"]
            and _bbox_eq(existing.get("bbox") or [], prov["bbox"])
            and _bbox_eq(existing.get("crop_rect") or [], prov["crop_rect"], tol=0))


def _children_in(data: dict, parent_idx: int, parent_label: str, bbox) -> list:
    """(index, shape) of every shape whose centre lies inside the box, the
    parent-label shapes excluded (the parent is the input, never content)."""
    out = []
    for i, s in enumerate(data.get("shapes") or []):
        if i == parent_idx or s.get("label") == parent_label:
            continue
        pts = s.get("points") or []
        if len(pts) < 2:
            continue
        b = box_of(pts)
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        if bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
            out.append((i, s))
    return out


# ── A. derive crops ──────────────────────────────────────────────────────────

def derive_crops(src_ann: Path, src_project: str, tgt_ann: Path, tgt_project: str,
                 label: str, write_json, statuses=None, pattern: str | None = None,
                 margin: float = DEFAULT_MARGIN, policy: str = "keep",
                 with_children: bool = False, layout="auto",
                 dry_run: bool = False) -> dict:
    """Cut every `label` box of the selected source pages into crop pages of
    the target project. See the module docstring for the contract.

    policy: keep    — never overwrite an existing crop page; stale → reported
            replace — regenerate stale crops, except pages with human shapes
                      or a non-predicted status (reported as protected)
            force   — regenerate stale crops regardless
    with_children: shapes whose centre lies inside the box travel along as
    initial shapes (translated, clipped, all content kept); the source copies
    are stamped `crop_origin` so the first write-back replaces them instead
    of duplicating them.

    Returns counts + the stale list; nothing is deleted, ever."""
    from PIL import Image

    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES} (got {policy!r})")
    statuses = set(statuses or DEFAULT_SOURCE_STATUSES)
    rx = re.compile(pattern) if pattern else None
    if not dry_run:
        tgt_ann.mkdir(parents=True, exist_ok=True)
    st = {"dry_run": dry_run, "label": label, "source": src_project,
          "target": tgt_project, "policy": policy, "margin": float(margin),
          "pages_scanned": 0, "pages_selected": 0, "pages_with_boxes": 0,
          "pages_without_image": 0, "boxes": 0,
          "created": 0, "unchanged": 0, "refreshed": 0, "replaced": 0,
          "protected": 0, "children_carried": 0, "source_pages_stamped": 0,
          "stale": [], "crops": []}

    for jf in sorted(src_ann.glob("*.json"), key=lambda p: natural_key(p.stem)):
        data = _load(jf)
        if data is None:
            continue
        st["pages_scanned"] += 1
        stem = jf.stem
        if _status(data) not in statuses or (rx and not rx.search(stem)):
            continue
        st["pages_selected"] += 1
        parents = ordered_parents(data, label, layout)
        if not parents:
            continue
        st["pages_with_boxes"] += 1
        img_path = find_image(src_ann, data, stem)
        if img_path is None:
            st["pages_without_image"] += 1
            continue
        w, h = _page_size(data, img_path)
        im = None                      # opened lazily: unchanged pages cost no decode
        src_dirty = False
        try:
            for k, (idx, bbox) in enumerate(parents):
                st["boxes"] += 1
                shape = data["shapes"][idx]
                crop = crop_rect(bbox, w, h, margin)
                if crop[2] - crop[0] <= 1 or crop[3] - crop[1] <= 1:
                    continue
                cstem = crop_stem(stem, k)
                cj = tgt_ann / f"{cstem}.json"
                prov = provenance_for(src_project, stem, idx, shape, bbox, crop,
                                      label, margin, _status(data))
                existing = _load(cj) if cj.exists() else None
                entry = {"crop_stem": cstem, "source_stem": stem, "k": k,
                         "shape_idx": idx, "group_id": prov["group_id"],
                         "continuation": prov["continuation"]}
                if existing is not None:
                    ex = (existing.get("flags") or {}).get(PROV_KEY)
                    if provenance_matches(ex, prov):
                        # same box, same framing → the crop stays; only the
                        # record fields / index / status of the provenance move
                        delta = {f: prov[f] for f in _REFRESHABLE if ex.get(f) != prov[f]}
                        if delta:
                            ex.update(delta)
                            ex["refreshed"] = prov["ts"]
                            st["refreshed"] += 1
                            entry["action"] = "refreshed"
                            entry["refreshed_fields"] = sorted(delta)
                            if not dry_run:
                                write_json(cj, existing)
                        else:
                            st["unchanged"] += 1
                            entry["action"] = "unchanged"
                        st["crops"].append(entry)
                        continue
                    # stale: the box at this k moved / was re-framed, or the
                    # page carries someone else's crop
                    if not isinstance(ex, dict):
                        reason = "no_provenance"
                    elif ex.get("project") != src_project or ex.get("stem") != stem \
                            or ex.get("label") != label:
                        reason = "foreign"
                    elif not _bbox_eq(ex.get("bbox") or [], prov["bbox"]):
                        reason = "moved"
                    else:
                        reason = "reframed"
                    protected = page_has_human_work(existing) or _status(existing) != "predicted"
                    stale = {**entry, "reason": reason, "protected": protected,
                             "crop_status": _status(existing)}
                    if policy == "keep" or (protected and policy != "force"):
                        if protected:
                            st["protected"] += 1
                        stale["action"] = "kept"
                        st["stale"].append(stale)
                        continue
                    stale["action"] = "replaced"
                    st["stale"].append(stale)
                    st["replaced"] += 1
                    entry["action"] = "replaced"
                else:
                    st["created"] += 1
                    entry["action"] = "created"

                # (re)generate the crop image + page
                kids = []
                if with_children:
                    for ci, child in _children_in(data, idx, label, bbox):
                        moved = shift_shape(child, -crop[0], -crop[1],
                                            clip=(0, 0, crop[2] - crop[0], crop[3] - crop[1]))
                        if moved is None:
                            continue
                        moved.pop(ORIGIN_KEY, None)
                        kids.append(moved)
                        origin = {"project": tgt_project, "stem": cstem}
                        if child.get(ORIGIN_KEY) != origin:
                            child[ORIGIN_KEY] = origin
                            src_dirty = True
                    st["children_carried"] += len(kids)
                entry["children"] = len(kids)
                st["crops"].append(entry)
                if dry_run:
                    continue
                if im is None:
                    im = Image.open(img_path)
                    im.load()
                im.crop(crop).convert("RGB").save(tgt_ann / f"{cstem}.jpg", quality=92)
                page = {"version": "5.0.0",
                        "flags": {"status": "predicted", PROV_KEY: prov},
                        "shapes": kids, "imagePath": f"{cstem}.jpg", "imageData": None,
                        "imageWidth": crop[2] - crop[0], "imageHeight": crop[3] - crop[1]}
                write_json(cj, page)
            # crops of boxes that no longer exist (k beyond the current count)
            for old in tgt_ann.glob(f"{stem}{CROP_SEP}*.json"):
                parsed = parse_crop_stem(old.stem)
                if parsed is None or parsed[0] != stem or parsed[1] < len(parents):
                    continue
                ex_doc = _load(old) or {}
                ex = (ex_doc.get("flags") or {}).get(PROV_KEY) or {}
                if ex.get("project") != src_project or ex.get("label") != label:
                    continue
                st["stale"].append({"crop_stem": old.stem, "source_stem": stem,
                                    "k": parsed[1], "reason": "vanished",
                                    "protected": page_has_human_work(ex_doc)
                                    or _status(ex_doc) != "predicted",
                                    "crop_status": _status(ex_doc), "action": "kept"})
        finally:
            if im is not None:
                im.close()
        if src_dirty:
            st["source_pages_stamped"] += 1
            if not dry_run:
                write_json(jf, data)
    return st


# ── B. write back ────────────────────────────────────────────────────────────

def renumber_tables(shapes: list, taken: set) -> list:
    """Lattice `table` ids of `shapes` that collide with ids in `taken` are
    remapped to fresh ids (max + 1 …); non-colliding ids are kept. Mutates
    and returns `shapes`; the mapping is deterministic for a given input."""
    ids = sorted({s["table"] for s in shapes
                  if isinstance(s.get("table"), int) and not isinstance(s.get("table"), bool)})
    if not ids:
        return shapes
    used = {t for t in taken if isinstance(t, int) and not isinstance(t, bool)}
    mapping, nxt = {}, (max(used | set(ids)) + 1 if used else 0)
    for t in ids:
        if t in used:
            mapping[t] = nxt
            used.add(nxt)
            nxt += 1
        else:
            used.add(t)
    if mapping:
        for s in shapes:
            if s.get("table") in mapping:
                s["table"] = mapping[s["table"]]
    return shapes


def write_crop_onto_page(page: dict, crop_page: dict, crop_project: str,
                         crop_stem_: str) -> dict:
    """Pure: translate the crop page's shapes onto `page` (mutated).
    Returns {"status": ok|stale, "removed", "inserted", "parent_idx"}."""
    prov = (crop_page.get("flags") or {}).get(PROV_KEY) or {}
    pidx = locate_source_shape(page, prov)
    if pidx is None:
        return {"status": "stale", "removed": 0, "inserted": 0, "parent_idx": None}
    origin = {"project": crop_project, "stem": crop_stem_}
    cr = prov.get("crop_rect") or [0, 0, 0, 0]
    dx, dy = float(cr[0]), float(cr[1])
    w, h = _page_size(page)
    clip = (0, 0, w, h) if w > 0 and h > 0 else None
    gid = prov.get("group_id")
    new = []
    for s in crop_page.get("shapes") or []:
        moved = shift_shape(s, dx, dy, clip=clip)
        if moved is None:
            continue
        moved[ORIGIN_KEY] = dict(origin)
        if gid is not None:
            moved["group_id"] = gid
        new.append(moved)
    shapes = page.get("shapes") or []
    kept, removed, at = [], 0, None
    for i, s in enumerate(shapes):
        if s.get(ORIGIN_KEY) == origin:
            removed += 1
            if at is None:
                at = len(kept)
            continue
        kept.append(s)
    if at is None:
        # first write-back: right after the parent box (its index among the kept)
        at = next((j for j, s in enumerate(kept) if s is shapes[pidx]), len(kept) - 1) + 1
    renumber_tables(new, {s.get("table") for s in kept if s.get("table") is not None})
    page["shapes"] = kept[:at] + new + kept[at:]
    return {"status": "ok", "removed": removed, "inserted": len(new), "parent_idx": pidx}


def write_back(crop_ann: Path, crop_project: str, projects_root: Path, write_json,
               statuses=None, pattern: str | None = None, dry_run: bool = False) -> dict:
    """Write every selected crop page (default: verified ones) back onto its
    source page. Crops of one source page are applied together in k order
    and the page is written once, only when its shape multiset changed."""
    statuses = set(statuses or DEFAULT_WRITEBACK_STATUSES)
    rx = re.compile(pattern) if pattern else None
    st = {"dry_run": dry_run, "crop_project": crop_project,
          "crop_pages_scanned": 0, "crop_pages_selected": 0, "no_provenance": 0,
          "source_pages": 0, "written": 0, "unchanged": 0,
          "shapes_written": 0, "shapes_replaced": 0,
          "stale": [], "missing_source": [], "pages": []}
    groups: dict = {}
    for jf in sorted(crop_ann.glob("*.json"), key=lambda p: natural_key(p.stem)):
        cdoc = _load(jf)
        if cdoc is None:
            continue
        st["crop_pages_scanned"] += 1
        if _status(cdoc) not in statuses or (rx and not rx.search(jf.stem)):
            continue
        prov = (cdoc.get("flags") or {}).get(PROV_KEY)
        if not isinstance(prov, dict) or not prov.get("project") or not prov.get("stem"):
            st["no_provenance"] += 1
            continue
        st["crop_pages_selected"] += 1
        groups.setdefault((prov["project"], prov["stem"]), []).append((jf.stem, cdoc))

    for (sproj, sstem), crops in groups.items():
        sfile = projects_root / sproj / "annotations" / f"{sstem}.json"
        page = _load(sfile) if sfile.exists() else None
        if page is None:
            st["missing_source"].append({"source_project": sproj, "source_stem": sstem,
                                         "crops": [c for c, _ in crops]})
            continue
        st["source_pages"] += 1
        before = _shape_multiset(page.get("shapes") or [])
        rec = {"source_project": sproj, "source_stem": sstem,
               "source_folder": str(sfile.parent), "crops": [],
               "n_before": len(page.get("shapes") or [])}
        for cstem, cdoc in crops:
            res = write_crop_onto_page(page, cdoc, crop_project, cstem)
            if res["status"] == "stale":
                st["stale"].append({"crop_stem": cstem, "source_project": sproj,
                                    "source_stem": sstem, "reason": "source box moved or vanished"})
                rec["crops"].append({"crop_stem": cstem, "status": "stale"})
                continue
            st["shapes_written"] += res["inserted"]
            st["shapes_replaced"] += res["removed"]
            rec["crops"].append({"crop_stem": cstem, "status": "ok",
                                 "inserted": res["inserted"], "replaced": res["removed"]})
        changed = _shape_multiset(page.get("shapes") or []) != before
        rec["changed"] = changed
        rec["n_after"] = len(page.get("shapes") or [])
        st["pages"].append(rec)
        if changed:
            st["written"] += 1
            if not dry_run:
                write_json(sfile, page)
        else:
            st["unchanged"] += 1
    return st
