"""Record-level entity resolution (knowledge_base/13 §2, step E3).

A record (the fragments behind one `group_id`) resolves to an entity of a
shared authority file with one of three outcomes (13 §2.2):

    same       → a slice on the existing entity (+ the printed name as alias)
    new        → a new entity minted from the record
    successor  → a new entity plus a typed edge from the predecessor

The decision is stored on the record's HEAD fragment as `shape["authority"]`
(the same field cells carry, so the editor shows it), with `record` and
`outcome` keys. The authority file receives entities, aliases, slices and
edges only — never per-page decisions (13 §5.2).

What this module knows: how to turn a record's identity object (the
structured extraction of 13 §1b, when present) or its name text into
queries, blocking boosts (seat, founding year, county, legal form — soft,
authority-agnostic: they compare slice attrs of the same names) and the
slice / aliases written back. It knows nothing about firms.
"""
from __future__ import annotations

import re

# Soft ranking boosts (on top of the string score; the accept floor is
# still judged on the raw similarity, like the parent boost for places).
BOOST_SEAT     = 12.0
BOOST_FOUNDED  = 10.0
BOOST_COUNTY   = 4.0
BOOST_FORM     = 3.0

IDENT_KEYS = ("serial_no", "name_hu", "name_de", "other_names", "legal_form", "seat",
              "county", "address", "founded", "charter_until", "capital_nominal",
              "capital_paid_in", "currency", "notes")

OUTCOMES = ("same", "new", "successor")


def identity_of(structured) -> dict:
    """The identity fields from a record's structured object (data over
    llm), restricted to the known keys; {} when there is none."""
    if not isinstance(structured, dict):
        return {}
    obj = structured.get("data") if structured.get("data") is not None else structured.get("llm")
    if not isinstance(obj, dict):
        return {}
    return {k: obj.get(k) for k in IDENT_KEYS if obj.get(k) not in (None, "", [])}


def queries_for(record_name: str, ident: dict) -> list:
    """[(query, lang)] in priority order: Hungarian name, German name, other
    names, then the record's own text when nothing structured exists."""
    out = []
    if ident.get("name_hu"):
        out.append((str(ident["name_hu"]), "hu"))
    if ident.get("name_de"):
        out.append((str(ident["name_de"]), "de"))
    for o in ident.get("other_names") or []:
        if isinstance(o, dict) and o.get("name"):
            out.append((str(o["name"]), o.get("lang")))
        elif isinstance(o, str) and o.strip():
            out.append((o.strip(), None))
    if not out and (record_name or "").strip():
        out.append((record_name.strip(), None))
    return out


def _norm(s) -> str:
    s = str(s or "").strip().casefold()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _year(v):
    try:
        return int(str(v).strip()[:4])
    except (TypeError, ValueError):
        return None


def entity_facts(entity: dict) -> dict:
    """seat / county / founded / legal_form of an entity, from its latest
    slice's attrs (falling back through earlier slices and top-level attrs)."""
    facts = {}
    layers = [entity.get("attrs") or {}] + [sl.get("attrs") or {} for sl in (entity.get("slices") or [])]
    for layer in layers:
        for k in ("seat", "county", "founded", "legal_form"):
            if layer.get(k) not in (None, ""):
                facts[k] = layer[k]
    return facts


def boost_for(entity: dict, ident: dict) -> tuple:
    """(boost, reasons) comparing the record's identity with the entity's facts."""
    if not ident or not entity:
        return 0.0, []
    f = entity_facts(entity)
    b, why = 0.0, []
    if ident.get("seat") and f.get("seat") and _norm(ident["seat"]) == _norm(f["seat"]):
        b += BOOST_SEAT; why.append("seat")
    fy, ey = _year(ident.get("founded")), _year(f.get("founded"))
    if fy and ey and fy == ey:
        b += BOOST_FOUNDED; why.append("founded")
    if ident.get("county") and f.get("county") and _norm(ident["county"]) == _norm(f["county"]):
        b += BOOST_COUNTY; why.append("county")
    if ident.get("legal_form") and f.get("legal_form") and _norm(ident["legal_form"]) == _norm(f["legal_form"]):
        b += BOOST_FORM; why.append("legal_form")
    return b, why


def has_slice_from(entity: dict, source: str, except_gid=None) -> bool:
    """True when the entity already carries a slice from `source` (another
    record of the same source), unless that slice is this very record's."""
    for sl in entity.get("slices") or []:
        if sl.get("source") != source:
            continue
        prov = sl.get("provenance") or {}
        if except_gid is not None and prov.get("group_id") == except_gid:
            continue
        return True
    return False


def merge_candidates(per_query: list, raw_by_id: dict, ident: dict, k: int = 8,
                     rescore=None, exclude_source: str | None = None, own_gid=None) -> list:
    """Union of the matcher's candidate lists (one per query), best score per
    entity, blocking boosts applied for ranking; `score` is the string
    similarity (re-scored by `rescore(query, matched)` when given — the
    record lane compares WHOLE names, so sharing a generic word is not a
    match) so callers can judge the accept floor on it.

    A directory lists a firm once: an entity that already holds a slice
    from `exclude_source` (another record of this same source) is never a
    candidate — so a bootstrap mints every record, and a dry run says what
    the real run does."""
    best: dict = {}
    for q, lang, cands in per_query:
        for c in cands:
            cid = c.get("id")
            if not cid:
                continue
            if exclude_source and has_slice_from(raw_by_id.get(cid) or {}, exclude_source, own_gid):
                continue
            score = c.get("score", 0)
            if rescore is not None:
                score = rescore(q, c.get("matched") or "")
            cur = best.get(cid)
            if not cur or score > cur["score"]:
                best[cid] = {**c, "score": float(score), "query": q, "query_lang": lang}
    out = []
    for cid, c in best.items():
        ent = raw_by_id.get(cid) or {}
        b, why = boost_for(ent, ident)
        facts = entity_facts(ent)
        slices = ent.get("slices") or []
        last = slices[-1] if slices else {}
        out.append({**c, "boost": b, "boost_why": why, "ranked": c["score"] + b,
                    "facts": facts, "n_slices": len(slices),
                    "last_slice": {"as_of": last.get("as_of"), "source": last.get("source"),
                                   "name": last.get("name"), "provenance": last.get("provenance"),
                                   "seq": last.get("seq")},
                    "aliases": [a.get("name") for a in (ent.get("aliases") or []) if a.get("name")][:6]})
    out.sort(key=lambda c: (-c["ranked"], c.get("id") or ""))
    return out[:k]


def aliases_from(ident: dict, source: str) -> list:
    out = []
    if ident.get("name_hu"):
        out.append({"name": str(ident["name_hu"]), "lang": "hu", "source": source})
    if ident.get("name_de"):
        out.append({"name": str(ident["name_de"]), "lang": "de", "source": source})
    for o in ident.get("other_names") or []:
        if isinstance(o, dict) and o.get("name"):
            out.append({"name": str(o["name"]), "lang": o.get("lang"), "source": source})
    return out


def display_name(record_name: str, ident: dict) -> str:
    return str(ident.get("name_hu") or ident.get("name_de") or record_name or "").strip()


def slice_from(ident: dict, record_name: str, project: str, stem: str, gid, seq,
               source_year, etype) -> dict:
    """The source-year slice a resolution writes to the entity."""
    attrs = {k: ident[k] for k in ("seat", "county", "founded", "charter_until", "legal_form",
                                   "capital_nominal", "capital_paid_in", "currency", "address",
                                   "serial_no", "name_de", "notes") if ident.get(k) not in (None, "")}
    return {"as_of": source_year, "source": project, "type": etype,
            "name": display_name(record_name, ident) or None,
            "attrs": attrs,
            "provenance": {"project": project, "stem": stem, "group_id": gid},
            "seq": seq if seq is not None else gid}


def entity_attrs(ident: dict) -> dict:
    """Timeless-ish facts worth keeping at entity level at mint time."""
    return {k: ident[k] for k in ("legal_form", "founded", "seat") if ident.get(k) not in (None, "")}


def mark(entity: dict | None, outcome: str, source: str, score, file: str, gid, ts: str,
         edge: dict | None = None) -> dict:
    """What the head fragment stores under `authority`."""
    m = {"id": (entity or {}).get("id"), "name": (entity or {}).get("name"),
         "type": (entity or {}).get("type"), "source": source,
         "score": score, "ts": ts, "file": file,
         "outcome": outcome, "record": {"group_id": gid}}
    if edge:
        m["edge"] = {k: edge.get(k) for k in ("from", "to", "type")}
    return m


def summary(authority) -> dict | None:
    if not isinstance(authority, dict) or not authority.get("id"):
        return None
    return {k: authority.get(k) for k in ("id", "name", "source", "score", "ts", "file", "outcome", "edge")}
