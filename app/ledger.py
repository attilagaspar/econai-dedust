"""Ledger parsers (knowledge_base/13 §6b): fields read from a row's printed
text without a model — the date of an entry, the issue it cites, a registry
position, the kind of event the verbs describe, the issue a page belongs to.

A dataset declaration refers to a parser by name (`record.derive`:
{"event_date": {"from": "order", "parser": "date_hu"}}); the engine stores
what the parser returns on the record (`derived`) and the record bridge
folds it into the identity object. The conventions here are those of
Hungarian official print of the period; another book adds its own parser
to PARSERS and names it in its declaration — nothing in the engine knows
them.
"""
from __future__ import annotations

import re

# Hungarian month names and the abbreviations the gazettes print (with the
# 19th-century spellings: martius, deczember), folded to ASCII lower case.
_MONTHS = {
    1: ("januar", "jan"), 2: ("februar", "febr", "feb"), 3: ("marczius", "martius", "marcius", "marcz", "marc", "mart"),
    4: ("aprilis", "apr"), 5: ("majus", "maj"), 6: ("junius", "jun"), 7: ("julius", "jul"),
    8: ("augusztus", "aug"), 9: ("szeptember", "september", "szept", "sept", "szep"), 10: ("oktober", "october", "okt", "oct"),
    11: ("november", "nov"), 12: ("deczember", "december", "decz", "dec"),
}
_MONTH_OF = {}
for _n, _names in _MONTHS.items():
    for _m in _names:
        _MONTH_OF[_m] = _n


def _fold(s) -> str:
    try:
        from unidecode import unidecode
        s = unidecode(str(s or ""))
    except ImportError:
        s = str(s or "")
    return s.lower()


def parse_date_hu(text) -> str | None:
    """'1876. jan. 5.' / '1876. évi január hó 5-én' / '1876 jun. 26' →
    '1876-01-05'; a year and month alone → '1876-01'; None when no year."""
    t = _fold(text).replace("\n", " ")
    m = re.search(r"(1[6-9]\d\d|20\d\d)\s*\.?\s*(?:evi\s+)?([a-z]{3,10})\.?\s*(?:ho\s*)?(\d{1,2})?", t)
    if not m:
        m2 = re.search(r"\b(1[6-9]\d\d|20\d\d)\b", t)
        return m2.group(1) if m2 else None
    year, mon, day = int(m.group(1)), m.group(2), m.group(3)
    mn = None
    for key, n in _MONTH_OF.items():
        if mon.startswith(key[:3]) and (len(key) <= 3 or mon.startswith(key) or key.startswith(mon)):
            mn = n
            break
    if mn is None:
        return str(year)
    if day and 1 <= int(day) <= 31:
        return f"{year:04d}-{mn:02d}-{int(day):02d}"
    return f"{year:04d}-{mn:02d}"


def parse_issue_ref(text) -> dict | None:
    """The earlier publication a note cites: '(Lásd az eredeti bejegyzést,
    14. szám.)' → {"issue": 14}; 'Lásd a II. évf. 34. számát' → {"issue": 34,
    "volume": "II"}; '(Lásd 1924. évf. 8.)' → {"issue": 8, "year": 1924};
    None when the text cites nothing."""
    t = _fold(text).replace("\n", " ")
    if not re.search(r"l\s*a\s*s\s*d", t):
        return None
    out = {}
    m = re.search(r"\b([ivxl]{1,5})\s*\.?\s*evf", t)
    if m:
        out["volume"] = m.group(1).upper()
    m = re.search(r"\b(1[6-9]\d\d|20\d\d)\s*\.?\s*(?:evi|evf)", t)
    if m:
        out["year"] = int(m.group(1))
    m = re.search(r"(\d{1,3})\s*\.?\s*(?:ik\s+)?sz\s*[aá]?\s*m", t) or re.search(r"evf\.?\s*(\d{1,3})\s*\.?\)", t)
    if not m:
        return None
    out["issue"] = int(m.group(1))
    return out


def parse_folio(text) -> dict | None:
    """A registry position printed as folio/sub-number ('32/2', '7/4'):
    {"folio": 32, "sub": 2} — the sub-number is the firm's event ordinal."""
    t = str(text or "")
    m = re.search(r"(?<![\d/])(\d{1,5})\s*/\s*(\d{1,3})(?![\d/])", t)
    return {"folio": int(m.group(1)), "sub": int(m.group(2))} if m else None


# verbs of the registry gazette, most specific first; the first family that
# matches names the event
_EVENT_VERBS = (
    ("deleted",     ("toroltetett", "kitoroltetett", "torlese", "kitorlese", "megszunt", "megszunese", "megszuntnek")),
    ("transferred", ("atvezettetett", "atvezetes", "athelyeztetett")),
    ("bankruptcy",  ("csod",)),
    ("dissolved",   ("feloszlott", "feloszlat", "felszamolas ala")),
    ("changed",     ("bejegyeztetett", "bejegyeztetik", "kilepett", "belepett", "modosit", "valtoz", "elhalalozas", "meghalt", "atalakult")),
)


def event_type(text) -> str:
    """What the verbs of a row say happened: deleted / transferred /
    bankruptcy / dissolved / changed, else 'registered' (a first entry
    carries no verb of change)."""
    t = _fold(text).replace("\n", " ")
    for kind, verbs in _EVENT_VERBS:
        if any(v in t for v in verbs):
            return kind
    return "registered"


def issue_of_pages(pages: list, header_labels=("document_header", "title")) -> dict:
    """{stem: issue number} for a volume: the masthead of an issue prints
    '… 14. szám' in a header cell on its first page; the pages that follow
    belong to the same issue until the next masthead. Pages before the
    first masthead get no issue."""
    out, cur = {}, None
    rx = re.compile(r"(\d{1,3})\s*[.,]?\s*sz\s*[aá]\s*m", re.I)
    for stem, data in pages:
        for sh in data.get("shapes") or []:
            if sh.get("label") not in header_labels:
                continue
            txt = (sh.get("pdf_text") or (sh.get("human_output") or {}).get("human_corrected_text")
                   or (sh.get("tesseract_output") or {}).get("ocr_text") or "")
            m = rx.search(txt.replace("\n", " "))
            if m:
                cur = int(m.group(1))
                break
        if cur is not None:
            out[stem] = cur
    return out


PARSERS = {
    "date_hu": parse_date_hu,
    "issue_ref": parse_issue_ref,
    "folio": parse_folio,
    "event_type": event_type,
}


def derive(values: dict, spec: dict) -> dict:
    """Apply a declaration's `record.derive` to a record's texts: {name:
    {"from": variable | [variables], "parser": name}} → {name: parsed}.
    Several source variables are joined with a newline before parsing."""
    out = {}
    for name, s in (spec or {}).items():
        if not isinstance(s, dict):
            continue
        fn = PARSERS.get(s.get("parser"))
        src = s.get("from")
        srcs = [src] if isinstance(src, str) else list(src or [])
        text = "\n".join(str(values.get(v) or "") for v in srcs)
        if fn is None or not text.strip():
            out[name] = None
            continue
        try:
            out[name] = fn(text)
        except Exception:
            out[name] = None
    return out
