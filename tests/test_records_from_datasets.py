"""Records from lattice rows (knowledge_base/13 §6): a project whose config
names dataset declarations in `record_datasets` gets one record per lattice
row — the head fragment is the key cell, the identity object comes from the
declaration's identity map — and the record list, the candidates, a
decision, the entity view and the browser context work on them; the sweep
is off. Throwaway authority dir + `_smoke_` project."""
import json
import shutil
import time
import uuid

import pytest
from PIL import Image

from app import server as srv
from app import entities as ent
from app import record_resolve as rr
from app.pipeline import create_project, PROJECTS_ROOT

AUTH = "_smoke_rows.authority.json"
DS = "egyeni"


def _cell(sr, sc, text, label="table_cell", pdf=False):
    sh = {"label": label, "points": [[sc * 100, sr * 60], [sc * 100 + 90, sr * 60 + 50]],
          "shape_type": "rectangle", "flags": {}, "group_id": None,
          "super_row": sr, "super_column": sc, "table": 0}
    if text and pdf:
        sh["pdf_text"] = text
    elif text:
        sh["human_output"] = {"human_corrected_text": text}
    return sh


DECL = {
    "name": DS, "version": 1,
    "scope": {"labels": ["table_cell"], "pattern": "1", "tables": [0]},
    "record": {"unit": "lattice_row", "key": {"slot": 1, "column": 3, "dtype": "text"},
               "identity": {"name_hu": "name", "seat": "seat", "founded": "founded"}},
    "variables": [{"name": "court", "column": 1}, {"name": "date", "column": 2},
                  {"name": "name", "column": 3}, {"name": "seat", "column": 4},
                  {"name": "founded", "column": 5}],
}


def _rm(path):
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.3)


@pytest.fixture()
def rows_project(tmp_path, monkeypatch):
    d = tmp_path / "authorities"; d.mkdir()
    (d / AUTH).write_text(json.dumps({
        "authority": "_smoke_rows", "version": "0.3", "entity_types": ["firm"],
        "id_scheme": {"prefix": "F", "width": 6},
        "normalize": {"strip_tokens": ["részvénytársaság", "r.-t."]},
        "slices_present": [], "counts": {}, "edges": [], "entities": [
            {"id": "F000001", "name": "Galántai takarékpénztár", "type": "firm", "aliases": [], "xref": {},
             "attrs": {"seat": "Galánta", "founded": 1873},
             "slices": [{"as_of": 1875, "source": "other_1875", "type": "firm", "name": "Galántai takarékpénztár",
                         "provenance": {"project": "other_1875", "stem": "x", "group_id": 1}}]},
            {"id": "F000002", "name": "Galántai népbank", "type": "firm", "aliases": [], "xref": {},
             "attrs": {"seat": "Galánta", "founded": 1869}, "slices": []},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(srv, "AUTHORITIES_DIR", d)
    srv._AUTH_CACHE.clear(); srv._AUTH_META_CACHE.clear(); ent._CACHE.clear()
    name = f"_smoke_rows_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", ["table_cell", "table_header"])
    pd = PROJECTS_ROOT / name
    cfg = json.loads((pd / "config.json").read_text(encoding="utf-8"))
    cfg.update({"record_authority": AUTH, "source_year": 1876, "record_datasets": [DS]})
    (pd / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (pd / "datasets").mkdir(exist_ok=True)
    (pd / "datasets" / f"{DS}.dataset.json").write_text(json.dumps(DECL), encoding="utf-8")
    ann = pd / "annotations"; ann.mkdir(parents=True, exist_ok=True)
    shapes = [_cell(1, c, h, label="table_header") for c, h in
              enumerate(["Királyi törvényszék", "kelte", "A czég szövege", "A főtelep", "alapítva"], start=1)]
    shapes += [_cell(2, 1, "Pozsonyi törvényszék"), _cell(2, 2, "1876. jun. 26."),
               _cell(2, 3, "G a l á n t a i  t a k a r é k p é n z t á r", pdf=True),   # the PDF layer prints it spaced
               _cell(2, 4, "Galánta"), _cell(2, 5, "1873")]
    shapes += [_cell(3, 1, "„"), _cell(3, 2, "1876. jul. 3."),
               _cell(3, 3, "Promontori önsegélyző-egylet"), _cell(3, 4, "Promontor"), _cell(3, 5, "")]
    (ann / "p1.json").write_text(json.dumps({"version": "5.0.1", "flags": {}, "shapes": shapes,
                                             "imagePath": "p1.jpg", "imageWidth": 700, "imageHeight": 300}),
                                 encoding="utf-8")
    Image.new("RGB", (700, 300), "white").save(ann / "p1.jpg")
    try:
        yield name, d
    finally:
        _rm(pd)


def _shape(project, idx):
    return json.loads((PROJECTS_ROOT / project / "annotations" / "p1.json").read_text(encoding="utf-8"))["shapes"][idx]


def test_rows_are_records_with_identity_from_the_declaration(client, rows_project):
    name, _ = rows_project
    d = client.get(f"/api/project/{name}/records").json()
    assert d["record_source"] == "dataset" and d["record_datasets"] == [DS] and d["record_label"] is None
    assert len(d["records"]) == 2 and d["unswept"] == []
    a, b = d["records"]
    assert a["id"] < b["id"] and a["dataset"] == DS and a["page_span"] == ["p1", "p1"]
    assert a["name"] == "Galántai takarékpénztár"                     # letter-spaced PDF text collapsed
    assert a["values"] == {"court": "Pozsonyi törvényszék", "date": "1876. jun. 26.",
                           "name": "G a l á n t a i  t a k a r é k p é n z t á r", "seat": "Galánta", "founded": "1873"}
    assert [f["idx"] for f in a["fragments"]] == [7, 5, 6, 8, 9]       # head = the key (name) cell, then the others
    assert a["n_fragments"] == 5 and a["structured"] is None and a["authority"] is None
    assert b["name"] == "Promontori önsegélyző-egylet" and b["values"]["court"] == "„"
    ctx = client.get("/api/records/context", params={"folder": str(PROJECTS_ROOT / name / "annotations")}).json()
    assert ctx["record_datasets"] == [DS] and ctx["record_label"] is None


def test_candidates_decision_and_entity_view_work_on_row_records(client, rows_project):
    name, auth_dir = rows_project
    recs = client.get(f"/api/project/{name}/records").json()["records"]
    gid = recs[0]["id"]
    c = client.get(f"/api/project/{name}/records/candidates", params={"gid": gid}).json()
    assert c["identity"] == {"name_hu": "Galántai takarékpénztár", "seat": "Galánta", "founded": 1873}
    assert c["queries"][0][0] == "Galántai takarékpénztár"
    top = c["candidates"][0]
    assert top["id"] == "F000001" and set(top["boost_why"]) >= {"seat", "founded"}
    assert c["auto"]["verdict"] == "same"
    # the decision marks the KEY cell and writes the slice with the row's identity
    r = client.post(f"/api/project/{name}/records/resolve",
                    json={"gid": gid, "outcome": "same", "entity_id": "F000001", "source": "human"})
    assert r.status_code == 200, r.text
    mark = _shape(name, 7)["authority"]
    assert mark["id"] == "F000001" and mark["outcome"] == "same" and mark["source"] == "human"
    assert "authority" not in _shape(name, 5)                           # not on another cell of the row
    auth = json.loads((auth_dir / AUTH).read_text(encoding="utf-8"))
    sl = next(e for e in auth["entities"] if e["id"] == "F000001")["slices"][-1]
    assert sl["as_of"] == 1876 and sl["source"] == name and sl["name"] == "Galántai takarékpénztár"
    assert sl["provenance"] == {"project": name, "stem": "p1", "group_id": gid} and sl["attrs"]["seat"] == "Galánta"
    # the record list and the entity view see the mark
    recs = client.get(f"/api/project/{name}/records").json()
    assert recs["n_resolved"] == 1 and recs["records"][0]["authority"]["id"] == "F000001"
    t = client.get("/api/entities/table", params={"authority": AUTH}).json()
    col = next(x for x in t["columns"] if x["name"] == name)
    assert col["n_records"] == 2 and col["n_open"] == 1 and col["record_label"] == DS
    row = next(x for x in t["rows"] if x["id"] == "F000001")
    cell = row["cells"][name][0]
    assert (cell["stem"], cell["idx"], cell["group_id"], cell["live"], cell["name"]) == ("p1", 7, gid, True, "Galántai takarékpénztár")
    assert [o["name"] for o in t["unresolved"][name]] == ["Promontori önsegélyző-egylet"]
    # the same-source rule still holds: the other row cannot be offered F000001 now
    gid2 = recs["records"][1]["id"]
    c2 = client.get(f"/api/project/{name}/records/candidates", params={"gid": gid2, "q": "Galánta"}).json()
    assert [x["id"] for x in c2["candidates"]] == ["F000002"]


def test_sweep_is_off_and_config_validates_the_dataset_names(client, rows_project):
    name, _ = rows_project
    r = client.post(f"/api/project/{name}/sweep-records", json={})
    assert r.status_code == 400 and "dataset" in r.json()["detail"]
    assert client.patch(f"/api/project/{name}/config", json={"record_datasets": ["nope"]}).status_code == 400
    r = client.patch(f"/api/project/{name}/config", json={"record_datasets": []})
    assert r.status_code == 200 and "record_datasets" not in r.json()["config"]
    r = client.patch(f"/api/project/{name}/config", json={"record_datasets": [DS]})
    assert r.status_code == 200 and r.json()["config"]["record_datasets"] == [DS]


def test_declaration_identity_is_validated_and_unspace_only_touches_spaced_print():
    decl = srv.DatasetDecl(**{**DECL, "record": {**DECL["record"], "unit": "internal_row",
                                                  "identity": {"name_hu": "name", "colour": "name", "seat": "nope"}}})
    probs = srv._ds_decl_problems(decl)
    assert any("lattice_row" in p for p in probs)
    assert any("'colour'" in p for p in probs) and any("'nope'" in p for p in probs)
    assert srv._ds_decl_problems(srv.DatasetDecl(**DECL)) == []
    assert rr.unspace("Z i n n e r  M ó r i t z") == "Zinner Móritz"
    assert rr.unspace("K u s n i t z k y  O s k á r\nO s k a r  K u s n i t z k y") == "Kusnitzky Oskár Oskar Kusnitzky"
    assert rr.unspace("1876. jun. 26.") == "1876. jun. 26."
    assert rr.unspace("Buch Ágost E.") == "Buch Ágost E."
    assert rr.unspace("  Glück   Sámuel ") == "Glück Sámuel"
    assert rr.unspace("") == "" and rr.unspace(None) == ""
