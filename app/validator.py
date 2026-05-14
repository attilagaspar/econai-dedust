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
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── Router ────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/validate")

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
    col_id   INTEGER PRIMARY KEY,
    position INTEGER,
    name     TEXT,
    variable TEXT,
    role     TEXT DEFAULT 'data',
    dtype    TEXT DEFAULT 'int'
);
CREATE TABLE IF NOT EXISTS rows (
    row_id       INTEGER PRIMARY KEY,
    position     REAL,
    page_stem    TEXT,
    page_row_idx INTEGER,
    is_deleted   INTEGER DEFAULT 0
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
"""


@contextmanager
def _db(folder: str):
    path = _db_path(folder)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
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


def _shapes_to_cells(shapes, layer="best_llm", selected_types=None):
    """Return list of raw cell dicts with spatial grid coordinates."""
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
        return []

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

    # Column clustering (global, cx-based)
    med_w    = sorted(c["w"] for c in raw)[len(raw) // 2]
    thresh_x = max(3, med_w * 0.45)
    all_cx   = sorted({c["cx"] for c in raw})
    col_centers: list = []
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
    ]


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
    first_group_cells = []
    for jf in groups[0]:
        cells = _shapes_to_cells(_load_shapes(jf), req.layer, selected_types)
        first_group_cells.append(cells)
    # Pad to ppr if first group is short
    while len(first_group_cells) < ppr:
        first_group_cells.append([])

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
            for col_id in range(total_cols):
                conn.execute(
                    "INSERT INTO columns(col_id,position,name,variable,role,dtype)"
                    " VALUES(?,?,?,?,?,?)",
                    (col_id, col_id, f"col_{col_id}", f"col_{col_id}", "data", "int"),
                )

        # Always reset rows and cells
        conn.execute("DELETE FROM cells")
        conn.execute("DELETE FROM rows")

        _log(conn, "import",
             note=f"pages_per_row={ppr}, layer={req.layer}, "
                  f"total_cols={total_cols}, groups={len(groups)}, "
                  f"pages={len(jfiles)}")

        row_counter    = 0
        global_pos     = 0.0

        for group in groups:
            # ── Load cells for each page slot (cells now carry .lines list) ───
            group_cells = []
            for jf in group:
                cells = _shapes_to_cells(_load_shapes(jf), req.layer, selected_types)
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
            group_db_rows: list = []    # index = rel_row → db_row_id
            for rel in range(group_height):
                global_pos += 1.0
                db_row_id   = row_counter
                row_counter += 1
                conn.execute(
                    "INSERT INTO rows(row_id,position,page_stem,page_row_idx)"
                    " VALUES(?,?,?,?)",
                    (db_row_id, global_pos, "", rel),
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
        q = ("SELECT * FROM rows"
             + ("" if include_deleted else " WHERE is_deleted=0")
             + " ORDER BY position")
        rows = [dict(r) for r in conn.execute(q)]
        cells: dict = {}
        if rows:
            ids = [r["row_id"] for r in rows]
            ph  = ",".join("?" * len(ids))
            for r in conn.execute(
                    f"SELECT * FROM cells WHERE row_id IN ({ph})", ids):
                cells.setdefault(r["row_id"], {})[r["col_id"]] = {
                    "value":          r["value"],
                    "original_value": r["original_value"],
                    "source":         r["source"],
                }
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


# ── Constraints ───────────────────────────────────────────────────────────────

class ConstraintIn(BaseModel):
    folder: str
    label: str
    lhs: str
    rhs: str
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
        cur = conn.execute(
            "INSERT INTO constraints(label,lhs,rhs,tolerance,severity)"
            " VALUES(?,?,?,?,?)",
            (req.label, req.lhs, req.rhs, req.tolerance, req.severity),
        )
        cid = cur.lastrowid
        _log(conn, "constraint_add", constraint_id=cid,
             note=f"{req.label}: {req.lhs} == {req.rhs} ±{req.tolerance}")
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


@router.get("/violations")
def api_violations(folder: str):
    with _db(folder) as conn:
        constraints = [dict(r) for r in conn.execute("SELECT * FROM constraints")]
        columns     = {r["col_id"]: dict(r) for r in conn.execute("SELECT * FROM columns")}
        rows        = [dict(r) for r in conn.execute(
            "SELECT * FROM rows WHERE is_deleted=0 ORDER BY position")]
        row_ids     = [r["row_id"] for r in rows]
        cells_raw: dict = {}
        if row_ids:
            ph = ",".join("?" * len(row_ids))
            for r in conn.execute(
                    f"SELECT row_id,col_id,value FROM cells WHERE row_id IN ({ph})",
                    row_ids):
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
            if abs(delta) > con["tolerance"]:
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


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/audit")
def api_audit(folder: str, limit: int = 1000):
    with _db(folder) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log ORDER BY log_id DESC LIMIT ?", (limit,))]


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
