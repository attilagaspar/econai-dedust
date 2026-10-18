"""
Record chaining — fragments of one record across column / page breaks
(plan doc knowledge_base/09_hierarchical_layout_compass.md §H5, applied to a
single chosen parent label; label-agnostic).

A *record* is the chain of boxes of the project's `record_label` that belong
to one entry (one firm in a directory, one article, one entry in a register).
An entry that runs across a column or page break is several boxes, one per
fragment. Per-shape fields:

    continuation: true   this box continues the previous box of the same
                         label in reading order (same record)
    group_id: <int>      the record id — LabelMe-native field, numbered
                         through the whole volume by the sweep
    group_locked: true   hand-assigned id; the sweep keeps it and continues
                         from it

Project config: `record_label` (which label records are built from) and
`layout_columns` ("auto" | 1 | 2; auto = x-histogram valley). A box wider
than WIDE_FRACTION of the page resets the column flow (full-width elements
split the page into bands that are read one after the other).

Everything here is pure: pages in, decisions out. Writing is the caller's
job (the server's atomic writer). The derived registry
`intermediate/records.json` is rebuilt from the shapes and is never the
source of truth.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

WIDE_FRACTION = 0.70          # a box this wide (of the page) resets the column flow
VALLEY_MIN_FRACTION = 0.03    # auto mode: an empty x-run at least this wide = a gutter
VALLEY_SEARCH = (0.25, 0.75)  # auto mode: the gutter must lie in the page's middle half
LAYOUT_MODES = ("auto", 1, 2)


# ── config ───────────────────────────────────────────────────────────────────

def record_label_for(cfg: dict) -> str | None:
    v = (cfg or {}).get("record_label")
    return v.strip() if isinstance(v, str) and v.strip() else None


def layout_columns_for(cfg: dict):
    """'auto' (default) | 1 | 2 from config.layout_columns."""
    return normalize_layout((cfg or {}).get("layout_columns"))


def normalize_layout(v):
    if v is None or v == "" or v == "auto":
        return "auto"
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"layout_columns must be 'auto', 1 or 2 (got {v!r})")
    if n not in (1, 2):
        raise ValueError(f"layout_columns must be 'auto', 1 or 2 (got {v!r})")
    return n


# ── geometry helpers ─────────────────────────────────────────────────────────

def _bbox(shape: dict):
    pts = shape.get("points") or []
    if len(pts) < 2:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _page_width(data: dict, boxes: list) -> float:
    w = data.get("imageWidth")
    try:
        w = float(w)
    except (TypeError, ValueError):
        w = 0.0
    if w <= 0 and boxes:
        w = max(b[2] for b in boxes)
    return w or 1.0


# ── column assignment ────────────────────────────────────────────────────────

def split_x(boxes: list, page_w: float, mode="auto") -> float | None:
    """The x position that separates the left column from the right one,
    or None for a single column.

    mode 1 → None. mode 2 → the widest empty x-run in the middle half of
    the page (the gutter), else the page centre. mode 'auto' → the gutter
    when the boxes leave one at least VALLEY_MIN_FRACTION wide, with boxes
    on both sides; else None (single column)."""
    mode = normalize_layout(mode)
    if mode == 1 or not boxes:
        return None
    lo, hi = VALLEY_SEARCH[0] * page_w, VALLEY_SEARCH[1] * page_w
    # Empty runs of x: merge the boxes' x-intervals, look at the gaps.
    ivs = sorted((b[0], b[2]) for b in boxes)
    merged = []
    for a, b in ivs:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    best, best_w = None, 0.0
    for (a1, b1), (a2, b2) in zip(merged, merged[1:]):
        gap_lo, gap_hi = max(b1, lo), min(a2, hi)
        if gap_hi - gap_lo > best_w:
            best, best_w = (gap_lo + gap_hi) / 2.0, gap_hi - gap_lo
    if best is not None and best_w >= VALLEY_MIN_FRACTION * page_w:
        left = any(_cx(b) < best for b in boxes)
        right = any(_cx(b) >= best for b in boxes)
        if left and right:
            return best
    if mode == 2:
        return page_w / 2.0
    return None


def _cx(b):
    return (b[0] + b[2]) / 2.0


def reading_order(shapes: list, record_label: str, page_w: float,
                  mode="auto") -> list:
    """Indices of the record-label shapes on a page in reading order.

    The page is cut into horizontal bands at every wide box (width >
    WIDE_FRACTION of the page); within a band the boxes are read column by
    column (left column top to bottom, then right column), the wide box
    itself is read between the bands. Columns are decided per band by
    split_x. Ties in y are broken by x, then by index, so the order is a
    deterministic function of the geometry."""
    items = []
    for i, sh in enumerate(shapes):
        if sh.get("label") != record_label:
            continue
        b = _bbox(sh)
        if b is None:
            continue
        items.append((i, b))
    if not items:
        return []
    wide = {i for i, b in items if (b[2] - b[0]) > WIDE_FRACTION * page_w}
    # bands: split at the vertical centre of each wide box
    items.sort(key=lambda t: (t[1][1], t[1][0], t[0]))
    bands, cur = [], []
    for i, b in items:
        if i in wide:
            if cur:
                bands.append(("cols", cur))
                cur = []
            bands.append(("wide", [(i, b)]))
        else:
            cur.append((i, b))
    if cur:
        bands.append(("cols", cur))
    order = []
    for kind, members in bands:
        if kind == "wide":
            order.extend(i for i, _ in members)
            continue
        sx = split_x([b for _, b in members], page_w, mode)
        if sx is None:
            cols = [members]
        else:
            cols = [[t for t in members if _cx(t[1]) < sx],
                    [t for t in members if _cx(t[1]) >= sx]]
        for col in cols:
            col.sort(key=lambda t: (t[1][1], t[1][0], t[0]))
            order.extend(i for i, _ in col)
    return order


# ── the sweep ────────────────────────────────────────────────────────────────

def sweep(pages: list, record_label: str, mode="auto",
          start_id: int = 1, carry_id=None) -> dict:
    """Assign record ids over pages in order.

    `pages` = [(stem, data), ...] in natural page order; skip pages must be
    filtered out by the caller. A box starts a new record unless
    `continuation` is set, in which case it joins the record of the previous
    box of the label in reading order — across page boundaries. A box with
    `group_locked` keeps its own `group_id` (and later continuations join
    it); the sweep's next new id is always max(id seen so far) + 1, so
    ids stay unique and a locked id is "continued from".

    `start_id` / `carry_id` seed a forward resweep: the next new record id
    and the id of the open record at the start (what a leading continuation
    joins).

    Returns {"assignments": {stem: {idx: gid}}, "changed": {stem: n},
    "records": {gid: [...fragments...]}, "orphans": [fragment, ...],
    "next_id": int, "n_boxes": int}. A fragment is
    {"stem", "idx", "bbox", "continuation", "locked"}. Nothing is written."""
    next_id = int(start_id)
    open_id = carry_id
    assignments: dict = {}
    changed: dict = {}
    records: dict = {}
    orphans: list = []
    n_boxes = 0
    seen_max = next_id - 1
    for stem, data in pages:
        shapes = data.get("shapes") or []
        boxes = [_bbox(sh) for sh in shapes
                 if sh.get("label") == record_label and _bbox(sh) is not None]
        page_w = _page_width(data, boxes)
        order = reading_order(shapes, record_label, page_w, mode)
        page_assign = {}
        n_changed = 0
        for idx in order:
            sh = shapes[idx]
            n_boxes += 1
            cont = bool(sh.get("continuation"))
            locked = bool(sh.get("group_locked")) and _int_or_none(sh.get("group_id")) is not None
            if locked:
                gid = int(sh["group_id"])
            elif cont and open_id is not None:
                gid = open_id
            else:
                gid = seen_max + 1
            frag = {"stem": stem, "idx": idx, "bbox": list(_bbox(sh)),
                    "continuation": cont, "locked": locked}
            if gid not in records:
                records[gid] = []
                if cont:
                    orphans.append(frag)
            records[gid].append(frag)
            seen_max = max(seen_max, gid)
            open_id = gid
            page_assign[idx] = gid
            if _int_or_none(sh.get("group_id")) != gid:
                n_changed += 1
        assignments[stem] = page_assign
        if n_changed:
            changed[stem] = n_changed
    return {"assignments": assignments, "changed": changed, "records": records,
            "orphans": orphans, "next_id": seen_max + 1, "n_boxes": n_boxes}


def _int_or_none(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def apply_assignments(data: dict, page_assign: dict) -> bool:
    """Write the sweep's ids into a page's shapes in place; True when any
    shape changed."""
    changed = False
    for idx, gid in page_assign.items():
        sh = data["shapes"][idx]
        if _int_or_none(sh.get("group_id")) != gid:
            sh["group_id"] = gid
            changed = True
    return changed


# ── forward resweep seed ─────────────────────────────────────────────────────

def seed_from_pages(pages: list, record_label: str, mode="auto") -> tuple:
    """(start_id, carry_id) for a sweep that begins AFTER these pages: the
    next unused id and the id of the last box of the label in reading order
    (what a leading continuation on the next page joins). Pages here are
    not touched: their stored ids are the truth."""
    seen_max = 0
    carry = None
    for stem, data in pages:
        shapes = data.get("shapes") or []
        boxes = [_bbox(sh) for sh in shapes
                 if sh.get("label") == record_label and _bbox(sh) is not None]
        page_w = _page_width(data, boxes)
        for idx in reading_order(shapes, record_label, page_w, mode):
            gid = _int_or_none(shapes[idx].get("group_id"))
            if gid is not None:
                seen_max = max(seen_max, gid)
                carry = gid
    return seen_max + 1, carry


# ── registry + browser listing (from stored ids) ─────────────────────────────

def best_text(shape: dict, limit: int = 80) -> str:
    """Human > LLM > OCR > PDF, first line, trimmed — a record's name."""
    cands = [
        (shape.get("human_output") or {}).get("human_corrected_text"),
        (shape.get("openai_output") or {}).get("response"),
        (shape.get("tesseract_output") or {}).get("ocr_text"),
        (shape.get("easyocr_output") or {}).get("ocr_text"),
        shape.get("pdf_text"),
    ]
    rows = (shape.get("row_struct") or {}).get("rows") or []
    if rows:
        r0 = rows[0]
        cands = [r0.get("human"), r0.get("llm"), r0.get("ocr"), r0.get("pdf")] + cands
    for c in cands:
        if isinstance(c, str) and c.strip():
            line = c.strip().splitlines()[0].strip()
            return line[:limit]
    return ""


def collect_records(pages: list, record_label: str, mode="auto",
                    stem_order: dict | None = None) -> dict:
    """Records as STORED on the shapes (group_id), in reading order —
    what the browser shows and what the registry holds. Boxes without a
    group_id are listed under "unswept". Returns {"records": {gid:
    {...}}, "unswept": [fragments], "n_boxes": int}."""
    stem_order = stem_order or {s: i for i, (s, _) in enumerate(pages)}
    records: dict = {}
    unswept: list = []
    n_boxes = 0
    for stem, data in pages:
        shapes = data.get("shapes") or []
        boxes = [_bbox(sh) for sh in shapes
                 if sh.get("label") == record_label and _bbox(sh) is not None]
        page_w = _page_width(data, boxes)
        for idx in reading_order(shapes, record_label, page_w, mode):
            sh = shapes[idx]
            n_boxes += 1
            frag = {"stem": stem, "idx": idx, "bbox": list(_bbox(sh)),
                    "continuation": bool(sh.get("continuation")),
                    "locked": bool(sh.get("group_locked")),
                    "text": best_text(sh)}
            gid = _int_or_none(sh.get("group_id"))
            if gid is None:
                unswept.append(frag)
                continue
            rec = records.setdefault(gid, {"fragments": []})
            rec["fragments"].append(frag)
    for gid, rec in records.items():
        frags = rec["fragments"]
        stems = [f["stem"] for f in frags]
        pages_set = {s: stem_order.get(s, 0) for s in stems}
        first = min(pages_set, key=pages_set.get)
        last = max(pages_set, key=pages_set.get)
        rec["first_stem"] = frags[0]["stem"]
        rec["page_span"] = [first, last]
        rec["n_pages"] = len(pages_set)
        rec["orphan"] = bool(frags[0]["continuation"])
        rec["name"] = next((f["text"] for f in frags if f["text"]), "")
    return {"records": records, "unswept": unswept, "n_boxes": n_boxes}


def build_registry(collected: dict, record_label: str, mode) -> dict:
    """The derived registry document written to intermediate/records.json.
    Rebuildable from the shapes; never the source of truth."""
    recs = {}
    for gid in sorted(collected["records"]):
        r = collected["records"][gid]
        recs[str(gid)] = {
            "fragments": [{"stem": f["stem"], "idx": f["idx"], "bbox": f["bbox"]}
                          for f in r["fragments"]],
            "page_span": r["page_span"],
            "first_stem": r["first_stem"],
            "n_pages": r["n_pages"],
            "orphan": r["orphan"],
            "name": r["name"],
        }
    return {
        "meta": {"record_label": record_label, "layout_columns": mode,
                 "generated": datetime.now(timezone.utc).isoformat(),
                 "n_records": len(recs), "n_unswept": len(collected["unswept"]),
                 "derived": "rebuilt by the sweep from the page JSONs; "
                            "the shapes' group_id is the source of truth"},
        "records": recs,
    }


# ── page-order sanity check ──────────────────────────────────────────────────

_TRAIL_NUM = re.compile(r"^(.*?)(\d+)$")


def page_order_check(stems: list) -> dict:
    """Cheap sanity check that the natural stem sort is the physical order:
    every stem should be <prefix><number> with ONE prefix per volume and a
    contiguous, duplicate-free number sequence. Reports what breaks that so
    the human can look before a sweep numbers records across it."""
    prefixes: dict = {}
    non_numeric = []
    for s in stems:
        m = _TRAIL_NUM.match(s)
        if not m:
            non_numeric.append(s)
            continue
        prefixes.setdefault(m.group(1), []).append(int(m.group(2)))
    gaps, dups = [], []
    for pre, nums in prefixes.items():
        seen = set()
        for n in nums:
            if n in seen:
                dups.append(f"{pre}{n}")
            seen.add(n)
        if seen:
            lo, hi = min(seen), max(seen)
            missing = [n for n in range(lo, hi + 1) if n not in seen]
            if missing:
                gaps.append({"prefix": pre, "missing": missing[:20],
                             "n_missing": len(missing)})
    ok = (len(prefixes) <= 1 and not non_numeric and not gaps and not dups)
    return {"ok": ok, "n_pages": len(stems),
            "prefixes": sorted(prefixes), "non_numeric": non_numeric[:20],
            "gaps": gaps, "duplicates": dups[:20],
            "note": ("stems are <prefix><number>, one prefix, contiguous — "
                     "natural sort is the physical order" if ok else
                     "look at the stems before trusting the page order: "
                     "several prefixes, gaps or non-numeric stems interleave")}
