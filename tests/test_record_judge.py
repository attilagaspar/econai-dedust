"""The model judge for open records (13 §2.3): stage 1 (decision model on
facts, stubbed HTTP) → stage 2 (vision model on crops, stubbed) →
suggestions on the head fragment; accept turns them into decisions,
dismiss drops them. Throwaway authority dir + `_smoke_` project."""
import json
import shutil
import time
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image

from app import server as srv
from app import record_judge as rj
from app.pipeline import create_project, PROJECTS_ROOT

AUTH = "_smoke_judge.authority.json"


def _sh(x1, y1, x2, y2, **e):
    d = {"label": "firm", "points": [[x1, y1], [x2, y2]], "group_id": None, "shape_type": "rectangle", "flags": {}}
    d.update(e); return d


def _st(**ident):
    return {"schema_name": "identity", "llm": ident, "data": ident, "edited": False, "model": "m", "ts": "t"}


def _page(ann, stem, shapes):
    ann.mkdir(parents=True, exist_ok=True)
    (ann / f"{stem}.json").write_text(json.dumps({"version": "5.0.1", "flags": {"status": "verified"}, "shapes": shapes,
                                                  "imagePath": f"{stem}.jpg", "imageWidth": 1000, "imageHeight": 800}), encoding="utf-8")
    Image.new("RGB", (1000, 800), "white").save(ann / f"{stem}.jpg")


def _rm(path):
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.3)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    d = tmp_path / "authorities"; d.mkdir()
    tag = uuid.uuid4().hex[:6]
    p77 = f"_smoke_jdg77_{tag}"; name = f"_smoke_jdg82_{tag}"
    (d / AUTH).write_text(json.dumps({
        "authority": "_smoke_judge", "version": "0.3", "entity_types": ["firm"], "id_scheme": {"prefix": "F", "width": 6},
        "normalize": {"strip_tokens": ["részvénytársaság"]}, "slices_present": [1877], "counts": {}, "edges": [],
        "entities": [
            {"id": "F000001", "name": "Cservenkai előleg- és hitel-egylet", "type": "firm", "aliases": [], "xref": {},
             "attrs": {"seat": "Cservenka", "founded": 1870},
             "slices": [{"as_of": 1877, "source": p77, "name": "Cservenkai előleg- és hitel-egylet", "attrs": {"seat": "Cservenka", "founded": 1870},
                         "provenance": {"project": p77, "stem": "a", "group_id": 1}}]},
            {"id": "F000002", "name": "Pesti Hengermalom", "type": "firm", "aliases": [], "xref": {}, "attrs": {"seat": "Budapest", "founded": 1839},
             "slices": [{"as_of": 1877, "source": p77, "name": "Pesti Hengermalom", "attrs": {"seat": "Budapest", "founded": 1839},
                         "provenance": {"project": p77, "stem": "a", "group_id": 2}}]},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(srv, "AUTHORITIES_DIR", d)
    srv._AUTH_CACHE.clear(); srv._AUTH_META_CACHE.clear()
    from app import entities as ent; ent._CACHE.clear()
    for n, yr in ((p77, 1877), (name, 1882)):
        create_project(n, "A", ["firm"]); pd = PROJECTS_ROOT / n
        cfg = json.loads((pd / "config.json").read_text(encoding="utf-8"))
        cfg.update({"record_label": "firm", "layout_columns": "1", "record_authority": AUTH, "source_year": yr})
        (pd / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    _page(PROJECTS_ROOT / p77 / "annotations", "a", [
        _sh(100, 100, 900, 300, group_id=1, authority={"id": "F000001", "source": "auto", "outcome": "new", "record": {"group_id": 1}}),
        _sh(100, 350, 900, 550, group_id=2, authority={"id": "F000002", "source": "auto", "outcome": "new", "record": {"group_id": 2}})])
    _page(PROJECTS_ROOT / name / "annotations", "b", [
        # 1: Cservenka again, founding year differs → open after the batch (good name, a fact contradicts)
        _sh(100, 100, 900, 300, group_id=1, structured=_st(name_hu="Cservenkai előleg- és hitelegylet", seat="Cservenka", founded=1876)),
        # 2: renamed mill → open
        _sh(100, 350, 900, 550, group_id=2, structured=_st(name_hu="Budapesti Hengermalom", seat="Budapest", founded=1839)),
        # 3: resolved already → never judged
        _sh(100, 600, 900, 750, group_id=3, authority={"id": "F000002", "source": "human", "outcome": "same", "record": {"group_id": 3}},
            structured=_st(name_hu="x")),
    ])
    try:
        yield name
    finally:
        _rm(PROJECTS_ROOT / p77); _rm(PROJECTS_ROOT / name)


class _FakeDecision:
    """requests.post stand-in: answers from a script keyed on the record name."""
    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, url, headers=None, timeout=None, json=None):
        self.calls.append({"url": url, "headers": headers, "body": json})
        name = (json["state"]["record"].get("name_hu") or "")
        probs, rel = self.script.get(name, ({"none": 1.0}, 0.0))
        choice = max(probs, key=probs.get)
        return SimpleNamespace(status_code=200, text="", json=lambda: {
            "model": "microsoft-decision-1",
            "answers": {"match": {"type": "choice", "choice": choice, "probabilities": probs, "confidence": probs[choice]},
                        "relation": {"type": "choice", "choice": "successor" if rel >= 0.5 else "same",
                                     "probabilities": {"same": 1 - rel, "successor": rel}},
                        "sure": {"type": "score", "score": 1.8, "probabilities": {}}},
            "usage": {"input_tokens": 200, "output_tokens": 2}})


def test_stage1_verdict_mapping():
    def ans(choice, p, succ=0.1):
        other = "none" if choice != "none" else "F000001"
        return {"answers": {"match": {"choice": choice, "probabilities": {choice: p, other: 1 - p}},
                            "relation": {"probabilities": {"same": 1 - succ, "successor": succ}},
                            "sure": {"score": 1.5}}}
    assert rj.stage1_verdict(ans("F000001", 0.96))["verdict"] == "same"
    assert rj.stage1_verdict(ans("F000001", 0.96, succ=0.7))["verdict"] == "successor"
    assert rj.stage1_verdict(ans("F000001", 0.6))["verdict"] == "unsure"
    assert rj.stage1_verdict(ans("none", 0.9))["verdict"] == "new"
    assert rj.stage1_verdict(ans("none", 0.6))["verdict"] == "unsure"
    assert rj.stage1_verdict({})["verdict"] == "unsure"
    state, q = rj.decision_request({"name_hu": "A", "seat": "S", "founded": 1870}, "", [{"id": "F1", "name": "A", "facts": {"seat": "S"}}], 1882)
    assert state["record"]["seen_in"] == 1882 and set(q["match"]["criteria"]) == {"F1", "none"}
    assert q["match"]["type"] == "choice" and q["sure"]["type"] == "score"


def test_judge_two_stages_then_accept_and_dismiss(client, project, monkeypatch):
    monkeypatch.setenv("DEDUST_DECISION_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("DEDUST_DECISION_KEY", "k")
    fake = _FakeDecision({"Cservenkai előleg- és hitelegylet": ({"F000001": 0.97, "none": 0.03}, 0.1),
                          "Budapesti Hengermalom": ({"F000002": 0.55, "none": 0.45}, 0.6)})     # hedged → stage 2
    import requests
    monkeypatch.setattr(requests, "post", fake)
    vision_calls = []
    def fake_vision(client_, model, messages, max_out, schema, name_, use_cache=False):
        vision_calls.append(messages)
        obj = {"verdict": "successor", "entity_id": "F000002", "edge_type": "rename", "confidence": 0.91,
               "reason": "same mill, the name gained the Budapest prefix after the city merger"}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(obj)))],
                               usage=SimpleNamespace(prompt_tokens=900, completion_tokens=40), cached=False)
    monkeypatch.setattr(srv, "_llm_complete_json", fake_vision)
    monkeypatch.setattr(srv, "_make_llm_client", lambda m: object())

    dry = client.post(f"/api/project/{project}/judge-records", json={"dry_run": True}).json()
    assert [i["id"] for i in dry["items"]] == [1, 2] and dry["counts"]["skipped"] == 1
    assert dry["items"][0]["candidates"][0]["id"] == "F000001"
    assert fake.calls == [] and vision_calls == []

    d = client.post(f"/api/project/{project}/judge-records", json={"threshold": 0.85, "vision_model": "gpt-4.1-mini"}).json()
    assert d["counts"] == {"suggested": 2, "unsure": 0, "kept": 0, "skipped": 1, "error": 0}
    assert len(fake.calls) == 2 and fake.calls[0]["headers"]["api-key"] == "k"
    assert fake.calls[0]["url"].endswith("/providers/microsoft/v1/systemone")
    assert "Convention" in fake.calls[0]["body"]["questions"]["match"]["instructions"]
    assert len(vision_calls) == 1                                   # only the hedged record reached stage 2
    content = vision_calls[0][0]["content"]
    assert sum(1 for c in content if c["type"] == "image_url") >= 2  # the record's crop and the candidate's
    by = {i["id"]: i for i in d["items"]}
    assert by[1]["suggestion"]["verdict"] == "same" and by[1]["suggestion"]["stage"] == "decision" and by[1]["suggestion"]["entity_id"] == "F000001"
    assert by[2]["suggestion"]["verdict"] == "successor" and by[2]["suggestion"]["stage"] == "vision" and by[2]["suggestion"]["edge_type"] == "rename"
    doc = json.loads((PROJECTS_ROOT / project / "annotations" / "b.json").read_text(encoding="utf-8"))
    assert doc["shapes"][0]["authority_suggestion"]["confidence"] == 0.97 and "authority" not in doc["shapes"][0]
    # the record browser and the entity view show the suggestions
    recs = {r["id"]: r for r in client.get(f"/api/project/{project}/records").json()["records"]}
    assert recs[1]["suggestion"]["verdict"] == "same" and recs[3]["suggestion"] is None
    tbl = client.get("/api/entities/table", params={"authority": AUTH}).json()
    assert tbl["unresolved"][project][1]["suggestion"]["verdict"] == "successor"
    # a second judge run keeps existing suggestions
    d2 = client.post(f"/api/project/{project}/judge-records", json={}).json()
    assert d2["counts"]["kept"] == 2 and len(fake.calls) == 2

    # accept ≥ 0.95: record 1 only; record 2 stays a suggestion
    a = client.post(f"/api/project/{project}/records/accept-suggestions", json={"min_confidence": 0.95}).json()
    assert a["counts"]["accepted"] == 1 and a["counts"]["below"] == 1
    doc = json.loads((PROJECTS_ROOT / project / "annotations" / "b.json").read_text(encoding="utf-8"))
    m = doc["shapes"][0]["authority"]
    assert m["id"] == "F000001" and m["outcome"] == "same" and m["source"] == "human" and m["judge"]["stage"] == "decision"
    assert "authority_suggestion" not in doc["shapes"][0]
    auth = json.loads((srv.AUTHORITIES_DIR / AUTH).read_text(encoding="utf-8"))
    e1 = next(e for e in auth["entities"] if e["id"] == "F000001")
    assert any(s.get("source") == project for s in e1["slices"])
    # accept the rest: the successor mints and links
    a2 = client.post(f"/api/project/{project}/records/accept-suggestions", json={"min_confidence": 0.9}).json()
    assert a2["counts"]["accepted"] == 1
    auth = json.loads((srv.AUTHORITIES_DIR / AUTH).read_text(encoding="utf-8"))
    assert auth["edges"][0]["type"] == "rename" and auth["edges"][0]["from"] == "F000002" and len(auth["entities"]) == 3
    # dismiss on a fresh suggestion
    doc = json.loads((PROJECTS_ROOT / project / "annotations" / "b.json").read_text(encoding="utf-8"))
    doc["shapes"][1].pop("authority"); doc["shapes"][1]["authority_suggestion"] = {"verdict": "new", "confidence": 0.5, "stage": "decision"}
    (PROJECTS_ROOT / project / "annotations" / "b.json").write_text(json.dumps(doc), encoding="utf-8")
    x = client.post(f"/api/project/{project}/records/accept-suggestions", json={"dismiss": True}).json()
    assert x["counts"]["dismissed"] == 1
    doc = json.loads((PROJECTS_ROOT / project / "annotations" / "b.json").read_text(encoding="utf-8"))
    assert "authority_suggestion" not in doc["shapes"][1]


def test_judge_refuses_without_the_decision_endpoint(client, project, monkeypatch):
    monkeypatch.delenv("DEDUST_DECISION_ENDPOINT", raising=False)
    r = client.post(f"/api/project/{project}/judge-records", json={"stage1": True})
    assert r.status_code == 400 and "DEDUST_DECISION_ENDPOINT" in r.text
    assert client.post(f"/api/project/{project}/judge-records", json={"stage1": True, "dry_run": True}).status_code == 200


def test_record_without_candidates_is_suggested_new_without_a_call(client, project, monkeypatch):
    monkeypatch.setenv("DEDUST_DECISION_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("DEDUST_DECISION_KEY", "k")
    import requests
    calls = []
    def boom(*a, **k):
        calls.append(1); raise AssertionError("the decision model must not be called without candidates")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(srv, "_make_llm_client", lambda m: object())
    ann = PROJECTS_ROOT / project / "annotations"
    _page(ann, "c", [_sh(100, 100, 900, 300, group_id=9, structured=_st(name_hu="Zzzz Qqqq Wwww", seat="Nowhere"))])
    d = client.post(f"/api/project/{project}/judge-records", json={"record_ids": [9], "stage2": False}).json()
    assert d["counts"]["suggested"] == 1 and d["counts"]["error"] == 0 and calls == []
    sug = d["items"][0]["suggestion"]
    assert sug["verdict"] == "new" and sug["stage"] == "rules" and sug["confidence"] == 0.7
