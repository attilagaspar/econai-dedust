"""
Derive record fragments from start / stop / ignore roles (plan doc
knowledge_base/09_hierarchical_layout_compass.md §H5 / §H8, "starts and
stops are what the detector learns; fragments are derived").

The detector (or the annotator) marks SMALL, consistent objects: where a
record STARTS (a name line, an entry heading) and what STOPS it (a section
heading, an advertisement, a running foot …). Which label plays which role
is project configuration — `config.record_roles = {label: start | stop |
ignore}` — never code. From those marks this module builds the boxes of the
project's `record_label` (the fragments the sweep, the crop round trip and
the write-back already work on), per page:

    column top → first start/stop     a CONTINUATION fragment (the record
                                      open from the previous column / page
                                      runs on here) — only while a record
                                      is open and only if there is ink
    start → next start or stop        a fragment that begins with a start
    last start → column bottom        the record runs on into the next
                                      column / page (open at the end)
    stop                              ends the current fragment, starts none;
                                      what follows a stop until the next
                                      start belongs to no record
    ignore                            skipped, and its ink is masked out
                                      before the trims

A page with no starts and no stops while a record is open yields one
continuation fragment per column (a record running over several pages needs
no annotation on its continuation pages); with no record open it yields
nothing (an advertisement page after a stop).

Columns come from the record sweep's geometry (`records.split_x`,
`layout_columns`, the full-width rule: a box wider than WIDE_FRACTION of
the page cuts the page into bands read one after the other; a wide start
is a full-width fragment of its own, a wide stop closes the record. The
rule only applies to a two-column page — `layout_columns: 2`, or `auto`
with the narrow role boxes revealing a gutter — because in a single
column every heading is "wide" and must simply head its fragment). Every
fragment is trimmed to the ink: the bottom (and a continuation's top) to the
last / first inked row, the sides to the column's inked x-extent, plus a
margin; a fragment without ink is dropped. Ink = a cheap projection of the
page image (grey < threshold), ignore boxes masked out.

Output shapes carry the record label, `continuation: true` where the
fragment does not begin with a start, and a marker

    derived_from: {"kind": "record_roles", "bbox": [...], "continuation":
                   bool, "start_label": str | None, "column": k}

so a re-run can tell its own output from hand work. The REPLACEMENT RULE
(`is_replaceable`): a record-label shape is replaced by a re-run only when
it still carries the marker, its box is where the derivation put it (bbox
== marker bbox), its continuation mark is the derived one, it is not
`group_locked`, and it holds no content (no text layers, rows, authority,
structured data, clips, blank flag). Everything else — hand-drawn boxes,
moved or annotated derived boxes — is PROTECTED: kept as it is, and a new
fragment overlapping a protected box by at least OVERLAP_SUPPRESS of the
smaller area is suppressed (reported, not written).

Everything here is pure: page data + an ink map in, shapes out. Pages are
processed in order by the caller, which threads `carry_open` (is a record
open at the end of the previous page?) through `derive_page`.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.records import (WIDE_FRACTION, _bbox, _cx, _page_width, normalize_layout,
                         split_x)

ROLES = ("start", "stop", "ignore")
MARKER_KIND = "record_roles"
MARKER_KEY = "derived_from"
DEFAULT_MARGIN = 4.0          # px added around the inked extent
DEFAULT_THRESHOLD = 160       # grey level below which a pixel is ink
EDGE_FRACTION = 0.02          # scan borders: this much of the page edge is never ink
FURNITURE_FRACTION = 0.20     # an ignore box this close to the top/bottom edge is a running
                              # head/foot: its whole line is masked (page numbers in the margin)
MIN_INK_PIXELS = 2            # a row / column counts as inked from this many ink pixels
OVERLAP_SUPPRESS = 0.5        # fragment vs protected box: overlap / smaller area
BBOX_TOL = 0.5
CONTENT_KEYS = ("human_output", "human_correction", "openai_output", "ocr_output",
                "tesseract_output", "easyocr_output", "pdf_text", "row_struct",
                "authority", "structured", "clip", "crop_origin")


# ── config ───────────────────────────────────────────────────────────────────

def normalize_roles(roles, labels=None, record_label=None) -> dict:
    """{label: role} with every role in ROLES; labels not listed have no
    role. With `labels` every key must be a project label; the record label
    itself can carry no role (it is the output). ValueError otherwise."""
    if roles is None:
        return {}
    if not isinstance(roles, dict):
        raise ValueError("record_roles must be an object {label: start|stop|ignore}")
    out = {}
    for label, role in roles.items():
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"record_roles: bad label {label!r}")
        label = label.strip()
        if role in (None, ""):
            continue                       # "" = no role (clears)
        if role not in ROLES:
            raise ValueError(f"record_roles[{label!r}] must be one of {list(ROLES)} "
                             f"(got {role!r})")
        if labels is not None and label not in labels:
            raise ValueError(f"record_roles: {label!r} is not a project label "
                             f"(labels: {list(labels)})")
        if record_label and label == record_label:
            raise ValueError(f"record_roles: the record label {label!r} is the output "
                             f"of the derivation and can carry no role")
        out[label] = role
    return out


def record_roles_for(cfg: dict) -> dict:
    try:
        return normalize_roles((cfg or {}).get("record_roles"))
    except ValueError:
        return {}


# ── ink map ──────────────────────────────────────────────────────────────────

class InkMap:
    """Boolean ink mask of a page with cheap row / column projections.
    `ink[y, x]` is True where the grey level is below `threshold`. Ignore
    boxes are masked out with `mask_out`."""

    def __init__(self, mask):
        import numpy as np
        self.ink = np.asarray(mask, dtype=bool)
        self.h, self.w = self.ink.shape

    @classmethod
    def from_image(cls, path, threshold: int = DEFAULT_THRESHOLD,
                   edge_fraction: float = EDGE_FRACTION) -> "InkMap":
        import numpy as np
        from PIL import Image
        with Image.open(path) as im:
            g = np.asarray(im.convert("L"))
        ink = cls(g < int(threshold))
        ink.clear_edges(edge_fraction)
        return ink

    def clear_edges(self, fraction: float = EDGE_FRACTION):
        """Treat a frame of `fraction` × the shorter side around the page as
        paper: the dark scanner edge of a scan is a solid ink line that would
        otherwise pin every leading fragment to the very top of the page."""
        e = int(round(float(fraction) * min(self.w, self.h)))
        if e <= 0:
            return
        self.ink[:e, :] = False;  self.ink[-e:, :] = False
        self.ink[:, :e] = False;  self.ink[:, -e:] = False

    @classmethod
    def blank(cls, w: int, h: int) -> "InkMap":
        import numpy as np
        return cls(np.zeros((int(h), int(w)), dtype=bool))

    def _clamp(self, x1, y1, x2, y2):
        x1 = max(0, min(self.w, int(x1)));  x2 = max(0, min(self.w, int(round(x2))))
        y1 = max(0, min(self.h, int(y1)));  y2 = max(0, min(self.h, int(round(y2))))
        return x1, y1, x2, y2

    def mask_out(self, box, pad: float = 1.0):
        x1, y1, x2, y2 = self._clamp(box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
        if x2 > x1 and y2 > y1:
            self.ink[y1:y2, x1:x2] = False

    def row_extent(self, x1, x2, y1, y2, min_pixels: int = MIN_INK_PIXELS):
        """(first_y, last_y + 1) of the inked rows inside the rectangle, or
        None when no row holds at least `min_pixels` ink pixels."""
        import numpy as np
        x1, y1, x2, y2 = self._clamp(x1, y1, x2, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        rows = np.flatnonzero(self.ink[y1:y2, x1:x2].sum(axis=1) >= min_pixels)
        if rows.size == 0:
            return None
        return y1 + int(rows[0]), y1 + int(rows[-1]) + 1

    def col_extent(self, x1, x2, y1, y2, min_pixels: int = MIN_INK_PIXELS):
        """(first_x, last_x + 1) of the inked columns inside the rectangle."""
        import numpy as np
        x1, y1, x2, y2 = self._clamp(x1, y1, x2, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        cols = np.flatnonzero(self.ink[y1:y2, x1:x2].sum(axis=0) >= min_pixels)
        if cols.size == 0:
            return None
        return x1 + int(cols[0]), x1 + int(cols[-1]) + 1


# ── roles on a page ──────────────────────────────────────────────────────────

def role_shapes(data: dict, roles: dict) -> dict:
    """{"start": [(idx, bbox)], "stop": [...], "ignore": [...]} of the
    page's shapes that carry a role, each with a valid bbox."""
    out = {r: [] for r in ROLES}
    for i, sh in enumerate(data.get("shapes") or []):
        role = roles.get(sh.get("label"))
        if role not in out:
            continue
        b = _bbox(sh)
        if b is None or b[2] - b[0] <= 0 or b[3] - b[1] <= 0:
            continue
        out[role].append((i, b))
    return out


def _band_layout(items: list, page_w: float, page_h: float) -> list:
    """Cut the page into bands at every wide start / stop (as the sweep's
    reading order does). Returns [{"kind": "cols"|"wide", "y1", "y2",
    "items": [(idx, bbox, role)]}] top to bottom; a cols band may be empty
    (no role box in it) — it still spans its y-range."""
    items = sorted(items, key=lambda t: (t[1][1], t[1][0], t[0]))
    bands = []
    cur_items, cur_top = [], 0.0
    for idx, b, role in items:
        if (b[2] - b[0]) > WIDE_FRACTION * page_w:
            bands.append({"kind": "cols", "y1": cur_top, "y2": b[1], "items": cur_items})
            bands.append({"kind": "wide", "y1": b[1], "y2": b[3], "items": [(idx, b, role)]})
            cur_items, cur_top = [], b[3]
        else:
            cur_items.append((idx, b, role))
    bands.append({"kind": "cols", "y1": cur_top, "y2": page_h, "items": cur_items})
    return [bd for bd in bands if bd["y2"] > bd["y1"] or bd["kind"] == "wide"]


def _bands(items: list, page_w: float, page_h: float, mode) -> list:
    """The bands of a page under `mode`: one band spanning the page for a
    single column (mode 1, or auto without a gutter among the narrow role
    boxes), the wide-box bands of `_band_layout` for two columns."""
    if mode == 1:
        return [{"kind": "cols", "y1": 0.0, "y2": page_h, "items": list(items)}]
    if mode == "auto":
        narrow = [b for _, b, _ in items if (b[2] - b[0]) <= WIDE_FRACTION * page_w]
        if not narrow or split_x(narrow, page_w, "auto") is None:
            return [{"kind": "cols", "y1": 0.0, "y2": page_h, "items": list(items)}]
    return _band_layout(items, page_w, page_h)


def _columns(band: dict, page_w: float, mode) -> list:
    """[(x_lo, x_hi, [(idx, bbox, role)])] left to right. The x-slots are
    the page halves at the gutter (or the whole page); the fragments are
    trimmed to the ink inside the slot later."""
    boxes = [b for _, b, _ in band["items"]]
    sx = split_x(boxes, page_w, mode) if boxes else (page_w / 2.0 if mode == 2 else None)
    if sx is None:
        return [(0.0, page_w, list(band["items"]))]
    left = [t for t in band["items"] if _cx(t[1]) < sx]
    right = [t for t in band["items"] if _cx(t[1]) >= sx]
    return [(0.0, sx, left), (sx, page_w, right)]


# ── the derivation ───────────────────────────────────────────────────────────

def _trim(ink, x_lo, x_hi, y1, y2, margin, trim_top: bool, min_pixels: int):
    """The inked box inside [x_lo, x_hi) × [y1, y2): rows trimmed at the
    bottom (and at the top when trim_top), x to the inked extent of those
    rows; `margin` px added, clamped to the slot. None = no ink."""
    if y2 - y1 <= 0:
        return None
    if ink is None:
        return [x_lo, y1, x_hi, y2]
    rows = ink.row_extent(x_lo, x_hi, y1, y2, min_pixels)
    if rows is None:
        return None
    ry1 = rows[0] if trim_top else y1
    ry2 = rows[1]
    cols = ink.col_extent(x_lo, x_hi, ry1, ry2, min_pixels)
    if cols is None:
        return None
    return [max(x_lo, cols[0] - margin), max(y1, ry1 - margin) if trim_top else y1,
            min(x_hi, cols[1] + margin), min(y2, ry2 + margin)]


def _fragment(box, record_label, continuation, start, column):
    sh = {"label": record_label,
          "points": [[float(box[0]), float(box[1])], [float(box[2]), float(box[3])]],
          "group_id": None, "shape_type": "rectangle", "flags": {}}
    if continuation:
        sh["continuation"] = True
    sh[MARKER_KEY] = {"kind": MARKER_KIND,
                      "bbox": [float(v) for v in box],
                      "continuation": bool(continuation),
                      "start_label": start[2] if start else None,
                      "start_idx": start[0] if start else None,
                      "column": column}
    return sh


def derive_page(data: dict, roles: dict, record_label: str, ink=None,
                layout="auto", margin: float = DEFAULT_MARGIN,
                carry_open: bool = True, min_pixels: int = MIN_INK_PIXELS) -> dict:
    """Fragments of one page from its role shapes.

    `ink`: an InkMap of the page (ignore boxes are masked out here) or None
    (no trimming: fragments span their column slot). `carry_open`: a record
    is open at the end of the previous page (its first column's leading
    text is a continuation).

    Returns {"fragments": [shape, ...], "open_at_end": bool, "starts": n,
    "stops": n, "ignores": n, "columns": n}. Nothing is written."""
    mode = normalize_layout(layout)
    rs = role_shapes(data, roles)
    shapes = data.get("shapes") or []
    page_w = _page_width(data, [b for lst in rs.values() for _, b in lst])
    try:
        page_h = float(data.get("imageHeight") or 0)
    except (TypeError, ValueError):
        page_h = 0.0
    if page_h <= 0:
        page_h = max([b[3] for s in shapes for b in [_bbox(s)] if b] + [1.0])
    if ink is not None:
        for _, b in rs["ignore"]:
            cy = (b[1] + b[3]) / 2.0
            if cy < FURNITURE_FRACTION * page_h or cy > (1.0 - FURNITURE_FRACTION) * page_h:
                # running head / foot: the page number in the margin sits on
                # the same line outside the detected box — mask the whole line
                ink.mask_out([0.0, b[1], page_w, b[3]])
            else:
                ink.mask_out(b)
    items = [(i, b, "start") for i, b in rs["start"]] + [(i, b, "stop") for i, b in rs["stop"]]
    open_rec = bool(carry_open)
    frags = []
    n_cols = 0
    for band in _bands(items, page_w, page_h, mode):
        if band["kind"] == "wide":
            idx, b, role = band["items"][0]
            if role == "stop":
                open_rec = False
                continue
            box = _trim(ink, 0.0, page_w, b[1], b[3], margin, False, min_pixels)
            if box is not None:
                frags.append(_fragment(box, record_label, False,
                                       (idx, b, shapes[idx].get("label")), 0))
            open_rec = True
            continue
        for k, (x_lo, x_hi, members) in enumerate(_columns(band, page_w, mode)):
            n_cols += 1
            members = sorted(members, key=lambda t: (t[1][1], t[1][0], t[0]))
            y = band["y1"]
            # leading continuation: the open record runs on at the column top
            first_y = members[0][1][1] if members else band["y2"]
            if open_rec:
                box = _trim(ink, x_lo, x_hi, y, first_y, margin, True, min_pixels)
                if box is not None:
                    frags.append(_fragment(box, record_label, True, None, k))
            for j, (idx, b, role) in enumerate(members):
                if role == "stop":
                    open_rec = False
                    continue
                nxt = members[j + 1][1][1] if j + 1 < len(members) else band["y2"]
                box = _trim(ink, x_lo, x_hi, b[1], max(nxt, b[3]), margin, False, min_pixels)
                if box is not None:
                    frags.append(_fragment(box, record_label, False,
                                           (idx, b, shapes[idx].get("label")), k))
                open_rec = True
    return {"fragments": frags, "open_at_end": open_rec,
            "starts": len(rs["start"]), "stops": len(rs["stop"]),
            "ignores": len(rs["ignore"]), "columns": n_cols}


def page_open_at_end(data: dict, roles: dict, carry_open: bool = True) -> bool:
    """The carry state after a page WITHOUT reading its image: what
    `derive_page` would return as `open_at_end` (only the roles matter)."""
    return derive_page(data, roles, "_", ink=None, carry_open=carry_open)["open_at_end"]


# ── replacement rule ─────────────────────────────────────────────────────────

def _bbox_eq(a, b, tol=BBOX_TOL):
    try:
        return len(a) == len(b) == 4 and all(abs(float(x) - float(y)) <= tol
                                             for x, y in zip(a, b))
    except (TypeError, ValueError):
        return False


def has_content(shape: dict) -> bool:
    """Any content layer or flag a person (or a model run) put on the box."""
    for k in CONTENT_KEYS:
        v = shape.get(k)
        if v not in (None, "", {}, []):
            return True
    fl = shape.get("flags") or {}
    return bool(fl.get("blank") or fl.get("cross_page"))


def is_derived(shape: dict) -> bool:
    m = shape.get(MARKER_KEY)
    return isinstance(m, dict) and m.get("kind") == MARKER_KIND


def is_replaceable(shape: dict) -> bool:
    """A derived fragment a re-run may replace: marker present, box still
    where the derivation put it, continuation mark unchanged, not locked,
    no content. See the module docstring."""
    if not is_derived(shape):
        return False
    m = shape[MARKER_KEY]
    b = _bbox(shape)
    if b is None or not _bbox_eq(b, m.get("bbox") or []):
        return False
    if bool(shape.get("continuation")) != bool(m.get("continuation")):
        return False
    if shape.get("group_locked"):
        return False
    return not has_content(shape)


def _overlap_frac(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / smaller if smaller > 0 else 0.0


def _same_fragment(old: dict, new: dict) -> bool:
    return (_bbox_eq(_bbox(old) or [], _bbox(new) or [])
            and bool(old.get("continuation")) == bool(new.get("continuation")))


def merge_fragments(data: dict, record_label: str, fragments: list) -> dict:
    """Apply derived fragments to a page's shapes in place.

    Replaceable derived record-label shapes are dropped and the new
    fragments appended — except that a new fragment identical to an old
    replaceable one keeps the old shape (its group_id survives, nothing
    changes). Protected record-label shapes (hand-drawn, moved, annotated,
    locked) stay; a new fragment overlapping one by ≥ OVERLAP_SUPPRESS of
    the smaller area is suppressed. Returns counts + `changed`."""
    shapes = data.get("shapes") or []
    protected, replaceable = [], []
    for i, sh in enumerate(shapes):
        if sh.get("label") != record_label or _bbox(sh) is None:
            continue
        (replaceable if is_replaceable(sh) else protected).append(i)
    prot_boxes = [_bbox(shapes[i]) for i in protected]
    kept_old, new_shapes, suppressed = set(), [], 0
    for fr in fragments:
        fb = _bbox(fr)
        if any(_overlap_frac(fb, pb) >= OVERLAP_SUPPRESS for pb in prot_boxes):
            suppressed += 1
            continue
        same = next((i for i in replaceable if i not in kept_old
                     and _same_fragment(shapes[i], fr)), None)
        if same is not None:
            kept_old.add(same)
            shapes[same][MARKER_KEY] = fr[MARKER_KEY]   # refresh start_idx etc.
            continue
        new_shapes.append(fr)
    removed = [i for i in replaceable if i not in kept_old]
    if not removed and not new_shapes:
        return {"derived": len(fragments), "written": 0, "replaced": 0, "unchanged": len(kept_old),
                "protected": len(protected), "suppressed": suppressed, "changed": False}
    data["shapes"] = [sh for i, sh in enumerate(shapes) if i not in set(removed)] + new_shapes
    return {"derived": len(fragments), "written": len(new_shapes), "replaced": len(removed),
            "unchanged": len(kept_old), "protected": len(protected),
            "suppressed": suppressed, "changed": True}


# ── the batch over a project folder ──────────────────────────────────────────

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


def _find_image(ann: Path, data: dict, stem: str):
    cand = ann / (data.get("imagePath") or "")
    if data.get("imagePath") and cand.exists():
        return cand
    for ext in IMAGE_EXTS:
        p = ann / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def _status(data: dict) -> str:
    return (data.get("flags") or {}).get("status") or "predicted"


def derive_project(pages: list, ann: Path, roles: dict, record_label: str, write_json,
                   statuses=None, pattern=None, layout="auto", margin: float = DEFAULT_MARGIN,
                   threshold: int = DEFAULT_THRESHOLD, dry_run: bool = False,
                   no_ink_untrimmed: bool = False) -> dict:
    """Derive fragments on the selected pages of a project.

    `pages` = [(stem, data)] — every non-skip page in natural order (the
    carry state is threaded through ALL of them, so a page outside the
    selection still tells whether a record is open after it); only pages
    whose status is in `statuses` (None = all) and whose stem matches
    `pattern` (None = all) are derived and written. A page without an image
    is derived untrimmed when `no_ink_untrimmed`, else skipped."""
    import re
    rx = re.compile(pattern) if pattern else None
    statuses = set(statuses) if statuses else None
    st = {"dry_run": dry_run, "record_label": record_label, "roles": dict(roles),
          "pages_scanned": 0, "pages_selected": 0, "pages_written": 0,
          "fragments": 0, "continuations": 0, "written": 0, "replaced": 0,
          "unchanged": 0, "protected": 0, "suppressed": 0, "pages_without_image": 0,
          "empty_pages": [], "pages": []}
    carry = True
    for stem, data in pages:
        st["pages_scanned"] += 1
        selected = ((statuses is None or _status(data) in statuses)
                    and (rx is None or rx.search(stem)))
        if not selected:
            carry = page_open_at_end(data, roles, carry)
            continue
        st["pages_selected"] += 1
        img = _find_image(ann, data, stem)
        ink = None
        if img is not None:
            try:
                ink = InkMap.from_image(img, threshold)
            except Exception:
                ink = None
        if ink is None and not no_ink_untrimmed:
            st["pages_without_image"] += 1
            res = derive_page(data, roles, record_label, ink=None, layout=layout,
                              margin=margin, carry_open=carry)
            carry = res["open_at_end"]
            st["pages"].append({"stem": stem, "status": _status(data), "skipped": "no image",
                                "starts": res["starts"], "stops": res["stops"]})
            if res["starts"] == 0 and res["stops"] == 0:
                st["empty_pages"].append(stem)
            continue
        res = derive_page(data, roles, record_label, ink=ink, layout=layout,
                          margin=margin, carry_open=carry)
        carry = res["open_at_end"]
        if res["starts"] == 0 and res["stops"] == 0:
            st["empty_pages"].append(stem)
        m = merge_fragments(data, record_label, res["fragments"])
        n_cont = sum(1 for f in res["fragments"] if f.get("continuation"))
        row = {"stem": stem, "status": _status(data), "starts": res["starts"],
               "stops": res["stops"], "ignores": res["ignores"], "columns": res["columns"],
               "fragments": len(res["fragments"]), "continuations": n_cont,
               "open_at_end": res["open_at_end"], **{k: m[k] for k in
               ("written", "replaced", "unchanged", "protected", "suppressed", "changed")}}
        st["pages"].append(row)
        st["fragments"] += len(res["fragments"]); st["continuations"] += n_cont
        for k in ("written", "replaced", "unchanged", "protected", "suppressed"):
            st[k] += m[k]
        if m["changed"]:
            if not dry_run:
                write_json(ann / f"{stem}.json", data)
            st["pages_written"] += 1
    return st
