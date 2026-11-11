"""The entity view (knowledge_base/13 §2.4, roadmap P12 item 2, step E5).

Projects are *linked* when their records resolve against the same authority
file (`config.record_authority`). For such a group this module builds the
cross-source table: one row per entity of the authority, one column per
linked project (ordered by its `source_year`), a cell = the record of that
project resolved to the entity, with enough provenance to open it in the
editor (annotations folder, page stem, head-fragment index). Records of a
linked project that resolved to nothing are listed apart, per project.

Everything is derived: the authority file's slices (provenance) joined to
the projects' live record marks. Nothing here is stored.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

_CACHE: dict = {}            # project name -> {"sig": .., "heads": {gid: {...}}}
_CACHE_LOCK = threading.Lock()


def _fold(s) -> str:
    try:
        from unidecode import unidecode
        s = unidecode(str(s or ""))
    except ImportError:
        s = str(s or "")
    s = re.sub(r"[^\w\s]", " ", s.casefold())
    return re.sub(r"\s+", " ", s).strip()


def _ann_signature(ann: Path) -> tuple:
    """Cheap change detector for a project's annotations folder."""
    try:
        with os.scandir(ann) as it:
            n, latest = 0, 0.0
            for e in it:
                if e.name.endswith(".json"):
                    n += 1
                    m = e.stat().st_mtime
                    if m > latest:
                        latest = m
        return (n, latest)
    except OSError:
        return (0, 0.0)


def record_heads(name: str, ann: Path, pages: list, record_label: str, mode) -> dict:
    """{group_id: {stem, idx, name, mark}} for a project's records, cached on
    the annotations folder's signature. `pages` = [(stem, data)] as
    `_records_pages` returns them; `mark` = the head's `authority` dict."""
    from app import records as _records
    sig = _ann_signature(ann)
    with _CACHE_LOCK:
        c = _CACHE.get(name)
        if c and c["sig"] == sig:
            return c["heads"]
    stem_order = {s: i for i, (s, _) in enumerate(pages)}
    coll = _records.collect_records(pages, record_label, mode, stem_order)
    by_stem = dict(pages)
    heads = {}
    for gid, rec in coll["records"].items():
        h = rec["fragments"][0]
        shapes = (by_stem.get(h["stem"]) or {}).get("shapes") or []
        sh = shapes[h["idx"]] if 0 <= h["idx"] < len(shapes) else {}
        st = sh.get("structured") or {}
        obj = st.get("data") if st.get("data") is not None else st.get("llm")
        printed = None
        if isinstance(obj, dict):
            printed = obj.get("name_hu") or obj.get("name_de") or None
        sug = sh.get("authority_suggestion") if isinstance(sh.get("authority_suggestion"), dict) else None
        heads[gid] = {"stem": h["stem"], "idx": h["idx"],
                      "name": printed or rec.get("name") or "",
                      "n_fragments": len(rec["fragments"]),
                      "mark": sh.get("authority") if isinstance(sh.get("authority"), dict) else None,
                      "suggestion": ({k: sug.get(k) for k in ("verdict", "entity_id", "entity_name", "confidence", "stage")}
                                     if sug else None)}
    with _CACHE_LOCK:
        _CACHE[name] = {"sig": sig, "heads": heads}
    return heads


def columns_for(linked: list) -> list:
    """Linked projects as columns, by source year then name; a project
    without a year sorts last."""
    def key(p):
        y = p.get("source_year")
        return (y is None, y if y is not None else 0, p["name"])
    return sorted(linked, key=key)


def entity_rows(entities: list, edges: list, heads_by_project: dict, canonical: dict) -> list:
    """One row per entity with a cell per linked project it has a record in."""
    by_id = {e.get("id"): e for e in entities if e.get("id")}
    edges_by = {}
    for ed in edges or []:
        for k in (ed.get("from"), ed.get("to")):
            if k:
                edges_by.setdefault(k, []).append(ed)
    rows = []
    for e in entities:
        eid = e.get("id")
        if not eid:
            continue
        cells = {}
        for sl in e.get("slices") or []:
            prov = sl.get("provenance") or {}
            proj, gid = prov.get("project"), prov.get("group_id")
            if not proj or proj not in heads_by_project:
                continue
            head = heads_by_project[proj].get(gid) if gid is not None else None
            cell = {"group_id": gid, "as_of": sl.get("as_of"), "name": sl.get("name"),
                    "stem": (head or {}).get("stem") or prov.get("stem"),
                    "idx": (head or {}).get("idx"),
                    "live": bool(head and (head.get("mark") or {}).get("id") in (eid, None)),
                    "outcome": ((head or {}).get("mark") or {}).get("outcome"),
                    "source": ((head or {}).get("mark") or {}).get("source"),
                    "seq": sl.get("seq")}
            cells.setdefault(proj, []).append(cell)
        rows.append({"id": eid, "name": e.get("name"), "type": e.get("type"),
                     "aliases": [a.get("name") for a in (e.get("aliases") or []) if a.get("name")],
                     "facts": {k: (e.get("attrs") or {}).get(k) for k in ("seat", "founded", "legal_form")
                               if (e.get("attrs") or {}).get(k) not in (None, "")},
                     "cells": cells,
                     "edges": [{"from": ed.get("from"), "to": ed.get("to"), "type": ed.get("type"),
                                "as_of": ed.get("as_of"),
                                "other_name": (by_id.get(ed.get("to") if ed.get("from") == eid else ed.get("from")) or {}).get("name")}
                               for ed in edges_by.get(eid, [])],
                     "canonical": canonical.get(eid, eid)})
    return rows


def unresolved_for(heads: dict) -> list:
    return [{"group_id": gid, "stem": h["stem"], "idx": h["idx"], "name": h["name"],
             "n_fragments": h["n_fragments"], "suggestion": h.get("suggestion")}
            for gid, h in sorted(heads.items()) if not (h.get("mark") or {}).get("id")]


def filter_rows(rows: list, q: str | None, mode: str | None, n_columns: int) -> list:
    if q:
        fq = _fold(q)
        rows = [r for r in rows if fq in _fold(r["name"]) or any(fq in _fold(a) for a in r["aliases"])
                or fq == _fold(r["id"])]
    if mode == "missing":
        rows = [r for r in rows if len(r["cells"]) < n_columns]
    elif mode == "chain":
        rows = [r for r in rows if r["edges"]]
    elif mode == "dup":
        rows = [r for r in rows if any(ed["type"] == "duplicate_of" for ed in r["edges"])]
    elif mode == "single":
        rows = [r for r in rows if len(r["cells"]) == 1]
    return rows
