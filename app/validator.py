"""
EconAI — Statistical Table Validator / Cleaner
===============================================
Downstream of the OCR/LLM editor.  Imports the extracted table into a
project-local SQLite database, lets the user name columns, define algebraic
constraints between columns, visualise violations, apply corrections, and
keeps a full reproducible audit log.

Mount in server.py:
    from app.validator import router as validator_router
    app.include_router(validator_router)
"""

import csv
import io
import json
import re
import sqlite3
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

# ── Router ────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/validate")

_batch_stop: dict = {}   # job_id → bool

PROJECTS_DIR = Path(__file__).parent.parent / "projects"


def _resolve_folder(folder: str) -> Path:
    p = Path(folder)
    if p.is_absolute() and p.exists():
        return p
    candidate = PROJECTS_DIR / folder
    if candidate.exists():
        return candidate
    raise HTTPException(status_code=404, detail=f"Folder not found: {folder}")


def _db_path(folder: str) -> Path:
    return _resolve_folder(folder) / "validated.db"


# ── Schema ────────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS columns (
    col_id     INTEGER PRIMARY KEY,
    position   INTEGER,
    name       TEXT,
    variable   TEXT,
    role       TEXT DEFAULT 'data',
    dtype      TEXT DEFAULT 'int',
    page_stem  TEXT DEFAULT '',
    page_index INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rows (
    row_id       INTEGER PRIMARY KEY,
    position     REAL,
    page_stem    TEXT,
    page_row_idx INTEGER,
    is_deleted   INTEGER DEFAULT 0,
    group_stems  TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS cells (
    cell_id        INTEGER PRIMARY KEY,
    row_id         INTEGER REFERENCES rows(row_id),
    col_id         INTEGER REFERENCES columns(col_id),
    value          TEXT,
    original_value TEXT,
    source         TEXT DEFAULT 'ocr',
    UNIQUE(row_id, col_id)
);
CREATE TABLE IF NOT EXISTS constraints (
    constraint_id INTEGER PRIMARY KEY,
    label         TEXT,
    lhs           TEXT,
    rhs           TEXT,
    tolerance     REAL DEFAULT 0,
    severity      TEXT DEFAULT 'error'
);
CREATE TABLE IF NOT EXISTS audit_log (
    log_id        INTEGER PRIMARY KEY,
    ts            TEXT,
    action        TEXT,
    row_id        INTEGER,
    col_id        INTEGER,
    old_value     TEXT,
    new_value     TEXT,
    constraint_id INTEGER,
    note          TEXT
);
CREATE TABLE IF NOT EXISTS raw_line_counts (
    page_stem  TEXT,
    row_idx    INTEGER,
    col_idx    INTEGER,
    page_index INTEGER DEFAULT 0,
    line_count INTEGER,
    PRIMARY KEY (page_stem, row_idx, col_idx)
);
"""


@contextmanager
def _db(folder: str):
    path = _db_path(folder)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # Migrations for new columns (silently ignored if already present)
    for stmt in [
        "ALTER TABLE columns ADD COLUMN page_stem  TEXT    DEFAULT ''",
        "ALTER TABLE columns ADD COLUMN page_index INTEGER DEFAULT 0",
        "ALTER TABLE rows    ADD COLUMN group_stems     TEXT    DEFAULT '[]'",
        "ALTER TABLE rows    ADD COLUMN page_row_idx    INTEGER",
        "ALTER TABLE rows        ADD COLUMN hierarchy_level INTEGER",
        "ALTER TABLE constraints ADD COLUMN operator TEXT DEFAULT '='",
    ]:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _log(conn, action, *, row_id=None, col_id=None,
         old_value=None, new_value=None, constraint_id=None, note=None):
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO audit_log"
        "(ts,action,row_id,col_id,old_value,new_value,constraint_id,note)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (ts, action, row_id, col_id, old_value, new_value, constraint_id, note),
    )


# ── Number parsing ────────────────────────────────────────────────────────────
_DASH_RE = re.compile(r"^[-–—−·*x×.,:;_]+$")


def parse_number(raw):
    """Parse an OCR value to int / float / None.

    Rules:
    - Dash-like characters (-, –, —, −, ·) → 0
    - Dots / commas as thousands separators are stripped before int parse
    - Non-numeric content (row labels etc.) → None
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if _DASH_RE.match(s):
        return 0
    # Strip thousands separators then try int
    no_sep = re.sub(r"[.,  ]", "", s)
    try:
        return int(no_sep)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return None


# ── Spatial cell extraction (mirrors server.py shapes_to_cells) ───────────────

def _get_text(shape, layer):
    human = (shape.get("human_output") or {}).get("human_corrected_text") or ""
    ocr   = (
        (shape.get("tesseract_output") or {}).get("ocr_text") or
        (shape.get("easyocr_output")   or {}).get("ocr_text") or ""
    )
    llm   = (shape.get("openai_output") or {}).get("response") or ""
    human, ocr, llm = human.strip(), ocr.strip(), llm.strip()
    if layer == "human":    return human, "human"
    if layer == "ocr":      return ocr,   "ocr"
    if layer == "llm":      return llm,   "llm"
    if layer == "best_ocr":
        v = human or ocr or llm
        return v, "human" if human else ("ocr" if ocr else "llm")
    # best_llm (default)
    v = human or llm or ocr
    return v, "human" if human else ("llm" if llm else "ocr")


def _shapes_to_cells(shapes, layer="best_llm", selected_types=None,
                     ref_col_centers=None):
    """Return (cells, col_centers).

    col_centers are the column x-centroids used for col_idx assignment.
    Pass ref_col_centers (from a previous call on the same page slot) to pin
    the column layout — this prevents a small bounding-box edit on one cell
    from shifting the clustering and blanking out adjacent columns on re-import.
    """
    raw = []
    for sh in shapes:
        if selected_types and sh.get("label", "") not in selected_types:
            continue
        text, source = _get_text(sh, layer)
        if not text:
            continue
        pts = sh.get("points", [])
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        raw.append(dict(
            text=text, source=source,
            cx=(x1 + x2) / 2, cy=(y1 + y2) / 2,
            top_y=y1, bot_y=y2,
            h=max(1, y2 - y1), w=max(1, x2 - x1),
            label=sh.get("label", "?"),
        ))
    if not raw:
        return [], ref_col_centers or []

    # Row clustering: mirror JS _latticeAssignCoords (band-based, tol=10px)
    ROW_TOL = 10
    raw.sort(key=lambda c: c["top_y"])
    row_groups: list = []
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

    # Column clustering: use reference centers if supplied, otherwise compute fresh
    if ref_col_centers:
        col_centers = ref_col_centers
    else:
        med_w    = sorted(c["w"] for c in raw)[len(raw) // 2]
        thresh_x = max(3, med_w * 0.45)
        all_cx   = sorted({c["cx"] for c in raw})
        col_centers = []
        if all_cx:
            grp = [all_cx[0]]
            for cx in all_cx[1:]:
                if cx - grp[-1] <= thresh_x:
                    grp.append(cx)
                else:
                    col_centers.append(sum(grp) / len(grp))
                    grp = [cx]
            col_centers.append(sum(grp) / len(grp))

    for c in raw:
        c["col_idx"] = min(
            range(len(col_centers)),
            key=lambda i: abs(col_centers[i] - c["cx"]),
        )

    # Collision resolution: taller wins; loser rescued one row above
    winner: dict = {}
    for c in raw:
        k = (c["row_idx"], c["col_idx"])
        if k in winner:
            prev = winner[k]
            if c["h"] > prev["h"]:
                winner[k] = c
                loser = dict(prev)
            else:
                loser = dict(c)
            rk = (loser["row_idx"] - 1, loser["col_idx"])
            if rk not in winner:
                loser["row_idx"] -= 1
                winner[rk] = loser
        else:
            winner[k] = c

    if winner:
        min_row = min(c["row_idx"] for c in winner.values())
        if min_row < 0:
            for c in winner.values():
                c["row_idx"] -= min_row

    # Return cells with pre-split lines field (mirrors server.py shapes_to_cells)
    return [
        dict(
            row_idx=c["row_idx"],
            col_idx=c["col_idx"],
            lines=_text_lines(c["text"]),
            source=c.get("source", "ocr"),
            w=c["w"],
        )
        for c in winner.values()
    ], col_centers


def _lattice_start_row(cells):
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


def _page_height(cells, extra=0):
    """Total DB rows a page occupies after line expansion (mirrors page_excel_height)."""
    if not cells:
        return extra
    rmap: dict = defaultdict(list)
    for c in cells:
        rmap[c["row_idx"]].append(c)
    return extra + sum(max(len(c["lines"]) for c in row)
                       for row in rmap.values())


def _excel_row_for_lattice(cells):
    if not cells:
        return 1
    target = _lattice_start_row(cells)
    rmap: dict = defaultdict(list)
    for c in cells:
        rmap[c["row_idx"]].append(c)
    row = 1
    for ridx in sorted(rmap):
        if ridx >= target:
            return row
        row += max(len(c["lines"]) for c in rmap[ridx])
    return row


def _max_col_of(cells):
    return max((c["col_idx"] for c in cells), default=-1)


def _load_shapes(jf: Path):
    try:
        return json.loads(jf.read_text(encoding="utf-8")).get("shapes", [])
    except Exception:
        return []


def _find_image(folder: Path, stem: str) -> Optional[Path]:
    """Look for stem.{jpg,jpeg,png,tif,tiff} inside folder/annotations/."""
    ann = folder / "annotations"
    for ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        p = ann / (stem + ext)
        if p.exists():
            return p
    return None


@router.get("/image")
def api_image(folder: str = Query(...), stem: str = Query(...)):
    """Serve a page image for the Data Lab page viewer."""
    d   = _resolve_folder(folder)
    img = _find_image(d, stem)
    if img is None:
        raise HTTPException(status_code=404, detail=f"Image not found: {stem}")
    suffix = img.suffix.lower()
    media  = "image/jpeg" if suffix in (".jpg", ".jpeg") else \
             "image/png"  if suffix == ".png" else "image/tiff"
    return FileResponse(str(img), media_type=media)


# ── Text helpers ─────────────────────────────────────────────────────────────

def _text_lines(text: str) -> list:
    """Split cell text into lines, stripping trailing blanks.
    Mirrors text_to_lines() in the Excel export."""
    lines = [l.strip() for l in str(text).split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines or [""]


# ── Import ────────────────────────────────────────────────────────────────────

class ImportRequest(BaseModel):
    folder: str
    pages_per_row: int = 1
    layer: str = "best_llm"
    types: str = ""


@router.post("/import")
def api_import(req: ImportRequest):
    """
    Import all page JSONs from the project's annotations/ folder into
    validated.db.  pages_per_row consecutive pages are placed side-by-side
    (same logic as the dual-page Excel export) to form one wide table row set.
    Re-importing clears rows/cells but preserves column names and constraints.
    """
    d = _resolve_folder(req.folder)
    ann_dir = d / "annotations"
    if not ann_dir.exists():
        raise HTTPException(status_code=404, detail="annotations/ folder not found")

    selected_types = (
        {t.strip() for t in req.types.split(",") if t.strip()}
        if req.types else set()
    )

    def natural_key(p):
        return [int(c) if c.isdigit() else c.lower()
                for c in re.split(r"(\d+)", str(p))]

    jfiles = sorted(ann_dir.glob("*.json"), key=lambda f: natural_key(f.stem))
    if not jfiles:
        raise HTTPException(status_code=404, detail="No annotation JSON files found")

    ppr    = max(1, req.pages_per_row)
    groups = [jfiles[i:i + ppr] for i in range(0, len(jfiles), ppr)]

    # ── Determine column layout from first complete group ─────────────────────
    # Also capture per-slot reference column centers so subsequent groups use
    # the same col_idx mapping (prevents a bounding-box edit on one page from
    # shifting the clustering and blanking adjacent columns on re-import).
    first_group_cells  = []
    ref_col_centers    = []   # one list of x-centers per page slot
    for jf in groups[0]:
        cells, centers = _shapes_to_cells(_load_shapes(jf), req.layer, selected_types)
        first_group_cells.append(cells)
        ref_col_centers.append(centers)
    # Pad to ppr if first group is short
    while len(first_group_cells) < ppr:
        first_group_cells.append([])
        ref_col_centers.append([])

    page_col_counts  = [_max_col_of(c) + 1 if c else 0 for c in first_group_cells]
    page_col_offsets = []
    offset = 0
    for nc in page_col_counts:
        page_col_offsets.append(offset)
        offset += nc
    total_cols = offset

    with _db(req.folder) as conn:
        # Preserve column metadata if col count matches, else reset
        existing_cols = conn.execute("SELECT COUNT(*) FROM columns").fetchone()[0]
        if existing_cols != total_cols:
            conn.execute("DELETE FROM columns")
            for pi, jf in enumerate(groups[0]):
                col_off = page_col_offsets[pi]
                n_cols  = page_col_counts[pi]
                for col_id in range(col_off, col_off + n_cols):
                    conn.execute(
                        "INSERT INTO columns"
                        "(col_id,position,name,variable,role,dtype,page_stem,page_index)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (col_id, col_id, f"col_{col_id}", f"col_{col_id}",
                         "data", "int", jf.stem, pi),
                    )
        else:
            # Update page_stem / page_index even when preserving names/variables/roles
            for pi, jf in enumerate(groups[0]):
                col_off = page_col_offsets[pi]
                n_cols  = page_col_counts[pi]
                for col_id in range(col_off, col_off + n_cols):
                    conn.execute(
                        "UPDATE columns SET page_stem=?, page_index=? WHERE col_id=?",
                        (jf.stem, pi, col_id))

        # Always reset rows, cells and raw line counts
        conn.execute("DELETE FROM cells")
        conn.execute("DELETE FROM rows")
        conn.execute("DELETE FROM raw_line_counts")

        _log(conn, "import",
             note=f"pages_per_row={ppr}, layer={req.layer}, "
                  f"total_cols={total_cols}, groups={len(groups)}, "
                  f"pages={len(jfiles)}")

        row_counter    = 0
        global_pos     = 0.0

        for group in groups:
            # ── Load cells for each page slot (cells now carry .lines list) ───
            # Pass reference col_centers per slot so col_idx is pinned to the
            # layout from the first group — stable across re-imports.
            group_cells = []
            for pi, jf in enumerate(group):
                ref = ref_col_centers[pi] if pi < len(ref_col_centers) else None
                cells, _ = _shapes_to_cells(
                    _load_shapes(jf), req.layer, selected_types,
                    ref_col_centers=ref)
                group_cells.append((jf.stem, cells))
            while len(group_cells) < ppr:
                group_cells.append(("", []))

            # ── Lattice-alignment padding (mirrors dual-page Excel export) ────
            latt_rows = [_excel_row_for_lattice(c) for _, c in group_cells]
            max_latt  = max(latt_rows)
            pads      = [max_latt - lr for lr in latt_rows]

            # ── Total DB rows this group needs (mirrors page_excel_height) ────
            group_height = max(
                _page_height(cells, extra=pads[pi])
                for pi, (_, cells) in enumerate(group_cells)
            )

            # ── Create all DB rows for this group upfront ─────────────────────
            # rel_row 0 … group_height-1, stored as global sequential IDs
            group_stems_json = json.dumps([stem for stem, _ in group_cells])
            group_db_rows: list = []    # index = rel_row → db_row_id
            for rel in range(group_height):
                global_pos += 1.0
                db_row_id   = row_counter
                row_counter += 1
                conn.execute(
                    "INSERT INTO rows(row_id,position,page_stem,page_row_idx,group_stems)"
                    " VALUES(?,?,?,?,?)",
                    (db_row_id, global_pos, "", rel, group_stems_json),
                )
                group_db_rows.append(db_row_id)

            # ── Fill cells using exact write_cells logic ───────────────────────
            # For each page: rel_row starts at align pad, advances by max_lines
            # per lattice row_idx (mirrors excel_row = base_row + align_pad
            # then excel_row += max_lines).
            for pi, (stem, cells) in enumerate(group_cells):
                col_off = page_col_offsets[pi] if pi < len(page_col_offsets) else 0
                rmap: dict = defaultdict(list)
                for c in cells:
                    rmap[c["row_idx"]].append(c)

                rel_row = pads[pi]          # start after alignment blank rows
                for row_idx in sorted(rmap):
                    row_cells = rmap[row_idx]
                    max_lines = max(len(c["lines"]) for c in row_cells)
                    for line_i in range(max_lines):
                        db_rel = rel_row + line_i
                        if db_rel >= len(group_db_rows):
                            break
                        db_row_id = group_db_rows[db_rel]
                        # Annotate the row with the real stem/row_idx
                        # (only set once; first page that covers this rel wins)
                        conn.execute(
                            "UPDATE rows SET page_stem=?, page_row_idx=?"
                            " WHERE row_id=? AND page_stem=''",
                            (stem, row_idx, db_row_id),
                        )
                        for c in row_cells:
                            db_col = col_off + c["col_idx"]
                            if db_col >= total_cols:
                                continue
                            if line_i < len(c["lines"]):
                                val = c["lines"][line_i]
                            else:
                                val = ""
                            src = c.get("source", "ocr")
                            conn.execute(
                                "INSERT OR REPLACE INTO cells"
                                "(row_id,col_id,value,original_value,source)"
                                " VALUES(?,?,?,?,?)",
                                (db_row_id, db_col, val, val, src),
                            )
                    rel_row += max_lines

                # Store raw line count per lattice cell (before any padding).
                # len(c["lines"]) reflects the actual OCR/LLM/human line count
                # for the preferred layer — used by crawl detection to find
                # columns whose vector length differs from the majority.
                if stem:
                    for c in cells:
                        conn.execute(
                            "INSERT OR REPLACE INTO raw_line_counts"
                            "(page_stem,row_idx,col_idx,page_index,line_count)"
                            " VALUES(?,?,?,?,?)",
                            (stem, c["row_idx"], c["col_idx"], pi, len(c["lines"])),
                        )

    return {
        "ok": True,
        "columns": total_cols,
        "rows": row_counter,
        "groups": len(groups),
        "pages": len(jfiles),
    }


# ── Table ─────────────────────────────────────────────────────────────────────

@router.get("/table")
def api_table(folder: str, include_deleted: bool = False):
    with _db(folder) as conn:
        cols = [dict(r) for r in conn.execute(
            "SELECT * FROM columns ORDER BY position")]
        row_filter = "" if include_deleted else "WHERE r.is_deleted=0"
        rows = [dict(r) for r in conn.execute(
            f"SELECT r.* FROM rows r {row_filter} ORDER BY r.position")]
        cells: dict = {}
        # JOIN avoids a huge IN (?,?,…) clause that can hit SQLite's variable limit
        del_filter = "" if include_deleted else "JOIN rows r ON c.row_id=r.row_id AND r.is_deleted=0"
        for r in conn.execute(
                f"SELECT c.row_id, c.col_id, c.value, c.original_value, c.source"
                f" FROM cells c {del_filter}"):
            val  = r["value"]
            orig = r["original_value"]
            # Only ship original_value/source when value was edited — cuts payload
            # roughly in half for large unedited imports.
            if orig and orig != val:
                cells.setdefault(r["row_id"], {})[r["col_id"]] = {
                    "value":          val,
                    "original_value": orig,
                    "source":         r["source"],
                }
            else:
                cells.setdefault(r["row_id"], {})[r["col_id"]] = {"value": val}
    return {"columns": cols, "rows": rows, "cells": cells}


# ── Cell edit ─────────────────────────────────────────────────────────────────

class CellEdit(BaseModel):
    folder: str
    row_id: int
    col_id: int
    value: str
    note: str = ""


@router.patch("/cell")
def api_cell_edit(req: CellEdit):
    with _db(req.folder) as conn:
        existing = conn.execute(
            "SELECT value, original_value FROM cells WHERE row_id=? AND col_id=?",
            (req.row_id, req.col_id),
        ).fetchone()
        old      = existing["value"] if existing else None
        orig     = existing["original_value"] if existing else req.value
        conn.execute(
            "INSERT OR REPLACE INTO cells(row_id,col_id,value,original_value,source)"
            " VALUES(?,?,?,?,'manual')",
            (req.row_id, req.col_id, req.value, orig),
        )
        _log(conn, "cell_edit",
             row_id=req.row_id, col_id=req.col_id,
             old_value=old, new_value=req.value, note=req.note)
    return {"ok": True}


# ── Row operations ────────────────────────────────────────────────────────────

@router.delete("/row/{row_id}")
def api_row_delete(row_id: int, folder: str = Query(...), note: str = Query("")):
    with _db(folder) as conn:
        conn.execute("UPDATE rows SET is_deleted=1 WHERE row_id=?", (row_id,))
        _log(conn, "row_delete", row_id=row_id, note=note)
    return {"ok": True}


@router.post("/row/{row_id}/restore")
def api_row_restore(row_id: int, folder: str = Query(...), note: str = Query("")):
    with _db(folder) as conn:
        conn.execute("UPDATE rows SET is_deleted=0 WHERE row_id=?", (row_id,))
        _log(conn, "row_restore", row_id=row_id, note=note)
    return {"ok": True}


@router.patch("/row/{row_id}/hierarchy")
def api_row_hierarchy(row_id: int, folder: str = Query(...),
                      level: Optional[int] = Query(None)):
    """Set or clear the hierarchy level of a row (1=járás … 4=ország, None=regular)."""
    if level is not None and level not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="level must be 1–4 or absent")
    with _db(folder) as conn:
        conn.execute("UPDATE rows SET hierarchy_level=? WHERE row_id=?",
                     (level, row_id))
        _log(conn, "row_hierarchy", row_id=row_id,
             new_value=str(level) if level is not None else "clear")
    return {"ok": True}


class ReorderRequest(BaseModel):
    folder: str
    row_ids: List[int]


@router.post("/rows/reorder")
def api_rows_reorder(req: ReorderRequest):
    with _db(req.folder) as conn:
        for pos, rid in enumerate(req.row_ids, 1):
            conn.execute("UPDATE rows SET position=? WHERE row_id=?", (float(pos), rid))
        _log(conn, "row_move", note=f"reordered {len(req.row_ids)} rows")
    return {"ok": True}


# ── Column operations ─────────────────────────────────────────────────────────

class ColEdit(BaseModel):
    folder: str
    col_id: int
    name: Optional[str] = None
    variable: Optional[str] = None
    role: Optional[str] = None


@router.get("/columns")
def api_columns(folder: str):
    with _db(folder) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM columns ORDER BY position")]


@router.patch("/column")
def api_col_edit(req: ColEdit):
    with _db(req.folder) as conn:
        if req.name is not None:
            conn.execute("UPDATE columns SET name=? WHERE col_id=?",
                         (req.name, req.col_id))
            _log(conn, "col_rename", col_id=req.col_id, new_value=req.name)
        if req.variable is not None:
            clash = conn.execute(
                "SELECT col_id FROM columns WHERE variable=? AND col_id!=?",
                (req.variable, req.col_id),
            ).fetchone()
            if clash:
                raise HTTPException(400, detail=f"Variable '{req.variable}' already used")
            conn.execute("UPDATE columns SET variable=? WHERE col_id=?",
                         (req.variable, req.col_id))
            _log(conn, "col_variable", col_id=req.col_id, new_value=req.variable)
        if req.role is not None:
            conn.execute("UPDATE columns SET role=? WHERE col_id=?",
                         (req.role, req.col_id))
            _log(conn, "col_role", col_id=req.col_id, new_value=req.role)
    return {"ok": True}


# ── Config export / import ───────────────────────────────────────────────────

@router.get("/config")
def api_config_export(folder: str):
    """Download columns + constraints as an editable JSON file."""
    with _db(folder) as conn:
        cols = [dict(r) for r in conn.execute(
            "SELECT col_id, name, variable, role FROM columns ORDER BY position")]
        cons = [dict(r) for r in conn.execute(
            "SELECT label, lhs, rhs, tolerance, severity FROM constraints ORDER BY constraint_id")]
    payload = json.dumps({"columns": cols, "constraints": cons}, indent=2, ensure_ascii=False)
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="datalab_config.json"'},
    )


class ConfigImport(BaseModel):
    folder: str
    columns: list
    constraints: list


@router.post("/config")
def api_config_import(req: ConfigImport):
    """Apply columns + constraints from an edited JSON config."""
    with _db(req.folder) as conn:
        # Update columns (name, variable, role only — never touch col_id/position)
        for col in req.columns:
            cid  = col.get("col_id")
            name = col.get("name", "")
            var  = col.get("variable", "")
            role = col.get("role", "data")
            if cid is None:
                continue
            if var:
                clash = conn.execute(
                    "SELECT col_id FROM columns WHERE variable=? AND col_id!=?",
                    (var, cid),
                ).fetchone()
                if clash:
                    raise HTTPException(400, detail=f"Variable '{var}' already used by col {clash['col_id']}")
            conn.execute(
                "UPDATE columns SET name=?, variable=?, role=? WHERE col_id=?",
                (name, var, role, cid),
            )
            _log(conn, "col_config", col_id=cid,
                 note=f"name={name!r} var={var!r} role={role!r}")

        # Replace all constraints
        conn.execute("DELETE FROM constraints")
        for con in req.constraints:
            op = con.get("operator", "=")
            if op not in ("=", ">=", "<="):
                op = "="
            conn.execute(
                "INSERT INTO constraints(label,lhs,operator,rhs,tolerance,severity)"
                " VALUES(?,?,?,?,?,?)",
                (con.get("label", ""),
                 con.get("lhs", ""),
                 op,
                 con.get("rhs", ""),
                 float(con.get("tolerance", 0)),
                 con.get("severity", "error")),
            )
        _log(conn, "config_import",
             note=f"{len(req.columns)} cols, {len(req.constraints)} constraints")
    return {"ok": True}


# ── Constraints ───────────────────────────────────────────────────────────────

class ConstraintIn(BaseModel):
    folder: str
    label: str
    lhs: str
    rhs: str
    operator: str = "="
    tolerance: float = 0.0
    severity: str = "error"


@router.get("/constraints")
def api_constraints(folder: str):
    with _db(folder) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM constraints ORDER BY constraint_id")]


@router.post("/constraint")
def api_constraint_add(req: ConstraintIn):
    with _db(req.folder) as conn:
        op = req.operator if req.operator in ("=", ">=", "<=") else "="
        cur = conn.execute(
            "INSERT INTO constraints(label,lhs,operator,rhs,tolerance,severity)"
            " VALUES(?,?,?,?,?,?)",
            (req.label, req.lhs, op, req.rhs, req.tolerance, req.severity),
        )
        cid = cur.lastrowid
        _log(conn, "constraint_add", constraint_id=cid,
             note=f"{req.label}: {req.lhs} {op} {req.rhs} ±{req.tolerance}")
    return {"ok": True, "constraint_id": cid}


@router.delete("/constraint/{constraint_id}")
def api_constraint_delete(constraint_id: int, folder: str = Query(...)):
    with _db(folder) as conn:
        conn.execute("DELETE FROM constraints WHERE constraint_id=?", (constraint_id,))
        _log(conn, "constraint_delete", constraint_id=constraint_id)
    return {"ok": True}


# ── Violation detection ───────────────────────────────────────────────────────

_SAFE_BUILTINS = {"__builtins__": {}, "abs": abs, "round": round, "int": int}
_VAR_RE        = re.compile(r"\b([A-Za-z_]\w*)\b")


def _eval_expr(expr: str, ns: dict):
    try:
        return float(eval(expr, _SAFE_BUILTINS, ns))
    except Exception:
        return None


def _is_violated(delta: float, operator: str, tolerance: float) -> bool:
    """Return True when lhs − rhs (=delta) breaks the constraint."""
    op = operator or "="
    if op == "=":
        return abs(delta) > tolerance
    if op == ">=":
        return delta < -tolerance   # lhs < rhs − tol
    if op == "<=":
        return delta > tolerance    # lhs > rhs + tol
    return abs(delta) > tolerance   # fallback


def _constraint_weight(con: dict) -> float:
    """
    Credibility weight for a constraint based on how many distinct variables
    appear in its expression.  A 3-variable rule (X1+X2=X3) gets weight 1/3;
    a 6-variable rule gets 1/6.  Used to up-rank fixes to simpler rules.
    """
    n = len(set(_VAR_RE.findall(con["lhs"])) | set(_VAR_RE.findall(con["rhs"])))
    return 1.0 / max(n, 1)


@router.get("/violations")
def api_violations(folder: str):
    with _db(folder) as conn:
        constraints = [dict(r) for r in conn.execute("SELECT * FROM constraints")]
        columns     = {r["col_id"]: dict(r) for r in conn.execute("SELECT * FROM columns")}
        rows        = [dict(r) for r in conn.execute(
            "SELECT * FROM rows WHERE is_deleted=0 ORDER BY position")]
        cells_raw: dict = {}
        for r in conn.execute(
                "SELECT c.row_id, c.col_id, c.value FROM cells c"
                " JOIN rows r ON c.row_id=r.row_id AND r.is_deleted=0"):
            cells_raw.setdefault(r["row_id"], {})[r["col_id"]] = r["value"]

    var_to_col = {
        v["variable"]: v["col_id"]
        for v in columns.values()
        if v["variable"]
    }

    result = []
    for con in constraints:
        expr_str  = con["lhs"] + " " + con["rhs"]
        used_vars = {m for m in _VAR_RE.findall(expr_str) if m in var_to_col}
        used_cols = {var: var_to_col[var] for var in used_vars}

        viols = []
        for row in rows:
            rid      = row["row_id"]
            row_data = cells_raw.get(rid, {})
            ns: dict = {}
            skip     = False
            for var, cid in used_cols.items():
                num = parse_number(row_data.get(cid))
                if num is None:
                    skip = True
                    break
                ns[var] = num
            if skip:
                continue

            lv = _eval_expr(con["lhs"], ns)
            rv = _eval_expr(con["rhs"], ns)
            if lv is None or rv is None:
                continue

            delta = lv - rv
            op    = con.get("operator") or "="
            if _is_violated(delta, op, con["tolerance"]):
                viols.append({
                    "row_id":  rid,
                    "delta":   round(delta, 6),
                    "lhs_val": lv,
                    "rhs_val": rv,
                    "cells":   {
                        var: {
                            "col_id": cid,
                            "value":  row_data.get(cid),
                        }
                        for var, cid in used_cols.items()
                    },
                })

        result.append({
            "constraint": con,
            "count":      len(viols),
            "violations": viols,
        })

    return result


def _row_rule_viol(rid, pc, cells_raw, override=None):
    """Return True if row rid violates parsed constraint pc (with optional overrides)."""
    row_data = cells_raw.get(rid, {})
    ns: dict = {}
    for var, cid in pc["used_cols"].items():
        raw = (override.get((rid, cid), row_data.get(cid))
               if override else row_data.get(cid))
        num = parse_number(raw)
        if num is None:
            return False   # unevaluable → not a violation
        ns[var] = num
    lv = _eval_expr(pc["lhs"], ns)
    rv = _eval_expr(pc["rhs"], ns)
    if lv is None or rv is None:
        return False
    return _is_violated(lv - rv, pc.get("op", "="), pc["tol"])


# ── Crawl detection ──────────────────────────────────────────────────────────

@router.get("/crawl")
def api_detect_crawl(folder: str):
    """
    Detect column-length mismatches within lattice elements using raw line
    counts stored during import (before any line-count padding).

    For each lattice row (page_stem × row_idx) we compare how many lines
    each column had in the original annotation.  Columns whose count differs
    from the majority are candidates for a crawl: the OCR/LLM either merged
    two adjacent cells into one (too short) or split one cell into two (too
    long).

    For each mismatched constrained-data column we simulate inserting or
    removing a phantom row at every position within the DB lattice element
    and pick the position that most reduces constraint violations.  Results
    with no improvement are suppressed.  All mismatched columns of the same
    lattice element are reported as a single result (same phantom_row_id).
    """
    with _db(folder) as conn:
        constraints_list = [dict(r) for r in conn.execute("SELECT * FROM constraints")]
        columns_list     = [dict(r) for r in conn.execute(
            "SELECT * FROM columns ORDER BY position")]
        columns_dict     = {r["col_id"]: r for r in columns_list}

        rows_db = [dict(r) for r in conn.execute(
            "SELECT row_id, position, page_stem, page_row_idx FROM rows"
            " WHERE is_deleted=0 AND page_stem != '' ORDER BY position")]

        cells_raw: dict = {}
        for r in conn.execute(
                "SELECT c.row_id, c.col_id, c.value FROM cells c"
                " JOIN rows r ON c.row_id=r.row_id AND r.is_deleted=0"):
            cells_raw.setdefault(r["row_id"], {})[r["col_id"]] = r["value"]

        # Raw line counts written during import — one row per lattice cell,
        # reflecting the actual preferred-layer line count before padding.
        raw_counts = [dict(r) for r in conn.execute(
            "SELECT page_stem, row_idx, col_idx, page_index, line_count"
            " FROM raw_line_counts")]

    if not constraints_list or not rows_db or not raw_counts:
        return []

    # ── Build lookup structures ───────────────────────────────────────────────

    var_to_col = {v["variable"]: v["col_id"]
                  for v in columns_list if v.get("variable")}

    # col_id offset for each page slot: min col_id that belongs to page_index pi
    page_index_to_offset: dict = {}
    for col in columns_list:
        pi  = col.get("page_index", 0)
        cid = col["col_id"]
        if pi not in page_index_to_offset or cid < page_index_to_offset[pi]:
            page_index_to_offset[pi] = cid

    # (page_stem, page_row_idx) → DB row_ids in position order
    row_position = {r["row_id"]: r["position"] for r in rows_db}
    stem_rowidx_to_db_rows: dict = defaultdict(list)
    for r in rows_db:
        if r["page_row_idx"] is not None:
            stem_rowidx_to_db_rows[(r["page_stem"], r["page_row_idx"])].append(r["row_id"])
    for key in stem_rowidx_to_db_rows:
        stem_rowidx_to_db_rows[key].sort(key=lambda rid: row_position[rid])

    # Group raw line counts by (page_stem, row_idx)
    # → {col_idx: line_count}, and remember the page_index for col_id mapping
    stem_rowidx_col_lines: dict = defaultdict(dict)   # key → {col_idx: line_count}
    stem_rowidx_pi:        dict = {}                   # key → page_index
    for rc in raw_counts:
        key = (rc["page_stem"], rc["row_idx"])
        stem_rowidx_col_lines[key][rc["col_idx"]] = rc["line_count"]
        stem_rowidx_pi[key] = rc["page_index"]

    # ── Pre-parse constraints ─────────────────────────────────────────────────
    parsed_cons = []
    for con in constraints_list:
        used_vars = {m for m in _VAR_RE.findall(con["lhs"] + " " + con["rhs"])
                     if m in var_to_col}
        used_cols = {var: var_to_col[var] for var in used_vars}
        if used_cols:
            parsed_cons.append({
                "lhs": con["lhs"], "rhs": con["rhs"],
                "op":  con.get("operator") or "=",
                "tol": con["tolerance"],
                "used_cols": used_cols,
                "col_id_set": set(used_cols.values()),
            })

    if not parsed_cons:
        return []

    constrained_col_ids = set().union(*(pc["col_id_set"] for pc in parsed_cons))

    # ── Violation helper ──────────────────────────────────────────────────────
    def row_viols(rid, override=None):
        count    = 0
        row_data = cells_raw.get(rid, {})
        for pc in parsed_cons:
            ns   = {}
            skip = False
            for var, cid in pc["used_cols"].items():
                raw = (override.get((rid, cid), row_data.get(cid))
                       if override else row_data.get(cid))
                num = parse_number(raw)
                if num is None:
                    skip = True
                    break
                ns[var] = num
            if skip:
                continue
            lv = _eval_expr(pc["lhs"], ns)
            rv = _eval_expr(pc["rhs"], ns)
            if lv is not None and rv is not None and _is_violated(lv - rv, pc["op"], pc["tol"]):
                count += 1
        return count

    # ── Main scan ─────────────────────────────────────────────────────────────
    MAX_ELEM_SIZE = 40
    results = []

    for key, col_line_counts in stem_rowidx_col_lines.items():
        page_stem, row_idx = key

        if len(col_line_counts) < 2:
            continue   # single-column lattice row — nothing to compare

        # Majority line count for this lattice row
        expected = Counter(col_line_counts.values()).most_common(1)[0][0]
        if expected == 0:
            continue

        # col_idxs whose line count differs from the majority
        mismatched_cidx = {cidx: cnt
                           for cidx, cnt in col_line_counts.items()
                           if cnt != expected}
        if not mismatched_cidx:
            continue

        # Map col_idx → col_id via the stored page_index offset
        pi     = stem_rowidx_pi.get(key, 0)
        col_off = page_index_to_offset.get(pi, 0)

        # Keep only constrained data columns
        mismatched_cid_diff: dict = {}   # col_id → diff (<0 short, >0 long)
        for cidx, cnt in mismatched_cidx.items():
            cid = col_off + cidx
            col = columns_dict.get(cid, {})
            if cid in constrained_col_ids and col.get("role") == "data":
                mismatched_cid_diff[cid] = cnt - expected

        if not mismatched_cid_diff:
            continue

        # DB rows for this lattice element
        elem_rids = stem_rowidx_to_db_rows.get(key, [])
        n = len(elem_rids)
        if n < 2 or n > MAX_ELEM_SIZE:
            continue

        # Baseline violations
        row_viol_cache = [row_viols(rid) for rid in elem_rids]
        baseline = sum(row_viol_cache)
        if baseline == 0:
            continue   # nothing to improve

        # Prefix sums: rows before shift position are unaffected
        prefix = [0] * (n + 1)
        for k in range(n):
            prefix[k + 1] = prefix[k] + row_viol_cache[k]

        # ── Per-column shift search ───────────────────────────────────────────
        col_best: dict = {}   # col_id → {pos, viols_after, diff}

        for cid, diff in mismatched_cid_diff.items():
            cur_vals = [cells_raw.get(rid, {}).get(cid, "") for rid in elem_rids]
            best_v   = baseline
            best_pos = None

            for p in range(n):
                ov: dict = {}
                if diff < 0:
                    # Too short: insert phantom "0" at position p, shift rest down
                    for k in range(n - 1, p, -1):
                        ov[(elem_rids[k], cid)] = cur_vals[k - 1]
                    ov[(elem_rids[p], cid)] = "0"
                else:
                    # Too long: remove value at position p, shift rest up
                    if not cur_vals[p]:
                        continue
                    for k in range(p, n - 1):
                        ov[(elem_rids[k], cid)] = cur_vals[k + 1]
                    ov[(elem_rids[n - 1], cid)] = ""

                v = prefix[p] + sum(row_viols(elem_rids[k], ov) for k in range(p, n))
                if v < best_v:
                    best_v   = v
                    best_pos = p

            if best_pos is not None:
                col_best[cid] = {"pos": best_pos, "viols_after": best_v, "diff": diff}

        if not col_best:
            continue

        # ── One result per lattice element (grouped by best fix position) ─────
        pos_to_cols: dict = defaultdict(list)
        for cid, info in col_best.items():
            pos_to_cols[info["pos"]].append(cid)

        for pos, col_ids in pos_to_cols.items():
            best_after = min(col_best[cid]["viols_after"] for cid in col_ids)
            diff       = col_best[col_ids[0]]["diff"]

            # Build the combined value override for the best shift position
            # so we can measure per-rule improvement at that exact fix.
            combined_ov: dict = {}
            for cid in col_ids:
                cur_vals = [cells_raw.get(rid, {}).get(cid, "") for rid in elem_rids]
                d = col_best[cid]["diff"]
                if d < 0:
                    for k in range(n - 1, pos, -1):
                        combined_ov[(elem_rids[k], cid)] = cur_vals[k - 1]
                    combined_ov[(elem_rids[pos], cid)] = "0"
                else:
                    for k in range(pos, n - 1):
                        combined_ov[(elem_rids[k], cid)] = cur_vals[k + 1]
                    combined_ov[(elem_rids[n - 1], cid)] = ""

            # Per-rule breakdown: how many violations in this element,
            # before and after the proposed shift, for each constraint.
            per_rule = []
            for i, pc in enumerate(parsed_cons):
                vb = sum(1 for rid in elem_rids
                         if _row_rule_viol(rid, pc, cells_raw))
                va = sum(1 for rid in elem_rids
                         if _row_rule_viol(rid, pc, cells_raw, combined_ov))
                if vb > 0 or va > 0:
                    per_rule.append({
                        "constraint_id": constraints_list[i]["constraint_id"],
                        "label":         constraints_list[i]["label"],
                        "viols_before":  vb,
                        "viols_after":   va,
                    })

            results.append({
                "col_ids":      col_ids,
                "col_names":    [columns_dict[cid]["name"] for cid in col_ids],
                "run_row_ids":  elem_rids,
                "mismatch":     diff,
                "remedy": {
                    "direction": "down" if diff < 0 else "up",
                    "amount":    abs(diff),
                },
                "viols_before": baseline,
                "viols_after":  best_after,
                "viol_rate":    round((baseline - best_after) / baseline, 2),
                "per_rule":     per_rule,
            })

    results.sort(key=lambda g: -(g["viols_before"] - g["viols_after"]))
    return results


# ── Fix — single cell ─────────────────────────────────────────────────────────

class FixCell(BaseModel):
    folder: str
    row_id: int
    col_id: int
    value: str
    constraint_id: Optional[int] = None
    note: str = ""


@router.post("/fix/cell")
def api_fix_cell(req: FixCell):
    with _db(req.folder) as conn:
        existing = conn.execute(
            "SELECT value, original_value FROM cells WHERE row_id=? AND col_id=?",
            (req.row_id, req.col_id),
        ).fetchone()
        old  = existing["value"] if existing else None
        orig = existing["original_value"] if existing else req.value
        conn.execute(
            "INSERT OR REPLACE INTO cells(row_id,col_id,value,original_value,source)"
            " VALUES(?,?,?,?,'manual')",
            (req.row_id, req.col_id, req.value, orig),
        )
        _log(conn, "fix_cell",
             row_id=req.row_id, col_id=req.col_id,
             old_value=old, new_value=req.value,
             constraint_id=req.constraint_id, note=req.note)
    return {"ok": True}


# ── Fix — batch (one constraint, many rows) ───────────────────────────────────

class FixItem(BaseModel):
    row_id: int
    col_id: int
    value: str


class FixBatch(BaseModel):
    folder: str
    constraint_id: int
    fixes: List[FixItem]
    note: str = ""


@router.post("/fix/batch")
def api_fix_batch(req: FixBatch):
    applied = 0
    with _db(req.folder) as conn:
        for fix in req.fixes:
            existing = conn.execute(
                "SELECT value, original_value FROM cells WHERE row_id=? AND col_id=?",
                (fix.row_id, fix.col_id),
            ).fetchone()
            old  = existing["value"] if existing else None
            orig = existing["original_value"] if existing else fix.value
            conn.execute(
                "INSERT OR REPLACE INTO cells(row_id,col_id,value,original_value,source)"
                " VALUES(?,?,?,?,'manual')",
                (fix.row_id, fix.col_id, fix.value, orig),
            )
            _log(conn, "fix_batch",
                 row_id=fix.row_id, col_id=fix.col_id,
                 old_value=old, new_value=fix.value,
                 constraint_id=req.constraint_id, note=req.note)
            applied += 1
    return {"ok": True, "applied": applied}


# ── Cleaning suggestions ─────────────────────────────────────────────────────

@router.get("/meta")
def api_meta_get(folder: str = Query(...)):
    with _db(folder) as conn:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
    return {r["key"]: r["value"] for r in rows}


class MetaSet(BaseModel):
    folder: str
    key:    str
    value:  str


@router.post("/meta")
def api_meta_set(req: MetaSet):
    with _db(req.folder) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (req.key, req.value))
    return {"ok": True}


def _digit_edits(val_str: str) -> set:
    """
    All integer values reachable from val_str by exactly ONE digit operation:
      - replace any digit with another (0-9)
      - delete any one digit
      - insert any digit at any position
    Leading zeros (except '0' itself) are rejected.
    The original value is excluded.
    Non-numeric / empty strings: only single-digit insertions are returned.
    """
    s = str(val_str).strip()
    results: set = set()

    def _add(ns: str):
        ns = ns.strip()
        if not ns:
            results.add(0)
            return
        if len(ns) > 1 and ns[0] == '0':
            return  # leading zero
        try:
            results.add(int(ns))
        except ValueError:
            pass

    if not s or not any(c.isdigit() for c in s):
        for d in '0123456789':
            _add(d)
        return results

    # Replace any digit
    for i, ch in enumerate(s):
        if ch.isdigit():
            for d in '0123456789':
                if d != ch:
                    _add(s[:i] + d + s[i + 1:])

    # Delete any one digit
    for i, ch in enumerate(s):
        if ch.isdigit():
            _add(s[:i] + s[i + 1:])

    # Insert a digit at any position
    for i in range(len(s) + 1):
        for d in '0123456789':
            _add(s[:i] + d + s[i:])

    # Exclude the original value
    try:
        results.discard(int(s))
    except ValueError:
        pass

    return results


def _build_hier_constraints(conn, row_id: int, cols_list: list) -> list:
    """
    Build implicit hierarchy constraints for row_id so the solver can see
    vertical sum rules alongside horizontal formula rules.

    If row_id is a hierarchy row of level N:
      For each data column c:  row[c] = sum of children rows' c values.
      Children = non-deleted rows above this row, back to the previous row
      with hierarchy_level >= N, that have hierarchy_level < N.

    If row_id is a plain (non-hierarchy) row:
      For each hierarchy level N (1–4), find the nearest hierarchy row H of
      level N that follows this row AND for which this row is actually a child
      (no same-or-higher-level row sits in between).
      For each data column c:  row[c] = H[c] − sum_of_siblings[c]
      (i.e. changing this row's value should keep H's sum intact).
    """
    all_rows = [dict(r) for r in conn.execute(
        "SELECT row_id, position, hierarchy_level, is_deleted FROM rows ORDER BY position"
    )]
    cell_map: dict = {}
    for cell in conn.execute("SELECT row_id, col_id, value FROM cells"):
        cell_map.setdefault(cell["row_id"], {})[cell["col_id"]] = cell["value"]

    data_cols = [c for c in cols_list if c["role"] == "data"]

    ri = next((i for i, r in enumerate(all_rows) if r["row_id"] == row_id), None)
    if ri is None:
        return []

    this_hl = all_rows[ri]["hierarchy_level"] or 0
    constraints: list = []

    def _pf(v):
        try:
            return float(v) if v else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _children_of(hier_ri: int, level: int) -> list:
        start = 0
        for j in range(hier_ri - 1, -1, -1):
            if (all_rows[j]["hierarchy_level"] or 0) >= level and not all_rows[j]["is_deleted"]:
                start = j + 1
                break
        return [
            all_rows[j]["row_id"]
            for j in range(start, hier_ri)
            if not all_rows[j]["is_deleted"]
            and (all_rows[j]["hierarchy_level"] or 0) < level
        ]

    if this_hl > 0:
        # This is a hierarchy row — add constraint: row[c] = sum(children[c])
        child_ids = _children_of(ri, this_hl)
        for col in data_cols:
            cid = col["col_id"]
            child_sum = sum(_pf(cell_map.get(rid, {}).get(cid)) for rid in child_ids)
            constraints.append({
                "constraint_id": f"__hier_{row_id}_{cid}",
                "label":         f"H{this_hl}∑ {col['name']}",
                "lhs":           col["variable"],
                "rhs":           str(child_sum),
                "tolerance":     0.5,
            })
    else:
        # Plain row — check which hierarchy rows it feeds into
        for level in range(1, 5):
            hier_ri = None
            for j in range(ri + 1, len(all_rows)):
                if (all_rows[j]["hierarchy_level"] or 0) == level and not all_rows[j]["is_deleted"]:
                    hier_ri = j
                    break
            if hier_ri is None:
                continue
            # Make sure no same-or-higher-level row sits between this row and H
            if any(
                (all_rows[j]["hierarchy_level"] or 0) >= level and not all_rows[j]["is_deleted"]
                for j in range(ri + 1, hier_ri)
            ):
                continue
            hier_rid = all_rows[hier_ri]["row_id"]
            sibling_ids = [rid for rid in _children_of(hier_ri, level) if rid != row_id]
            for col in data_cols:
                cid = col["col_id"]
                hier_val = _pf(cell_map.get(hier_rid, {}).get(cid))
                sib_sum  = sum(_pf(cell_map.get(sid, {}).get(cid)) for sid in sibling_ids)
                constraints.append({
                    "constraint_id": f"__hier_{hier_rid}_{cid}_feed",
                    "label":         f"→H{level} {col['name']}",
                    "lhs":           col["variable"],
                    "rhs":           str(hier_val - sib_sum),
                    "tolerance":     0.5,
                })

    return constraints


@router.get("/suggest")
def api_suggest(folder: str = Query(...), row_id: int = Query(...),
                non_negative: bool = Query(False)):
    """
    For a given row return two cleaning suggestions based on DIGIT-LEVEL edits.

    A "single change" = one digit operation on one cell value:
      replace a digit / insert a digit / delete a digit.

    best_single — for each data column, enumerate all values reachable by one
        digit operation and pick the candidate that most reduces total
        constraint violation (explicit rules + implicit hierarchy sum checks).
        Returns up to 5 candidates (one per column), best first.

    min_fix — the fewest total digit operations across all data columns that
        makes every constraint hold within tolerance.  Found by BFS over edit
        depth (max 4 total digit edits, 15 k states).
    """
    with _db(folder) as conn:
        cols_list        = [dict(r) for r in conn.execute(
            "SELECT * FROM columns ORDER BY position")]
        cell_rows        = conn.execute(
            "SELECT col_id, value FROM cells WHERE row_id=?", (row_id,)
        ).fetchall()
        constrs_explicit = [dict(r) for r in conn.execute("SELECT * FROM constraints")]
        hier_constrs     = _build_hier_constraints(conn, row_id, cols_list)

    col_by_id = {c["col_id"]: c for c in cols_list}
    cell_vals = {r["col_id"]: r["value"] for r in cell_rows}

    if not constrs_explicit and not hier_constrs:
        return {"row_id": row_id, "n_violations": 0,
                "violations": [], "hier_violations": [],
                "best_single": [], "min_fix": None}

    data_cols = [c for c in cols_list if c["role"] == "data"]

    def make_ns(overrides: dict = None):
        ns = {}
        for c in cols_list:
            raw = (overrides or {}).get(c["col_id"], cell_vals.get(c["col_id"], ""))
            ns[c["variable"]] = parse_number(raw) or 0.0
        return ns

    base_ns = make_ns()

    def eval_delta(con, ns):
        lv = _eval_expr(con["lhs"], ns)
        rv = _eval_expr(con["rhs"], ns)
        return (lv - rv) if (lv is not None and rv is not None) else None

    # Attach credibility weight (1/n_vars) to each explicit constraint once
    for con in constrs_explicit:
        con.setdefault("_weight", _constraint_weight(con))

    # Explicit (horizontal) violations drive Q1/Q2 scoring and BFS termination
    violated = []
    for con in constrs_explicit:
        d = eval_delta(con, base_ns)
        op = con.get("operator") or "="
        if d is not None and _is_violated(d, op, con["tolerance"]):
            violated.append({"con": con, "delta": d})

    # Hierarchy (vertical) violations are shown separately — not fixed by digit edits
    hier_violated = []
    for con in hier_constrs:
        d = eval_delta(con, base_ns)
        if d is not None and _is_violated(d, "=", con["tolerance"]):
            hier_violated.append({"con": con, "delta": d})

    if not violated:
        return {"row_id": row_id, "n_violations": 0,
                "violations": [],
                "hier_violations": [{"label": v["con"]["label"],
                                     "delta": round(v["delta"], 4)}
                                    for v in hier_violated],
                "best_single": [], "min_fix": None}

    # Weighted total: simpler constraints (fewer vars) count more
    total_wabs   = sum(abs(v["delta"]) * v["con"]["_weight"] for v in violated)
    violated_ids = {v["con"]["constraint_id"] for v in violated}

    # ── Q1: best single-digit edit per column ─────────────────────────────────
    best_single: list = []
    for col in data_cols:
        cid     = col["col_id"]
        cur_str = str(cell_vals.get(cid) or "")
        candidates = _digit_edits(cur_str)
        if non_negative:
            candidates = {v for v in candidates if v >= 0}
        if not candidates:
            continue

        # Precompute hierarchy baseline for tiebreaking
        hier_base_abs = sum(
            abs(eval_delta(con, base_ns) or 0) for con in hier_constrs
        )

        # Precompute hierarchy baseline for tiebreaking
        hier_base_abs = sum(
            abs(eval_delta(con, base_ns) or 0) for con in hier_constrs
        )

        col_best = None
        for proposed in candidates:
            ns_new = make_ns({cid: str(proposed)})
            n_fixed, n_broken = 0, 0
            new_wabs = 0.0          # weighted remaining + newly broken
            broken_labels: list = []
            rem_labels:    list = []
            for con in constrs_explicit:
                nd  = eval_delta(con, ns_new)
                if nd is None:
                    continue
                w     = con["_weight"]
                op    = con.get("operator") or "="
                was_v = con["constraint_id"] in violated_ids
                now_v = _is_violated(nd, op, con["tolerance"])
                if was_v and not now_v:
                    n_fixed += 1
                elif was_v and now_v:
                    new_wabs += abs(nd) * w
                    rem_labels.append(con["label"])
                elif not was_v and now_v:
                    n_broken += 1
                    broken_labels.append(con["label"])
                    new_wabs += abs(nd) * w
            w_improvement = total_wabs - new_wabs   # positive = net gain
            if w_improvement <= 1e-9 and n_fixed == 0:
                continue

            # Hierarchy improvement as final tiebreaker
            hier_new_abs = sum(
                abs(eval_delta(con, ns_new) or 0) for con in hier_constrs
            )
            hier_improvement = round(hier_base_abs - hier_new_abs, 4)

            entry = {
                "col_id":               cid,
                "col_name":             col["name"],
                "variable":             col["variable"],
                "old_value":            cur_str,
                "proposed_value":       str(proposed),
                "n_digit_edits":        1,
                "constraints_fixed":    n_fixed,
                "constraints_broken":   n_broken,
                "broken_labels":        broken_labels,
                "remaining_violations": rem_labels,
                "violation_reduction":  round(w_improvement, 4),
                "hier_improvement":     hier_improvement,
            }
            # Pick best candidate for this column
            def _beats(a, b):
                if a["constraints_broken"] != b["constraints_broken"]:
                    return a["constraints_broken"] < b["constraints_broken"]
                if a["constraints_fixed"] != b["constraints_fixed"]:
                    return a["constraints_fixed"] > b["constraints_fixed"]
                if abs(a["violation_reduction"] - b["violation_reduction"]) > 1e-9:
                    return a["violation_reduction"] > b["violation_reduction"]
                return a["hier_improvement"] > b["hier_improvement"]

            if col_best is None or _beats(entry, col_best):
                col_best = entry

        if col_best:
            best_single.append(col_best)

    # Sort: fewer broken → more fixed → higher weighted reduction → hierarchy bonus
    best_single.sort(key=lambda x: (x["constraints_broken"],
                                    -x["constraints_fixed"],
                                    -x["violation_reduction"],
                                    -x.get("hier_improvement", 0)))

    # ── Q2: BFS — minimum total digit edits that fixes everything ─────────────
    MAX_DEPTH   = 4
    STATE_LIMIT = 15_000

    def check_all(overrides: dict) -> bool:
        """True when all explicit (horizontal) constraints are satisfied."""
        ns = make_ns(overrides)
        for con in constrs_explicit:
            d  = eval_delta(con, ns)
            op = con.get("operator") or "="
            if d is None or _is_violated(d, op, con["tolerance"]):
                return False
        return True

    min_fix = None

    # Shortcut: if any Q1 candidate already fixes everything, use it directly
    for s in best_single:
        if not s["remaining_violations"] and not s["constraints_broken"]:
            min_fix = {
                "n_digit_edits": 1,
                "n_changes":     1,
                "changes": [{
                    "col_id":         s["col_id"],
                    "col_name":       s["col_name"],
                    "variable":       s["variable"],
                    "old_value":      s["old_value"],
                    "proposed_value": s["proposed_value"],
                }],
            }
            break

    if min_fix is None and not check_all({}):
        # BFS: state = dict {col_id → value_str for overridden cells}
        queue:   deque = deque([({}, 0)])
        visited: set   = {frozenset()}
        states_seen    = 0

        while queue and states_seen < STATE_LIMIT:
            overrides, depth = queue.popleft()
            if depth >= MAX_DEPTH:
                continue
            found = False
            for col in data_cols:
                cid     = col["col_id"]
                cur_str = overrides.get(cid, str(cell_vals.get(cid) or ""))
                for new_val in _digit_edits(cur_str):
                    if non_negative and new_val < 0:
                        continue
                    new_ov  = {**overrides, cid: str(new_val)}
                    key     = frozenset(new_ov.items())
                    if key in visited:
                        continue
                    visited.add(key)
                    states_seen += 1
                    if check_all(new_ov):
                        min_fix = {
                            "n_digit_edits": depth + 1,
                            "n_changes":     len(new_ov),
                            "changes": [
                                {
                                    "col_id":         c["col_id"],
                                    "col_name":       c["name"],
                                    "variable":       c["variable"],
                                    "old_value":      str(cell_vals.get(c["col_id"]) or ""),
                                    "proposed_value": new_ov[c["col_id"]],
                                }
                                for c in data_cols if c["col_id"] in new_ov
                            ],
                        }
                        found = True
                        break
                    queue.append((new_ov, depth + 1))
                if found:
                    break
            if found:
                break

    return {
        "row_id":          row_id,
        "n_violations":    len(violated),
        "violations":      [{"label": v["con"]["label"],
                              "delta": round(v["delta"], 4)} for v in violated],
        "hier_violations": [{"label": v["con"]["label"],
                              "delta": round(v["delta"], 4)} for v in hier_violated],
        "best_single":     best_single[:8],
        "min_fix":         min_fix,
    }


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/audit")
def api_audit(folder: str, limit: int = 1000):
    with _db(folder) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log ORDER BY log_id DESC LIMIT ?", (limit,))]


def _best_q1_for_batch(row_id, cols_list, cell_vals_by_row, constrs_explicit):
    """
    For a single row, find the best single-digit edit (across ALL data columns)
    that fixes the most explicit constraints while breaking ZERO new ones.

    Takes pre-loaded data — no DB access.
    Returns dict {row_id, col_id, col_name, old_value, proposed_value,
                  constraints_fixed, violation_reduction} or None.
    """
    cell_vals = cell_vals_by_row.get(row_id, {})

    def make_ns(overrides=None):
        ns = {}
        for c in cols_list:
            raw = (overrides or {}).get(c["col_id"], cell_vals.get(c["col_id"], ""))
            ns[c["variable"]] = parse_number(raw) or 0.0
        return ns

    def eval_delta(con, ns):
        lv = _eval_expr(con["lhs"], ns)
        rv = _eval_expr(con["rhs"], ns)
        return (lv - rv) if (lv is not None and rv is not None) else None

    base_ns = make_ns()

    # Pre-check which constraints are currently violated
    violated_ids = set()
    for con in constrs_explicit:
        d  = eval_delta(con, base_ns)
        op = con.get("operator") or "="
        if d is not None and _is_violated(d, op, con["tolerance"]):
            violated_ids.add(con["constraint_id"])

    if not violated_ids:
        return None

    total_wabs = sum(
        abs(eval_delta(con, base_ns) or 0) * con.get("_weight", 1.0)
        for con in constrs_explicit
        if con["constraint_id"] in violated_ids
    )

    data_cols = [c for c in cols_list if c["role"] == "data"]
    global_best = None

    for col in data_cols:
        cid     = col["col_id"]
        cur_str = str(cell_vals.get(cid) or "")
        candidates = _digit_edits(cur_str)
        if not candidates:
            continue

        col_best = None
        for proposed in candidates:
            ns_new = make_ns({cid: str(proposed)})
            n_fixed, n_broken = 0, 0
            new_wabs = 0.0
            for con in constrs_explicit:
                nd  = eval_delta(con, ns_new)
                if nd is None:
                    continue
                w     = con.get("_weight", 1.0)
                op    = con.get("operator") or "="
                was_v = con["constraint_id"] in violated_ids
                now_v = _is_violated(nd, op, con["tolerance"])
                if was_v and not now_v:
                    n_fixed += 1
                elif was_v and now_v:
                    new_wabs += abs(nd) * w
                elif not was_v and now_v:
                    n_broken += 1
                    new_wabs += abs(nd) * w

            if n_broken > 0:
                continue
            if n_fixed == 0:
                continue

            w_improvement = total_wabs - new_wabs

            entry = {
                "row_id":             row_id,
                "col_id":             cid,
                "col_name":           col["name"],
                "old_value":          cur_str,
                "proposed_value":     str(proposed),
                "constraints_fixed":  n_fixed,
                "violation_reduction": round(w_improvement, 4),
            }

            def _beats(a, b):
                if a["constraints_fixed"] != b["constraints_fixed"]:
                    return a["constraints_fixed"] > b["constraints_fixed"]
                return a["violation_reduction"] > b["violation_reduction"]

            if col_best is None or _beats(entry, col_best):
                col_best = entry

        if col_best is not None:
            if global_best is None or col_best["constraints_fixed"] > global_best["constraints_fixed"] or (
                col_best["constraints_fixed"] == global_best["constraints_fixed"] and
                col_best["violation_reduction"] > global_best["violation_reduction"]
            ):
                global_best = col_best

    return global_best


@router.get("/batch-clean")
def api_batch_clean(folder: str = Query(...), job_id: str = Query(...)):
    """SSE endpoint: iteratively apply the best zero-breaking digit edits."""

    def generate():
        total_applied = 0
        iteration     = 0
        try:
            while True:
                if _batch_stop.get(job_id):
                    yield f"data: {json.dumps({'type': 'stopped', 'iteration': iteration, 'total_applied': total_applied})}\n\n"
                    return

                iteration += 1
                yield f"data: {json.dumps({'type': 'scan_start', 'iteration': iteration})}\n\n"

                # ── Load data (no yield inside with block) ────────────────────
                with _db(folder) as conn:
                    cols_list = [dict(r) for r in conn.execute(
                        "SELECT * FROM columns ORDER BY position")]
                    rows_db   = [dict(r) for r in conn.execute(
                        "SELECT row_id FROM rows WHERE is_deleted=0 ORDER BY position")]
                    constrs_explicit = [dict(r) for r in conn.execute(
                        "SELECT * FROM constraints")]
                    cell_vals_by_row: dict = {}
                    for r in conn.execute(
                            "SELECT c.row_id, c.col_id, c.value FROM cells c"
                            " JOIN rows rv ON c.row_id=rv.row_id AND rv.is_deleted=0"):
                        cell_vals_by_row.setdefault(r["row_id"], {})[r["col_id"]] = r["value"]

                # Attach weights once
                for con in constrs_explicit:
                    con.setdefault("_weight", _constraint_weight(con))

                # ── Scan all rows ─────────────────────────────────────────────
                candidates = []
                for row in rows_db:
                    if _batch_stop.get(job_id):
                        yield f"data: {json.dumps({'type': 'stopped', 'iteration': iteration, 'total_applied': total_applied})}\n\n"
                        return
                    best = _best_q1_for_batch(
                        row["row_id"], cols_list, cell_vals_by_row, constrs_explicit)
                    if best is not None:
                        candidates.append(best)

                yield f"data: {json.dumps({'type': 'scan_done', 'iteration': iteration, 'n_candidates': len(candidates)})}\n\n"

                if not candidates:
                    yield f"data: {json.dumps({'type': 'done', 'reason': 'no_candidates', 'max_fixed': 0, 'total_applied': total_applied})}\n\n"
                    return

                max_fixed = max(c["constraints_fixed"] for c in candidates)
                if max_fixed <= 1:
                    yield f"data: {json.dumps({'type': 'done', 'reason': 'max_fixed_1', 'max_fixed': max_fixed, 'total_applied': total_applied})}\n\n"
                    return

                to_apply = [c for c in candidates if c["constraints_fixed"] == max_fixed]
                yield f"data: {json.dumps({'type': 'applying', 'iteration': iteration, 'count': len(to_apply), 'max_fixed': max_fixed})}\n\n"

                # ── Apply all selected edits in one transaction ───────────────
                applied_list = []
                ts = datetime.now(timezone.utc).isoformat()
                with _db(folder) as conn:
                    for cand in to_apply:
                        existing = conn.execute(
                            "SELECT value, original_value FROM cells WHERE row_id=? AND col_id=?",
                            (cand["row_id"], cand["col_id"]),
                        ).fetchone()
                        old_val  = existing["value"] if existing else None
                        orig_val = existing["original_value"] if existing else cand["proposed_value"]
                        conn.execute(
                            "INSERT OR REPLACE INTO cells(row_id,col_id,value,original_value,source)"
                            " VALUES(?,?,?,?,'batch_clean')",
                            (cand["row_id"], cand["col_id"], cand["proposed_value"], orig_val),
                        )
                        conn.execute(
                            "INSERT INTO audit_log"
                            "(ts,action,row_id,col_id,old_value,new_value,note)"
                            " VALUES(?,?,?,?,?,?,?)",
                            (ts, "cell_edit",
                             cand["row_id"], cand["col_id"],
                             old_val, cand["proposed_value"],
                             f"batch_clean iter={iteration}"),
                        )
                        applied_list.append({
                            "row_id":            cand["row_id"],
                            "col_id":            cand["col_id"],
                            "col_name":          cand["col_name"],
                            "old_value":         old_val,
                            "new_value":         cand["proposed_value"],
                            "constraints_fixed": cand["constraints_fixed"],
                        })
                # Transaction committed — now yield events
                for ap in applied_list:
                    yield f"data: {json.dumps({'type': 'applied', **ap})}\n\n"

                total_applied += len(applied_list)
                yield f"data: {json.dumps({'type': 'iter_done', 'iteration': iteration, 'applied': len(applied_list), 'total_applied': total_applied})}\n\n"

        except GeneratorExit:
            pass
        finally:
            _batch_stop.pop(job_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/batch-clean/{job_id}")
def api_batch_clean_stop(job_id: str):
    _batch_stop[job_id] = True
    return {"ok": True}


@router.get("/audit/export")
def api_audit_export(folder: str, format: str = "csv"):
    with _db(folder) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log ORDER BY log_id ASC")]

    if format == "md":
        lines = ["# EconAI Audit Log\n"]
        for r in rows:
            lines.append(f"### [{r['ts']}] `{r['action']}`")
            parts = []
            if r["row_id"] is not None:
                parts.append(f"row={r['row_id']}")
            if r["col_id"] is not None:
                parts.append(f"col={r['col_id']}")
            if r["constraint_id"] is not None:
                parts.append(f"constraint={r['constraint_id']}")
            if parts:
                lines.append("  " + "  |  ".join(parts))
            if r["old_value"] is not None or r["new_value"] is not None:
                lines.append(f"  `{r['old_value']}` → `{r['new_value']}`")
            if r["note"]:
                lines.append(f"  *{r['note']}*")
            lines.append("")
        content = "\n".join(lines)
        return StreamingResponse(
            iter([content]),
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="audit_log.md"'},
        )

    # CSV
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "log_id", "ts", "action", "row_id", "col_id",
        "old_value", "new_value", "constraint_id", "note",
    ])
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )
