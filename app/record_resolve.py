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


_LETTER_SPACED_RE = re.compile(r"(?<=\S) (?=\S)")


def unspace(text: str) -> str:
    """Collapse letter-spaced print ("Z i n n e r  M ó r i t z": the PDF text
    layer of a spaced-out firm name) into words. Within a run whose tokens
    are mostly one character and separated by single spaces, the single
    spaces go; two or more spaces (or a line break) separate words.
    Ordinary text comes back with its whitespace normalised, nothing else."""
    t = str(text or "").strip()
    if not t:
        return ""
    out = []
    for part in re.split(r"\s{2,}|\n", t):
        toks = part.split(" ")
        if len(toks) >= 3 and sum(1 for x in toks if len(x) == 1) >= 0.6 * len(toks):
            out.append(_LETTER_SPACED_RE.sub("", part))
        else:
            out.append(re.sub(r"\s+", " ", part).strip())
    return " ".join(x for x in out if x)


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


def _fold(s) -> str:
    try:
        from unidecode import unidecode
        s = unidecode(str(s or ""))
    except ImportError:
        s = str(s or "")
    return _norm(s)


def seats_agree(a, b):
    """True / False / None (unknown). Tolerates spelling and accents
    (Bártfa ~ Bartfa, Deés ~ Déés), an abbreviated first part (B.-Csaba ~
    Békés-Csaba), and one name containing the other (Gyergyó-Szent-Miklós
    ~ Gyergyó); different towns disagree."""
    if not a or not b:
        return None
    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return None
    if fa == fb or fa in fb or fb in fa:
        return True
    ta, tb = fa.split(), fb.split()
    if ta and tb and ta[-1] == tb[-1]:               # same last part: B.-Csaba / Békés-Csaba
        return True
    try:
        from rapidfuzz import fuzz
        return fuzz.ratio(fa, fb) >= 85
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, fa, fb).ratio() * 100 >= 85


def years_agree(a, b):
    ya, yb = _year(a), _year(b)
    if ya is None or yb is None:
        return None
    return ya == yb


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
    if seats_agree(ident.get("seat"), f.get("seat")):
        b += BOOST_SEAT; why.append("seat")
    if years_agree(ident.get("founded"), f.get("founded")):
        b += BOOST_FOUNDED; why.append("founded")
    if ident.get("county") and f.get("county") and _norm(ident["county"]) == _norm(f["county"]):
        b += BOOST_COUNTY; why.append("county")
    if ident.get("legal_form") and f.get("legal_form") and _norm(ident["legal_form"]) == _norm(f["legal_form"]):
        b += BOOST_FORM; why.append("legal_form")
    return b, why


def merged_entity(raw_by_id: dict, members: dict | None, cid: str) -> dict:
    """The entity as the duplicate_of edges make it (13 §5.3): the survivor's
    own record with the slices and aliases of the entities merged into it
    folded in (`members` = {survivor: [merged ids]}, from the authority
    index) and its missing facts filled from them. Derived, never stored:
    the ids stay, the edge is the only fact written."""
    ent = raw_by_id.get(cid) or {}
    losers = [raw_by_id.get(x) for x in (members or {}).get(cid) or []]
    losers = [x for x in losers if x]
    if not losers:
        return ent
    out = dict(ent)
    slices = list(ent.get("slices") or [])
    for l in losers:
        slices.extend(l.get("slices") or [])
    out["slices"] = sorted(slices, key=lambda sl: str(sl.get("as_of") if sl.get("as_of") is not None else ""))
    seen = {_norm(ent.get("name") or "")}
    aliases = []
    for a in ent.get("aliases") or []:
        if a.get("name") and _norm(a["name"]) not in seen:
            seen.add(_norm(a["name"])); aliases.append(a)
    for l in losers:
        for a in [{"name": l.get("name"), "source": "duplicate_of"}] + list(l.get("aliases") or []):
            if a.get("name") and _norm(a["name"]) not in seen:
                seen.add(_norm(a["name"])); aliases.append(a)
    out["aliases"] = aliases
    attrs = {}
    for l in losers:
        for k, v in (l.get("attrs") or {}).items():
            if v not in (None, ""):
                attrs.setdefault(k, v)
    attrs.update({k: v for k, v in (ent.get("attrs") or {}).items() if v not in (None, "")})
    out["attrs"] = attrs
    out["merged_from"] = [l.get("id") for l in losers]
    return out


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


def first_seen(entity: dict):
    """The earliest source year an entity was observed in (its slices)."""
    years = [_year(sl.get("as_of")) for sl in (entity.get("slices") or [])]
    years = [y for y in years if y]
    return min(years) if years else None


def founded_after_seen(ident: dict, entity: dict):
    """True when the record's founding year is LATER than the earliest year
    the candidate entity was already observed in. Shown to the user and the
    judge, not a veto: the printed year is often the re-foundation or the
    change of legal form of the same firm (13 §5.4). None when unknown."""
    fy, seen = _year(ident.get("founded")), first_seen(entity)
    if fy is None or seen is None:
        return None
    return fy > seen


def merge_candidates(per_query: list, raw_by_id: dict, ident: dict, k: int = 8,
                     rescore=None, exclude_source: str | None = None, own_gid=None,
                     members: dict | None = None) -> list:
    """Union of the matcher's candidate lists (one per query), best score per
    entity, blocking boosts applied for ranking; `score` is the string
    similarity (re-scored by `rescore(query, matched)` when given — the
    record lane compares WHOLE names, so sharing a generic word is not a
    match) so callers can judge the accept floor on it.

    A directory lists a firm once: an entity that already holds a slice
    from `exclude_source` (another record of this same source) is never a
    candidate — so a bootstrap mints every record, and a dry run says what
    the real run does. Candidates are survivors (the matcher collapses
    duplicates), so the slices and facts looked at are the merged group's
    (`members`, see `merged_entity`)."""
    best: dict = {}
    for q, lang, cands in per_query:
        for c in cands:
            cid = c.get("id")
            if not cid:
                continue
            if exclude_source and has_slice_from(merged_entity(raw_by_id, members, cid), exclude_source, own_gid):
                continue
            score = c.get("score", 0)
            if rescore is not None:
                score = rescore(q, c.get("matched") or "")
            cur = best.get(cid)
            if not cur or score > cur["score"]:
                best[cid] = {**c, "score": float(score), "query": q, "query_lang": lang}
    out = []
    for cid, c in best.items():
        ent = merged_entity(raw_by_id, members, cid)
        b, why = boost_for(ent, ident)
        facts = entity_facts(ent)
        slices = ent.get("slices") or []
        last = slices[-1] if slices else {}
        out.append({**c, "boost": b, "boost_why": why, "ranked": c["score"] + b,
                    "facts": facts, "n_slices": len(slices),
                    "seat_agree": seats_agree(ident.get("seat"), facts.get("seat")),
                    "year_agree": years_agree(ident.get("founded"), facts.get("founded")),
                    "first_seen": first_seen(ent),
                    "founded_after_seen": founded_after_seen(ident, ent),
                    "last_slice": {"as_of": last.get("as_of"), "source": last.get("source"),
                                   "name": last.get("name"), "provenance": last.get("provenance"),
                                   "seq": last.get("seq")},
                    "aliases": [a.get("name") for a in (ent.get("aliases") or []) if a.get("name")][:6],
                    "merged_from": ent.get("merged_from") or []})
    out.sort(key=lambda c: (-c["ranked"], c.get("id") or ""))
    return out[:k]


# Decision tiers (13 §2.3). `score` is the rarity-weighted whole-name
# similarity; facts are the record's identity vs the entity's.
SURE_NAME   = 95.0   # accept unless BOTH seat and year contradict
GOOD_NAME   = 85.0   # accept when no known fact contradicts
WEAK_NAME   = 70.0   # accept only when seat AND year agree (corroboration)


def decide(cands: list, floor: float = GOOD_NAME, margin: float = 5.0) -> tuple:
    """(decision, reason) for the ranked candidates of one record:
    decision 'same' (cands[0]) or 'open'. `floor` is the GOOD tier; the
    SURE and WEAK tiers sit at fixed distances above / below it. A match
    the tiers would accept is still held back when the runner-up is close
    both on the name and after the boosts (a boosted wrong candidate must
    not cloud a clear name)."""
    if not cands:
        return "open", "no candidate"
    best = cands[0]
    sc = best.get("score", 0.0)
    sa, ya = best.get("seat_agree"), best.get("year_agree")
    sure, weak = floor + (SURE_NAME - GOOD_NAME), floor - (GOOD_NAME - WEAK_NAME)
    if sc >= sure:
        if sa is False and ya is False:
            return "open", "sure name but seat and year both contradict"
        verdict, reason = "same", "sure name"
    elif sc >= floor:
        if sa is False or ya is False:
            return "open", "good name but a fact contradicts"
        verdict, reason = "same", "good name, no fact contradicts"
    elif sc >= weak:
        if not (sa and ya):
            return "open", "weak name, not corroborated"
        verdict, reason = "same", "weak name corroborated by seat and year"
    else:
        return "open", "name too different"
    if len(cands) > 1:
        gap_raw = sc - cands[1].get("score", 0.0)
        gap_ranked = best.get("ranked", sc) - cands[1].get("ranked", cands[1].get("score", 0.0))
        if gap_raw < margin and gap_ranked < margin:
            return "open", f"ambiguous: runner-up within {margin}"
    return verdict, reason


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
    """Hungarian name, else German, else the first other-language name (a
    firm printed only in Romanian, Serbian, Italian…), else the record's
    own text."""
    for o in ([ident.get("name_hu"), ident.get("name_de")]
              + [(x.get("name") if isinstance(x, dict) else x) for x in (ident.get("other_names") or [])]
              + [record_name]):
        if isinstance(o, str) and o.strip():
            return o.strip()
    return ""


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
