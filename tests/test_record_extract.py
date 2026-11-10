"""Record-level structured extraction (knowledge_base/13 §1b, E1b):
POST /api/project/{name}/extract-records sends every fragment of a record
(crop image + attached page text, reading order) in ONE schema-constrained
call and stores the object on the head fragment as `structured` with a
`record` key. LLM stubbed; throwaway `_smoke_` project, deleted afterwards.
"""
import json
import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app import server as srv
from app.pipeline import create_project, PROJECTS_ROOT
from app import record_extract as rx

LABELS = ["firm", "name", "text"]
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["name", "seat", "founded"],
          "properties": {"name": {"type": "string"},
                         "seat": {"type": ["string", "null"]},
                         "founded": {"type": ["integer", "null"]}}}


def _sh(label, x1, y1, x2, y2, text=None, **extra):
    d = {"label": label, "points": [[x1, y1], [x2, y2]], "group_id": None,
         "shape_type": "rectangle", "flags": {}}
    if text is not None:
        d["tesseract_output"] = {"ocr_text": text}
    d.update(extra)
    return d


def _page(ann, stem, shapes, status="verified", w=1000, h=800):
    doc = {"version": "5.0.1", "flags": {"status": status}, "shapes": shapes,
           "imagePath": f"{stem}.jpg", "imageWidth": w, "imageHeight": h}
    ann.mkdir(parents=True, exist_ok=True)
    (ann / f"{stem}.json").write_text(json.dumps(doc), encoding="utf-8")
    Image.new("RGB", (w, h), "white").save(ann / f"{stem}.jpg")


def _rm(path):
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.3)


@pytest.fixture()
def project():
    name = f"_smoke_recx_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", LABELS)
    pd = PROJECTS_ROOT / name
    cfg = json.loads((pd / "config.json").read_text(encoding="utf-8"))
    cfg["record_label"] = "firm"; cfg["layout_columns"] = "1"
    (pd / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (pd / "schemas").mkdir(exist_ok=True)
    (pd / "schemas" / "identity.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    ann = pd / "annotations"
    # record 1: head on p1 (with a name child inside), continuation on p2
    # record 2: p2, single fragment, already carries the schema (edited)
    _page(ann, "p1", [
        _sh("firm", 100, 100, 900, 500, group_id=1, text="Pesti Hengermalom Rt.\nBudapest"),
        _sh("name", 110, 110, 890, 140, text="PESTI HENGERMALOM"),
    ])
    _page(ann, "p2", [
        _sh("firm", 100, 50, 900, 200, group_id=1, continuation=True, text="alapítva 1866"),
        _sh("firm", 100, 300, 900, 600, group_id=2,
            structured={"schema_name": "identity", "llm": {"name": "old"}, "data": {"name": "Hand"},
                        "edited": True, "model": "x", "ts": "t"}),
    ])
    _page(ann, "p3", [_sh("firm", 100, 50, 900, 200, group_id=3)], status="skip")
    try:
        yield name
    finally:
        _rm(pd)


class _FakeLLM:
    """Records every call; answers with an object derived from the prompt."""
    def __init__(self):
        self.calls = []

    def __call__(self, client, model, messages, max_out, schema, name, use_cache=False):
        self.calls.append({"model": model, "messages": messages, "schema": schema, "name": name})
        texts = [c["text"] for c in messages[0]["content"] if c["type"] == "text"]
        images = [c for c in messages[0]["content"] if c["type"] == "image_url"]
        obj = {"name": f"R{len(self.calls)}", "seat": "Budapest" if any("Budapest" in t for t in texts) else None,
               "founded": 1866 if any("1866" in t for t in texts) else None,
               "_n_images": len(images)}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(obj)))],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5), cached=False)


@pytest.fixture()
def fake_llm(monkeypatch):
    f = _FakeLLM()
    monkeypatch.setattr(srv, "_llm_complete_json", f)
    monkeypatch.setattr(srv, "_make_llm_client", lambda model: object())
    return f


def _post(client, name, **body):
    r = client.post(f"/api/project/{name}/extract-records", json={"schema_name": "identity", **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_dry_run_plans_records_and_skips_those_with_the_schema(client, project, fake_llm):
    d = _post(client, project, dry_run=True)
    assert d["n_records"] == 2 and d["n_planned"] == 1 and d["n_skipped"] == 1
    assert d["skipped"] == [{"id": 2, "reason": "has this schema"}]
    it = d["items"][0]
    assert it["id"] == 1 and it["n_fragments"] == 2 and it["n_images"] == 2 and it["has_text"] is True
    assert fake_llm.calls == []            # a dry run never calls the model


def test_extraction_sends_all_fragments_in_order_and_stores_on_the_head(client, project, fake_llm):
    d = _post(client, project, model="gpt-4o-mini")
    assert d["n_done"] == 1 and d["n_failed"] == 0 and d["tokens_in"] == 10
    call = fake_llm.calls[0]
    assert call["schema"] == SCHEMA and call["name"] == "identity"
    content = call["messages"][0]["content"]
    texts = [c["text"] for c in content if c["type"] == "text"]
    assert texts[0].startswith("You are given one record")        # default prompt
    assert any(t.startswith("--- fragment 1 of 2 (record head)") for t in texts)
    assert any(t.startswith("--- fragment 2 of 2 (continuation)") for t in texts)
    # attached text: the fragment's own OCR, then the child name shape inside it
    att = [t for t in texts if t.startswith("attached text:")]
    assert "Pesti Hengermalom Rt." in att[0] and "[name] PESTI HENGERMALOM" in att[0]
    assert "alapítva 1866" in att[1]
    assert sum(1 for c in content if c["type"] == "image_url") == 2
    # stored on the HEAD fragment (p1 #0), with the record key
    doc = json.loads((PROJECTS_ROOT / project / "annotations" / "p1.json").read_text(encoding="utf-8"))
    st = doc["shapes"][0]["structured"]
    assert st["schema_name"] == "identity" and st["data"]["seat"] == "Budapest" and st["data"]["founded"] == 1866
    assert st["record"] == {"group_id": 1, "fragments": [{"stem": "p1", "idx": 0}, {"stem": "p2", "idx": 0}],
                            "n_fragments": 2}
    assert st["edited"] is False
    # the continuation fragment carries nothing
    doc2 = json.loads((PROJECTS_ROOT / project / "annotations" / "p2.json").read_text(encoding="utf-8"))
    assert "structured" not in doc2["shapes"][0]
    # the record browser shows it
    r = client.get(f"/api/project/{project}/records").json()
    rec = next(x for x in r["records"] if x["id"] == 1)
    assert rec["structured"]["schema_name"] == "identity" and rec["structured"]["record"] is True
    assert rec["structured"]["data"]["founded"] == 1866


def test_overwrite_keeps_a_human_edited_object(client, project, fake_llm):
    d = _post(client, project, overwrite=True, record_ids=[2])
    assert d["n_planned"] == 1 and d["n_done"] == 1
    doc = json.loads((PROJECTS_ROOT / project / "annotations" / "p2.json").read_text(encoding="utf-8"))
    st = doc["shapes"][1]["structured"]
    assert st["data"] == {"name": "Hand"} and st["edited"] is True      # human copy survives
    assert st["llm"]["name"] == "R1" and st["record"]["group_id"] == 2  # model copy refreshed


def test_pattern_narrows_by_head_stem_and_max_fragments_caps_images(client, project, fake_llm):
    assert _post(client, project, pattern="^p2", dry_run=True)["n_planned"] == 0   # record 1's head is on p1
    assert _post(client, project, pattern="^p1", dry_run=True)["n_planned"] == 1
    d = _post(client, project, max_fragments=1)
    assert d["n_done"] == 1
    content = fake_llm.calls[0]["messages"][0]["content"]
    assert sum(1 for c in content if c["type"] == "image_url") == 1
    assert any("image omitted, text only" in c["text"] for c in content if c["type"] == "text")


def test_bad_schema_name_and_model_failure_are_reported(client, project, fake_llm, monkeypatch):
    r = client.post(f"/api/project/{project}/extract-records", json={"schema_name": "nope"})
    assert r.status_code == 404
    def boom(*a, **k):
        raise RuntimeError("quota")
    monkeypatch.setattr(srv, "_llm_complete_json", boom)
    d = _post(client, project)
    assert d["n_done"] == 0 and d["n_failed"] == 1 and "quota" in d["failures"][0]["error"]
    doc = json.loads((PROJECTS_ROOT / project / "annotations" / "p1.json").read_text(encoding="utf-8"))
    assert "structured" not in doc["shapes"][0]


def test_module_helpers():
    sh = {"row_struct": {"rows": [{"ocr": "a"}, {"human": "b", "ocr": "x"}]},
          "tesseract_output": {"ocr_text": "whole"}}
    assert rx._text_of(sh) == "a\nb"
    assert rx._text_of({"pdf_text": " p "}) == "p"
    msgs = rx.build_messages(None, [{"stem": "s", "idx": 0, "continuation": False, "text": "", "image_b64": None, "truncated": False}])
    assert msgs[0]["content"][0]["text"] == rx.DEFAULT_PROMPT.strip()
    assert rx.summary(None) is None
    assert rx.summary({"schema_name": "x", "llm": {"a": 1}})["data"] == {"a": 1}
