"""The ledger layer (knowledge_base/13 §6b): parsers for dates, cited issues,
registry positions and event verbs (app/ledger.py); `inherit: ditto` and
`record.derive` on dataset declarations; a project of kind `ledger` whose
rows are events — no same-source exclusion, dated typed slices, the issue
lane for rows citing an earlier issue, court disagreement blocks a match,
and the batch mints registrations only. Throwaway authority dir + `_smoke_`
project."""
import json
import shutil
import time
import uuid

import pytest
from PIL import Image

from app import ledger
from app import server as srv
from app import entities as ent
from app import record_resolve as rr
from app.pipeline import create_project, PROJECTS_ROOT

AUTH = "_smoke_registry.authority.json"
DS = "reg"


def test_parsers():
    assert ledger.parse_date_hu("1876. jan. 5.") == "1876-01-05"
    assert ledger.parse_date_hu("1876. évi január hó 5-én") == "1876-01-05"
    assert ledger.parse_date_hu("1876. decz. 31.") == "1876-12-31"
    assert ledger.parse_date_hu("1883. máj. 4.\n547/1") == "1883-05-04"
    assert ledger.parse_date_hu("1876 martius") == "1876-03"
    assert ledger.parse_date_hu("kelt 1880") == "1880"
    assert ledger.parse_date_hu("no date here") is None
    assert ledger.parse_issue_ref("(Lásd az eredeti be- jegyzést, 14. szám.)") == {"issue": 14}
    assert ledger.parse_issue_ref("Lásd a II. évf. 34. számát") == {"issue": 34, "volume": "II"}
    assert ledger.parse_issue_ref("(Lásd 1924. évf. 8.)") == {"issue": 8, "year": 1924}
    assert ledger.parse_issue_ref("Ezen czég töröltetett.") is None
    assert ledger.parse_folio("1880. márc. 22.\n7/4") == {"folio": 7, "sub": 4}
    assert ledger.parse_folio("1876. jan. 5.") is None
    assert ledger.event_type("Ezen czég a czégjegyzékből kitöröltetett.") == "deleted"
    assert ledger.event_type("átvezettetett a társas czégek jegyzékébe") == "transferred"
    assert ledger.event_type("Berger Fülöp kilépett") == "changed"
    assert ledger.event_type("") == "registered"
    assert ledger.event_type("Zinner Móritz kereskedő Pozsonyban") == "registered"
    assert rr.courts_agree("Budapesti k. és v. törv.", "Budapesti törvényszék") is True
    assert rr.courts_agree("Sz.-fehérvári törvényszék", "Székesfehérvári törvényszék") is True
    assert rr.courts_agree("Pesti törvényszék", "Szegedi törvényszék") is False
    assert rr.courts_agree("", "Szegedi törvényszék") is None
    d = ledger.derive({"order": "1876. jan. 5.", "note": "(Lásd 2. szám.)", "owner": ""},
                      {"event_date": {"from": "order", "parser": "date_hu"},
                       "issue_ref": {"from": "note", "parser": "issue_ref"},
                       "event_type": {"from": ["note", "owner"], "parser": "event_type"},
                       "bad": {"from": "order", "parser": "nope"}})
    assert d == {"event_date": "1876-01-05", "issue_ref": {"issue": 2}, "event_type": "registered", "bad": None}


def _cell(sr, sc, text, label="table_cell"):
    sh = {"label": label, "points": [[sc * 100, sr * 60 + 80], [sc * 100 + 90, sr * 60 + 130]],
          "shape_type": "rectangle", "flags": {}, "group_id": None,
          "super_row": sr, "super_column": sc, "table": 0}
    if text:
        sh["pdf_text"] = text
    return sh


def _masthead(issue):
    return {"label": "document_header", "points": [[50, 10], [900, 60]], "shape_type": "rectangle", "flags": {},
            "group_id": None, "pdf_text": f"I. évfolyam.  Budapest, 1876. január 23-án.  {issue}. szám.  Előfizetési ár"}


HEADERS = ["Királyi törvényszék", "A bejegyzést rendelő végzés kelte", "A czég szószerinti szövege",
           "A főtelep", "A czég birtokosa", "Jegyzet"]

DECL = {
    "name": DS, "version": 1,
    "scope": {"labels": ["table_cell"], "pattern": "1", "tables": [0], "header": {"5": "birtokos"}},
    "record": {"unit": "lattice_row", "key": {"slot": 1, "column": 3, "dtype": "text"},
               "identity": {"name_hu": "name", "seat": "seat", "court": "court"},
               "derive": {"event_date": {"from": "order", "parser": "date_hu"},
                          "issue_ref": {"from": "note", "parser": "issue_ref"},
                          "event_type": {"from": ["note", "owner"], "parser": "event_type"}}},
    "variables": [{"name": "court", "column": 1, "inherit": "ditto"}, {"name": "order", "column": 2},
                  {"name": "name", "column": 3}, {"name": "seat", "column": 4},
                  {"name": "owner", "column": 5}, {"name": "note", "column": 6}],
}


def _page(ann, stem, issue, rows):
    shapes = [_masthead(issue)] + [_cell(1, c + 1, h, "table_header") for c, h in enumerate(HEADERS)]
    for r, vals in enumerate(rows, start=2):
        shapes += [_cell(r, c + 1, v) for c, v in enumerate(vals)]
    (ann / f"{stem}.json").write_text(json.dumps({"version": "5.0.1", "flags": {}, "shapes": shapes,
                                                  "imagePath": f"{stem}.jpg", "imageWidth": 1000, "imageHeight": 600},
                                                 ensure_ascii=False), encoding="utf-8")
    Image.new("RGB", (1000, 600), "white").save(ann / f"{stem}.jpg")


def _rm(path):
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.3)


@pytest.fixture()
def ledger_project(tmp_path, monkeypatch):
    d = tmp_path / "authorities"; d.mkdir()
    (d / AUTH).write_text(json.dumps({
        "authority": "_smoke_registry", "version": "0.3", "entity_types": ["firm"],
        "id_scheme": {"prefix": "R", "width": 6}, "normalize": {"strip_tokens": ["és társa"]},
        "slices_present": [], "counts": {}, "edges": [], "entities": []}), encoding="utf-8")
    monkeypatch.setattr(srv, "AUTHORITIES_DIR", d)
    srv._AUTH_CACHE.clear(); srv._AUTH_META_CACHE.clear(); ent._CACHE.clear()
    name = f"_smoke_ledger_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", ["table_cell", "table_header", "document_header"])
    pd = PROJECTS_ROOT / name
    cfg = json.loads((pd / "config.json").read_text(encoding="utf-8"))
    cfg.update({"record_authority": AUTH, "source_year": 1876, "record_datasets": [DS], "record_source_kind": "ledger"})
    (pd / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (pd / "datasets").mkdir(exist_ok=True)
    (pd / "datasets" / f"{DS}.dataset.json").write_text(json.dumps(DECL, ensure_ascii=False), encoding="utf-8")
    ann = pd / "annotations"; ann.mkdir(parents=True, exist_ok=True)
    _page(ann, "p1", 1, [
        ["Pesti törvényszék", "1876. jan. 5.", "Zinner Móritz", "Pest", "Zinner Móritz kereskedő Pesten", ""],
        ["„", "1876. jan. 6.", "Glück Sámuel", "Tokaj", "Glück Sámuel bőrkereskedő", ""],
    ])
    _page(ann, "p2", 2, [
        ["Pesti törvényszék", "1876. jan. 30.", "Zinner Móritz", "Pest", "", "Ezen czég töröltetett. (Lásd az eredeti bejegyzést, 1. szám.)"],
        ["Szegedi törvényszék", "1876. febr. 2.", "Zinner Móritz", "Szeged", "Zinner Móritz rőfös kereskedő Szegeden", ""],
        [">", "1876. febr. 3.", "Kis János", "Szeged", "Kis János társ kilépett", ""],
    ])
    try:
        yield name, d
    finally:
        _rm(pd)


def test_rows_carry_inherited_court_and_parsed_event_fields(client, ledger_project):
    name, _ = ledger_project
    d = client.get(f"/api/project/{name}/records").json()
    assert d["record_source_kind"] == "ledger" and len(d["records"]) == 5
    a, b, c, dd, e = d["records"]
    assert a["values"]["court"] == "Pesti törvényszék" and b["values"]["court"] == "Pesti törvényszék"   # ditto inherited
    assert e["values"]["court"] == "Szegedi törvényszék"                                                # OCR's ">" too
    assert srv._ds_decl_problems(srv.DatasetDecl(**DECL)) == []
    bad = json.loads(json.dumps(DECL)); bad["variables"][0]["inherit"] = "up"; bad["record"]["derive"]["x"] = {"from": "nope", "parser": "date_hu"}
    probs = srv._ds_decl_problems(srv.DatasetDecl(**bad))
    assert any("inherit" in p for p in probs) and any("'nope'" in p for p in probs)
    ca = client.get(f"/api/project/{name}/records/candidates", params={"gid": a["id"]}).json()
    assert ca["kind"] == "ledger"
    assert ca["identity"] == {"name_hu": "Zinner Móritz", "seat": "Pest", "court": "Pesti törvényszék",
                              "event_date": "1876-01-05", "event_type": "registered", "issue": 1}
    cc = client.get(f"/api/project/{name}/records/candidates", params={"gid": c["id"]}).json()["identity"]
    assert cc["event_type"] == "deleted" and cc["issue_ref"] == {"issue": 1} and cc["issue"] == 2 and cc["event_date"] == "1876-01-30"


def test_ledger_batch_mints_registrations_attaches_cited_events_and_leaves_gaps_open(client, ledger_project):
    name, auth_dir = ledger_project
    recs = client.get(f"/api/project/{name}/records").json()["records"]
    a, b, c, d, e = [r["id"] for r in recs]
    r = client.post(f"/api/project/{name}/resolve-records", json={"mint_unmatched": True, "accept_floor": 85})
    assert r.status_code == 200, r.text
    out = r.json()
    by = {i["id"]: i for i in out["items"]}
    assert {k: v for k, v in out["counts"].items() if v} == {"new": 3, "same": 1, "unresolved": 1}
    assert by[a]["decision"] == "new" and by[a]["entity"] == "R000001"
    assert by[b]["decision"] == "new" and by[b]["entity"] == "R000002"
    # C: the deletion of Zinner cites issue 1 — same source as R000001's slice, no exclusion for a ledger
    assert by[c]["decision"] == "same" and by[c]["entity"] == "R000001"
    assert "cited issue" in by[c]["best"]["boost_why"] and "court" in by[c]["best"]["boost_why"]
    # D: the same name at another court is another registry entry
    assert by[d]["decision"] == "new" and by[d]["entity"] == "R000003"
    # E: a change event with no match is a gap, not a new firm
    assert by[e]["decision"] == "unresolved" and "not minted" in by[e]["reason"]
    auth = json.loads((auth_dir / AUTH).read_text(encoding="utf-8"))
    ents = {x["id"]: x for x in auth["entities"]}
    z = ents["R000001"]
    assert z["attrs"]["court"] == "Pesti törvényszék" and len(z["slices"]) == 2
    s1, s2 = z["slices"]
    assert (s1["as_of"], s1["date"], s1["event"], s1["provenance"]["issue"]) == (1876, "1876-01-05", "registered", 1)
    assert (s2["date"], s2["event"], s2["provenance"]["issue"], s2["attrs"]["court"]) == ("1876-01-30", "deleted", 2, "Pesti törvényszék")
    assert ents["R000003"]["attrs"]["seat"] == "Szeged" and auth["slices_present"] == [1876]
    # the issue lane is in the index, and a second run keeps everything
    idx = srv._load_authority(AUTH)
    assert idx["by_source_issue"][(name, 1)] == ["R000001", "R000002"] and idx["by_source_issue"][(name, 2)] == ["R000001", "R000003"]
    again = client.post(f"/api/project/{name}/resolve-records", json={"mint_unmatched": True}).json()
    assert again["counts"].get("kept") == 4 and again["counts"].get("unresolved") == 1
    # the entity view lists both events of Zinner under the one project
    t = client.get("/api/entities/table", params={"authority": AUTH}).json()
    row = next(x for x in t["rows"] if x["id"] == "R000001")
    assert [cell["group_id"] for cell in row["cells"][name]] == [a, c]


def test_batch_runs_as_a_background_job_with_progress(client, ledger_project):
    """`background: true` returns at once; the status endpoint reports pass,
    done / total and counts while it runs and the full result at the end;
    the authority is held in memory meanwhile and written once."""
    name, auth_dir = ledger_project
    assert client.get(f"/api/project/{name}/resolve-records/status").json()["job"] is None
    assert client.post(f"/api/project/{name}/resolve-records/stop").json()["ok"] is False
    r = client.post(f"/api/project/{name}/resolve-records", json={"mint_unmatched": True, "background": True})
    assert r.status_code == 200 and r.json()["job"]["state"] == "running"
    for _ in range(300):
        j = client.get(f"/api/project/{name}/resolve-records/status").json()["job"]
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["state"] == "finished" and j["total"] == 5 and j["done"] == 5 and j["error"] is None
    assert {k: v for k, v in j["counts"].items() if v} == {"new": 3, "same": 1, "unresolved": 1}
    assert "result" not in j
    res = client.get(f"/api/project/{name}/resolve-records/status", params={"items": True}).json()["job"]["result"]
    assert len(res["items"]) == 5 and res["authority_file"] == AUTH
    auth = json.loads((auth_dir / AUTH).read_text(encoding="utf-8"))
    assert len(auth["entities"]) == 3 and auth["counts"]["entities"] == 3 and auth["slices_present"] == [1876]
    assert AUTH not in srv._AUTH_SESSIONS                               # the session closed
    # the loaded index is the session's: the cited-issue map is there without a rebuild
    assert srv._load_authority(AUTH)["by_source_issue"][(name, 1)] == ["R000001", "R000002"]
    # a second job while none runs: everything kept
    r = client.post(f"/api/project/{name}/resolve-records", json={"mint_unmatched": True, "background": True})
    for _ in range(300):
        j = client.get(f"/api/project/{name}/resolve-records/status").json()["job"]
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["counts"].get("kept") == 4


def test_census_projects_keep_the_same_source_exclusion(client, ledger_project):
    """The same rows as a census: the deletion row cannot be offered the
    entity its own volume already registered (the old rule, unchanged)."""
    name, _ = ledger_project
    r = client.patch(f"/api/project/{name}/config", json={"record_source_kind": "census"})
    assert r.status_code == 200 and "record_source_kind" not in r.json()["config"]
    assert client.patch(f"/api/project/{name}/config", json={"record_source_kind": "weird"}).status_code == 400
    recs = client.get(f"/api/project/{name}/records").json()["records"]
    a, b, c = [x["id"] for x in recs[:3]]
    client.post(f"/api/project/{name}/records/resolve", json={"gid": a, "outcome": "new", "source": "human"})
    cc = client.get(f"/api/project/{name}/records/candidates", params={"gid": c}).json()
    assert cc["kind"] == "census" and "R000001" not in [x["id"] for x in cc["candidates"]]
    client.patch(f"/api/project/{name}/config", json={"record_source_kind": "ledger"})
    cc = client.get(f"/api/project/{name}/records/candidates", params={"gid": c}).json()
    assert [x["id"] for x in cc["candidates"]][:1] == ["R000001"] and cc["auto"]["verdict"] == "same"
