"""The model judge for open records (knowledge_base/13 §2.3).

Records the rules could not settle get a *suggestion* from a model, never
a decision: `shape["authority_suggestion"]` on the record's head fragment,
which a human confirms (then it becomes an ordinary resolution) or
dismisses. Two stages, each only on what it is good at:

  stage 1  a *decision model* (System One protocol, e.g. Microsoft-
           Decision-1 in Foundry): bounded question, probabilities out,
           text and facts only — the record's identity object against the
           top candidates' names and facts. Cheap and fast; confident on
           clear facts.
  stage 2  a *vision model* (chat completions with images): the record's
           crop and each candidate's crop side by side, plus the facts; a
           JSON verdict with a reason. Only for what stage 1 left hedged,
           and the only stage that can see through a wrong extraction.

Nothing here knows what a firm is; the question text states the user's
convention (13 §5.4: a changed name is a successor, not the same firm).
"""
from __future__ import annotations

import os

VERDICTS = ("same", "successor", "new", "unsure")
EDGE_TYPES = ("rename", "merger", "split", "legal_form", "successor")

SYSTEMONE_PATH = "/providers/microsoft/v1/systemone"
DEFAULT_THRESHOLD = 0.85

CONVENTION = ("Convention: two entries are the SAME firm when the printed name is "
              "unchanged apart from spelling, accents, language (Hungarian / German) or "
              "the legal-form suffix, and the seat and founding year do not contradict. "
              "A changed name (rename, merger, re-foundation, split) is a SUCCESSOR, "
              "a new legal entity continuing the old one. Different towns usually mean "
              "different firms even when the name type is the same.")


# ── stage 1: the decision model ─────────────────────────────────────────────

def decision_config() -> dict:
    """Endpoint / deployment / auth for the decision model, from the
    environment: DEDUST_DECISION_ENDPOINT (the Foundry resource URL),
    DEDUST_DECISION_MODEL (deployment name, default decision-1),
    DEDUST_DECISION_KEY (api-key; absent = Entra bearer via
    DefaultAzureCredential)."""
    ep = (os.environ.get("DEDUST_DECISION_ENDPOINT") or "").strip().rstrip("/")
    return {"endpoint": ep, "model": (os.environ.get("DEDUST_DECISION_MODEL") or "decision-1").strip(),
            "key": (os.environ.get("DEDUST_DECISION_KEY") or "").strip() or None}


def _bearer() -> str:
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token


def systemone(cfg: dict, state, questions: dict, timeout: float = 60.0) -> dict:
    """POST one System One request; returns the parsed answer document."""
    import requests
    if not cfg.get("endpoint"):
        raise RuntimeError("DEDUST_DECISION_ENDPOINT is not set (the Foundry resource URL)")
    headers = {"Content-Type": "application/json"}
    if cfg.get("key"):
        headers["api-key"] = cfg["key"]
    else:
        headers["Authorization"] = "Bearer " + _bearer()
    r = requests.post(cfg["endpoint"] + SYSTEMONE_PATH, headers=headers, timeout=timeout,
                      json={"model": cfg["model"], "state": state, "questions": questions})
    if r.status_code != 200:
        msg = r.text[:300]
        try:
            msg = (r.json().get("error") or {}).get("message") or msg
        except Exception:
            pass
        raise RuntimeError(f"decision model HTTP {r.status_code}: {msg}")
    return r.json()


def no_candidate_verdict() -> dict:
    """A record whose name resembles nothing in the authority: suggest a new
    entity without asking any model (a choice question needs two options)."""
    return {"verdict": "new", "entity_id": None, "edge_type": None, "confidence": 0.7,
            "reason": "no candidate in the authority: the name resembles no known entity"}


def _cand_brief(c: dict) -> dict:
    out = {"id": c.get("id"), "name": c.get("name")}
    if c.get("aliases"):
        out["other_names"] = c["aliases"][:4]
    f = c.get("facts") or {}
    for k in ("seat", "founded", "legal_form", "county"):
        if f.get(k) not in (None, ""):
            out[k] = f[k]
    ls = c.get("last_slice") or {}
    if ls.get("as_of"):
        out["seen_in"] = ls["as_of"]
    return out


def decision_request(ident: dict, record_name: str, cands: list, source_year=None) -> tuple:
    """(state, questions) for stage 1: match = one of the candidate ids or
    none; relation = same / successor; sure = 3-level score."""
    rec = {k: ident[k] for k in ("name_hu", "name_de", "other_names", "legal_form", "seat", "county",
                                 "founded", "capital_nominal", "serial_no") if ident.get(k) not in (None, "", [])}
    if not rec.get("name_hu") and not rec.get("name_de") and record_name:
        rec["name"] = record_name
    if source_year:
        rec["seen_in"] = source_year
    criteria = {c["id"]: _summ(_cand_brief(c)) for c in cands if c.get("id")}
    criteria["none"] = "none of the listed firms — a firm not in the list"
    questions = {
        "match": {"type": "choice",
                  "instructions": "Which earlier firm, if any, is the same firm as this record, or its direct predecessor? " + CONVENTION,
                  "criteria": criteria},
        "relation": {"type": "choice",
                     "instructions": "If the record matches a listed firm: is it the same firm (same name apart from spelling, language or legal form) or a successor (the name itself changed)? " + CONVENTION,
                     "criteria": {"same": "the same firm", "successor": "a successor: renamed, merged, re-founded or split"}},
        "sure": {"type": "score", "instructions": "How certain is the match answer?",
                 "criteria": ["a guess", "likely", "certain"]},
    }
    return {"record": rec, "candidates": [_cand_brief(c) for c in cands]}, questions


def _summ(b: dict) -> str:
    parts = [str(b.get("name") or "")]
    if b.get("other_names"):
        parts.append("also " + " / ".join(map(str, b["other_names"])))
    for k in ("seat", "county", "founded", "legal_form", "seen_in"):
        if b.get(k) not in (None, ""):
            parts.append(f"{k} {b[k]}")
    return "; ".join(p for p in parts if p)


def stage1_verdict(answer: dict, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Turn the decision model's answers into a verdict dict:
    {verdict, entity_id, confidence, reason, p}."""
    ans = (answer or {}).get("answers") or {}
    m = ans.get("match") or {}
    probs = m.get("probabilities") or {}
    choice = m.get("choice")
    p = float(probs.get(choice, 0.0)) if choice is not None else 0.0
    rel = ans.get("relation") or {}
    rp = rel.get("probabilities") or {}
    p_succ = float(rp.get("successor", 0.0))
    sure = ans.get("sure") or {}
    out = {"p": probs, "p_successor": p_succ, "confidence": round(p, 3),
           "sure": round(float(sure.get("score", 0.0)), 2) if sure else None}
    if choice is None:
        return {**out, "verdict": "unsure", "entity_id": None, "reason": "no answer"}
    if choice == "none":
        if p >= threshold:
            return {**out, "verdict": "new", "entity_id": None, "reason": f"none of the candidates (p={p:.2f})"}
        return {**out, "verdict": "unsure", "entity_id": None, "reason": f"leaning to none (p={p:.2f})"}
    if p >= threshold:
        v = "successor" if p_succ >= 0.5 else "same"
        return {**out, "verdict": v, "entity_id": choice,
                "reason": f"{choice} (p={p:.2f}); relation {v} (p={max(p_succ, 1 - p_succ):.2f})"}
    return {**out, "verdict": "unsure", "entity_id": choice, "reason": f"leaning to {choice} (p={p:.2f})"}


# ── stage 2: the vision model ───────────────────────────────────────────────

VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "entity_id", "edge_type", "confidence", "reason"],
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "entity_id": {"type": ["string", "null"], "description": "the matched candidate's id for same / successor; null otherwise"},
        "edge_type": {"type": ["string", "null"], "enum": list(EDGE_TYPES) + [None],
                      "description": "for successor: what happened"},
        "confidence": {"type": "number", "description": "0 to 1"},
        "reason": {"type": "string", "description": "one or two sentences citing what the images show"},
    },
}


def vision_messages(record_images: list, record_text: str, ident: dict, cands: list,
                    cand_images: dict, source_year=None) -> list:
    """One user message: the record's crop(s) + its extracted fields, then
    each candidate's crop + facts, then the question."""
    content = [{"type": "text", "text":
                "You are resolving an entry of a printed firm directory against entries of an "
                "earlier edition of the same directory. " + CONVENTION +
                " Answer with the JSON schema: verdict same / successor / new (none of the "
                "candidates) / unsure, the matched candidate's id, the successor edge type, "
                "a confidence from 0 to 1 and a short reason citing what the images show. "
                "The images are authoritative; the extracted fields may contain OCR errors."}]
    content.append({"type": "text", "text": f"--- THE RECORD{' (' + str(source_year) + ')' if source_year else ''}; extracted fields: "
                                            + _json(ident) + (f"; first line: {record_text}" if record_text else "")})
    for b64 in record_images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}})
    for c in cands:
        content.append({"type": "text", "text": f"--- CANDIDATE {c.get('id')}: " + _summ(_cand_brief(c))})
        b64 = cand_images.get(c.get("id"))
        if b64:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}})
        else:
            content.append({"type": "text", "text": "(no image available for this candidate)"})
    return [{"role": "user", "content": content}]


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def stage2_verdict(parsed: dict, cands: list) -> dict:
    ids = {c.get("id") for c in cands}
    v = parsed.get("verdict") if parsed.get("verdict") in VERDICTS else "unsure"
    eid = parsed.get("entity_id") if parsed.get("entity_id") in ids else None
    if v in ("same", "successor") and not eid:
        v = "unsure"
    et = parsed.get("edge_type") if parsed.get("edge_type") in EDGE_TYPES else None
    try:
        conf = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        conf = 0.0
    return {"verdict": v, "entity_id": eid, "edge_type": et if v == "successor" else None,
            "confidence": round(conf, 3), "reason": str(parsed.get("reason") or "")[:400]}


# ── the suggestion stored on the head fragment ──────────────────────────────

def suggestion(verdict: dict, stage: str, model: str, ts: str, entity_name=None) -> dict:
    return {"verdict": verdict["verdict"], "entity_id": verdict.get("entity_id"),
            "entity_name": entity_name, "edge_type": verdict.get("edge_type"),
            "confidence": verdict.get("confidence"), "reason": verdict.get("reason"),
            "stage": stage, "model": model, "ts": ts}


def summary(s) -> dict | None:
    if not isinstance(s, dict) or not s.get("verdict"):
        return None
    return {k: s.get(k) for k in ("verdict", "entity_id", "entity_name", "edge_type", "confidence", "reason", "stage", "model", "ts")}
