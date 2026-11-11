"""The entity view (13 §2.4, E5): projects linked through a shared record
authority, and the cross-source table — one row per entity, one column per
linked project by year, cells pointing at the record's head fragment, open
records listed apart. Throwaway authority dir + `_smoke_` projects."""
import json
import shutil
import time
import uuid

import pytest
from PIL import Image

from app import server as srv
from app import entities as ent
from app.pipeline import create_project, PROJECTS_ROOT

AUTH = "_smoke_link.authority.json"


def _sh(x1, y1, x2, y2, **e):
    d = {"label": "firm", "points": [[x1, y1], [x2, y2]], "group_id": None, "shape_type": "rectangle", "flags": {}}
    d.update(e); return d


def _mark(eid, outcome="same", source="auto"):
    return {"id": eid, "name": "n", "type": "firm", "source": source, "outcome": outcome, "record": {"group_id": 0}}


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
def linked(tmp_path, monkeypatch):
    d = tmp_path / "authorities"; d.mkdir()
    tag = uuid.uuid4().hex[:6]
    p77, p82, p_other = f"_smoke_lnk77_{tag}", f"_smoke_lnk82_{tag}", f"_smoke_lnkx_{tag}"
    (d / AUTH).write_text(json.dumps({
        "authority": "_smoke_link", "version": "0.3", "entity_types": ["firm"], "id_scheme": {"prefix": "F", "width": 6},
        "slices_present": [1877, 1882], "counts": {}, "edges": [
            {"from": "F000002", "to": "F000003", "type": "rename", "as_of": 1882, "source": p82},
            {"from": "F000004", "to": "F000001", "type": "duplicate_of", "as_of": None, "source": "econai"}],
        "entities": [
            {"id": "F000001", "name": "Alpha bank", "type": "firm", "aliases": [{"name": "Alfa-Bank", "lang": "de"}], "xref": {},
             "attrs": {"seat": "Pest", "founded": 1870},
             "slices": [{"as_of": 1877, "source": p77, "name": "Alpha bank", "provenance": {"project": p77, "stem": "a", "group_id": 1}, "seq": 1},
                        {"as_of": 1882, "source": p82, "name": "Alpha bank r.-t.", "provenance": {"project": p82, "stem": "b", "group_id": 1}, "seq": 3}]},
            {"id": "F000002", "name": "Beta mill", "type": "firm", "aliases": [], "xref": {}, "attrs": {},
             "slices": [{"as_of": 1877, "source": p77, "name": "Beta mill", "provenance": {"project": p77, "stem": "a", "group_id": 2}}]},
            {"id": "F000003", "name": "Gamma mill", "type": "firm", "aliases": [], "xref": {}, "attrs": {},
             "slices": [{"as_of": 1882, "source": p82, "name": "Gamma mill", "provenance": {"project": p82, "stem": "b", "group_id": 2}}]},
            {"id": "F000004", "name": "Alpha bank (dup)", "type": "firm", "aliases": [], "xref": {}, "attrs": {}, "slices": []},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(srv, "AUTHORITIES_DIR", d)
    srv._AUTH_CACHE.clear(); srv._AUTH_META_CACHE.clear(); ent._CACHE.clear()
    for name, year, link in ((p77, 1877, True), (p82, 1882, True), (p_other, None, False)):
        create_project(name, "A", ["firm"])
        pd = PROJECTS_ROOT / name
        cfg = json.loads((pd / "config.json").read_text(encoding="utf-8"))
        cfg.update({"record_label": "firm", "layout_columns": "1"})
        if link:
            cfg.update({"record_authority": AUTH, "source_year": year})
        (pd / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    _page(PROJECTS_ROOT / p77 / "annotations", "a", [
        _sh(100, 100, 900, 300, group_id=1, authority=_mark("F000001", "new"), tesseract_output={"ocr_text": "Alpha bank"}),
        _sh(100, 350, 900, 500, group_id=2, authority=_mark("F000002", "new"), tesseract_output={"ocr_text": "Beta mill"}),
    ])
    _page(PROJECTS_ROOT / p82 / "annotations", "b", [
        _sh(100, 100, 900, 300, group_id=1, authority=_mark("F000001", "same", "human"),
            structured={"schema_name": "identity", "data": {"name_hu": "Alpha bank r.-t."}, "llm": {}, "edited": False, "model": "m", "ts": "t"}),
        _sh(100, 350, 900, 500, group_id=2, authority=_mark("F000003", "successor", "human"), tesseract_output={"ocr_text": "Gamma mill"}),
        _sh(100, 550, 900, 700, group_id=3, tesseract_output={"ocr_text": "Delta new firm"}),            # open
    ])
    _page(PROJECTS_ROOT / p_other / "annotations", "c", [_sh(100, 100, 900, 300, group_id=1)])
    try:
        yield p77, p82, p_other
    finally:
        for n in (p77, p82, p_other):
            _rm(PROJECTS_ROOT / n)


def test_groups_list_linked_projects_by_year(client, linked):
    p77, p82, p_other = linked
    g = next(x for x in client.get("/api/entities/groups").json()["groups"] if x["file"] == AUTH)
    assert g["authority"] == "_smoke_link" and g["n_entities"] == 4 and g["n_edges"] == 2
    assert [p["name"] for p in g["projects"]] == [p77, p82]            # by year; the unlinked project is absent
    a, b = g["projects"]
    assert (a["source_year"], a["n_records"], a["n_resolved"], a["n_open"]) == (1877, 2, 2, 0)
    assert (b["source_year"], b["n_records"], b["n_resolved"], b["n_open"]) == (1882, 3, 2, 1)
    assert p_other not in [p["name"] for p in g["projects"]]


def test_table_rows_cells_edges_and_open_records(client, linked):
    p77, p82, _ = linked
    d = client.get("/api/entities/table", params={"authority": AUTH}).json()
    assert [c["name"] for c in d["columns"]] == [p77, p82]
    assert d["columns"][0]["folder"].endswith("annotations") and d["columns"][1]["n_open"] == 1
    assert d["n_entities"] == 4 and d["n_rows"] == 4
    rows = {r["id"]: r for r in d["rows"]}
    alpha = rows["F000001"]
    assert alpha["aliases"] == ["Alfa-Bank"] and alpha["facts"] == {"seat": "Pest", "founded": 1870}
    c77, c82 = alpha["cells"][p77][0], alpha["cells"][p82][0]
    assert (c77["stem"], c77["idx"], c77["group_id"], c77["outcome"], c77["source"]) == ("a", 0, 1, "new", "auto")
    assert (c82["stem"], c82["idx"], c82["name"], c82["outcome"], c82["source"], c82["seq"]) == ("b", 0, "Alpha bank r.-t.", "same", "human", 3)
    assert alpha["canonical"] == "F000001"
    # the duplicate collapses to Alpha and carries the edge
    assert rows["F000004"]["canonical"] == "F000001"
    assert rows["F000004"]["edges"][0]["type"] == "duplicate_of" and rows["F000004"]["edges"][0]["other_name"] == "Alpha bank"
    # the rename chain shows on both ends
    assert rows["F000002"]["edges"][0] == {"from": "F000002", "to": "F000003", "type": "rename", "as_of": 1882, "other_name": "Gamma mill"}
    assert rows["F000003"]["edges"][0]["other_name"] == "Beta mill"
    assert p82 not in rows["F000002"]["cells"] and p77 not in rows["F000003"]["cells"]
    # the open record of 1882
    assert d["unresolved"][p77] == []
    assert d["unresolved"][p82] == [{"group_id": 3, "stem": "b", "idx": 2, "name": "Delta new firm", "n_fragments": 1}]


def test_table_filters_and_search(client, linked):
    p77, p82, _ = linked
    get = lambda **kw: [r["id"] for r in client.get("/api/entities/table", params={"authority": AUTH, **kw}).json()["rows"]]
    assert get(q="alpha") == ["F000001", "F000004"]
    assert get(q="ALFA bank") == ["F000001"]               # alias, folded (hyphen dropped)
    assert get(q="F000003") == ["F000003"]
    assert get(filter="missing") == ["F000002", "F000003", "F000004"]
    assert get(filter="single") == ["F000002", "F000003"]
    assert get(filter="chain") == ["F000001", "F000002", "F000003", "F000004"]
    assert get(filter="dup") == ["F000001", "F000004"]
    assert get(limit=1) == ["F000001"] and get(limit=1, offset=1) == ["F000002"]
    assert client.get("/api/entities/table", params={"authority": "nope.authority.json"}).status_code == 404


def test_heads_cache_follows_the_folder(client, linked):
    p77, p82, _ = linked
    d1 = client.get("/api/entities/table", params={"authority": AUTH}).json()
    assert d1["unresolved"][p82][0]["group_id"] == 3
    jf = PROJECTS_ROOT / p82 / "annotations" / "b.json"
    doc = json.loads(jf.read_text(encoding="utf-8"))
    doc["shapes"][2]["authority"] = _mark("F000004", "same", "human")
    time.sleep(0.05); jf.write_text(json.dumps(doc), encoding="utf-8")
    d2 = client.get("/api/entities/table", params={"authority": AUTH}).json()
    assert d2["unresolved"][p82] == [] and d2["columns"][1]["n_open"] == 0
