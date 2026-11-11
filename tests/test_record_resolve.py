"""Record-level entity resolution (knowledge_base/13 §2, E3): candidates
for a record from its identity object, the three outcomes (same / new /
successor) written to the authority file and the head fragment, and the
batch with the bootstrap (mint unmatched). Throwaway `_smoke_` project and
a throwaway authority directory; nothing git-tracked is touched.
"""
import json
import shutil
import time
import uuid

import pytest
from PIL import Image

from app import server as srv
from app import record_resolve as rr
from app.pipeline import create_project, PROJECTS_ROOT

AUTH = "_smoke_firms.authority.json"


def _sh(label, x1, y1, x2, y2, **extra):
    d = {"label": label, "points": [[x1, y1], [x2, y2]], "group_id": None,
         "shape_type": "rectangle", "flags": {}}
    d.update(extra)
    return d


def _structured(**ident):
    return {"schema_name": "identity", "llm": ident, "data": ident, "edited": False, "model": "m", "ts": "t",
            "record": {"group_id": 0, "fragments": [], "n_fragments": 1}}


def _page(ann, stem, shapes, status="verified"):
    ann.mkdir(parents=True, exist_ok=True)
    (ann / f"{stem}.json").write_text(json.dumps({"version": "5.0.1", "flags": {"status": status}, "shapes": shapes,
                                                  "imagePath": f"{stem}.jpg", "imageWidth": 1000, "imageHeight": 800}),
                                      encoding="utf-8")
    Image.new("RGB", (1000, 800), "white").save(ann / f"{stem}.jpg")


def _rm(path):
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.3)


@pytest.fixture()
def auth_dir(tmp_path, monkeypatch):
    d = tmp_path / "authorities"; d.mkdir()
    (d / AUTH).write_text(json.dumps({
        "authority": "_smoke_firms", "version": "0.3", "entity_types": ["firm"],
        "id_scheme": {"prefix": "F", "width": 6},
        "normalize": {"strip_tokens": ["részvénytársaság", "r.-t.", "Actiengesellschaft"]},
        "slices_present": [], "counts": {}, "edges": [], "entities": [
            {"id": "F000001", "name": "Galántai takarékpénztár", "type": "firm",
             "aliases": [{"name": "Galántaer Sparcassa", "lang": "de", "source": "compass_1877"}],
             "xref": {}, "attrs": {"seat": "Galánta", "founded": 1873},
             "slices": [{"as_of": 1877, "source": "compass_1877", "type": "firm", "parent": None,
                         "name": "Galántai takarékpénztár", "attrs": {"seat": "Galánta", "founded": 1873},
                         "provenance": {"project": "compass_1877", "stem": "x1", "group_id": 71}, "seq": 71}]},
            {"id": "F000002", "name": "Galántai népbank", "type": "firm", "aliases": [], "xref": {},
             "attrs": {"seat": "Galánta", "founded": 1869}, "slices": []},
            {"id": "F000003", "name": "Pesti Hengermalom Részvénytársaság", "type": "firm", "aliases": [], "xref": {},
             "attrs": {"seat": "Budapest", "founded": 1839}, "slices": []},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(srv, "AUTHORITIES_DIR", d)
    srv._AUTH_CACHE.clear(); srv._AUTH_META_CACHE.clear()
    return d


@pytest.fixture()
def project(auth_dir):
    name = f"_smoke_recr_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", ["firm"])
    pd = PROJECTS_ROOT / name
    cfg = json.loads((pd / "config.json").read_text(encoding="utf-8"))
    cfg.update({"record_label": "firm", "layout_columns": "1", "record_authority": AUTH, "source_year": 1882})
    (pd / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    ann = pd / "annotations"
    _page(ann, "p1", [
        # record 1: the Galánta savings bank again, German name first, with seat + founding year
        _sh("firm", 100, 100, 900, 300, group_id=1, tesseract_output={"ocr_text": "71. Galantaer Sparcassa"},
            structured=_structured(serial_no=71, name_hu="Galántai takarékpénztár r.-t.", name_de="Galántaer Sparcassa",
                                   seat="Galánta", founded=1873, legal_form="részvénytársaság", capital_nominal=50000, currency="frt")),
        # record 2: no identity object, only text; nothing like it in the authority
        _sh("firm", 100, 350, 900, 500, group_id=2, tesseract_output={"ocr_text": "Promontori önsegélyző-egylet"}),
        # record 3: renamed mill — the human will declare it a successor of F000003
        _sh("firm", 100, 550, 900, 700, group_id=3,
            structured=_structured(name_hu="Budapesti Hengermalom Részvénytársaság", seat="Budapest", founded=1839)),
    ])
    try:
        yield name
    finally:
        _rm(pd)


def _auth(auth_dir):
    return json.loads((auth_dir / AUTH).read_text(encoding="utf-8"))


def _head(project, idx=0):
    return json.loads((PROJECTS_ROOT / project / "annotations" / "p1.json").read_text(encoding="utf-8"))["shapes"][idx]


def test_candidates_use_the_identity_object_and_boost_on_facts(client, project):
    r = client.get(f"/api/project/{project}/records/candidates", params={"gid": 1})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [q[0] for q in d["queries"]] == ["Galántai takarékpénztár r.-t.", "Galántaer Sparcassa"]
    assert d["identity"]["seat"] == "Galánta" and d["source_year"] == 1882
    top = d["candidates"][0]
    assert top["id"] == "F000001" and top["score"] >= 90
    assert set(top["boost_why"]) == {"seat", "founded"} and top["boost"] == rr.BOOST_SEAT + rr.BOOST_FOUNDED
    assert top["facts"] == {"seat": "Galánta", "founded": 1873}
    assert top["last_slice"]["as_of"] == 1877 and top["crop"]["stem"] == "x1"
    # the other Galánta bank is there too, lower, with only the seat boost
    other = next(c for c in d["candidates"] if c["id"] == "F000002")
    assert other["boost_why"] == ["seat"] and other["ranked"] < top["ranked"]
    # a record without an identity object matches on its text
    d2 = client.get(f"/api/project/{project}/records/candidates", params={"gid": 2}).json()
    assert d2["identity"] == {} and d2["queries"] == [["Promontori önsegélyző-egylet", None]]


def test_same_writes_a_slice_and_the_printed_alias(client, project, auth_dir):
    r = client.post(f"/api/project/{project}/records/resolve",
                    json={"gid": 1, "outcome": "same", "entity_id": "F000001", "source": "human", "score": 95})
    assert r.status_code == 200, r.text
    assert r.json()["entity"] == {"id": "F000001", "name": "Galántai takarékpénztár"}
    e = next(x for x in _auth(auth_dir)["entities"] if x["id"] == "F000001")
    assert len(e["slices"]) == 2
    sl = e["slices"][1]
    assert sl["as_of"] == 1882 and sl["source"] == project and sl["seq"] == 71
    assert sl["provenance"] == {"project": project, "stem": "p1", "group_id": 1}
    assert sl["attrs"]["capital_nominal"] == 50000 and sl["attrs"]["legal_form"] == "részvénytársaság"
    assert {a["name"] for a in e["aliases"]} == {"Galántaer Sparcassa", "Galántai takarékpénztár r.-t."}
    assert _auth(auth_dir)["slices_present"] == [1877, 1882]
    a = _head(project)["authority"]
    assert a["id"] == "F000001" and a["outcome"] == "same" and a["source"] == "human" and a["record"] == {"group_id": 1}
    # re-running replaces the slice, never stacks it
    client.post(f"/api/project/{project}/records/resolve", json={"gid": 1, "outcome": "same", "entity_id": "F000001"})
    assert len(next(x for x in _auth(auth_dir)["entities"] if x["id"] == "F000001")["slices"]) == 2
    # the browser shows it
    rec = next(x for x in client.get(f"/api/project/{project}/records").json()["records"] if x["id"] == 1)
    assert rec["authority"]["id"] == "F000001" and rec["authority"]["outcome"] == "same"


def test_new_mints_from_the_record_and_successor_adds_an_edge(client, project, auth_dir):
    r = client.post(f"/api/project/{project}/records/resolve", json={"gid": 2, "outcome": "new"})
    assert r.status_code == 200, r.text
    assert r.json()["entity"]["id"] == "F000004" and r.json()["entity"]["name"] == "Promontori önsegélyző-egylet"
    e = next(x for x in _auth(auth_dir)["entities"] if x["id"] == "F000004")
    assert e["type"] == "firm" and e["slices"][0]["provenance"]["group_id"] == 2 and e["slices"][0]["seq"] == 2
    r = client.post(f"/api/project/{project}/records/resolve",
                    json={"gid": 3, "outcome": "successor", "entity_id": "F000003", "edge_type": "rename", "note": "moved seat"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["entity"]["id"] == "F000005" and d["edge"]["from"] == "F000003" and d["edge"]["type"] == "rename"
    auth = _auth(auth_dir)
    assert auth["edges"][0]["to"] == "F000005" and auth["edges"][0]["as_of"] == 1882 and auth["counts"]["edges"] == 1
    e5 = next(x for x in auth["entities"] if x["id"] == "F000005")
    assert e5["name"] == "Budapesti Hengermalom Részvénytársaság" and e5["attrs"] == {"founded": 1839, "seat": "Budapest"}
    a = _head(project, 2)["authority"]
    assert a["outcome"] == "successor" and a["edge"] == {"from": "F000003", "to": "F000005", "type": "rename"}
    # bad inputs
    assert client.post(f"/api/project/{project}/records/resolve",
                       json={"gid": 3, "outcome": "successor", "entity_id": "F000003", "edge_type": "duplicate_of"}).status_code == 400
    assert client.post(f"/api/project/{project}/records/resolve",
                       json={"gid": 3, "outcome": "same", "entity_id": "F999999"}).status_code == 400
    assert client.post(f"/api/project/{project}/records/resolve", json={"gid": 99, "outcome": "new"}).status_code == 404
    # clear drops the mark, keeps the file
    r = client.post(f"/api/project/{project}/records/resolve", json={"gid": 3, "outcome": "clear"})
    assert r.status_code == 200 and "authority" not in _head(project, 2)
    assert len(_auth(auth_dir)["entities"]) == 5


def test_batch_accepts_unambiguous_matches_and_bootstraps_the_rest(client, project, auth_dir):
    dry = client.post(f"/api/project/{project}/resolve-records", json={"dry_run": True, "accept_floor": 95}).json()
    assert dry["counts"] == {"same": 1, "new": 0, "unresolved": 2, "kept": 0}
    by = {i["id"]: i for i in dry["items"]}
    assert by[1]["decision"] == "same" and by[1]["best"]["id"] == "F000001"
    assert by[2]["decision"] == "unresolved"
    # "Budapesti Hengermalom" vs "Pesti Hengermalom", same seat and year: a strong
    # candidate, but the name itself changed — exactly the case the floor must
    # leave to a human (13 §5.4: a name change is a successor edge, never auto-same)
    assert by[3]["decision"] == "unresolved" and by[3]["best"]["id"] == "F000003"
    assert by[3]["best"]["score"] < 95          # whole-name score: a different name, not a near-miss
    assert len(_auth(auth_dir)["entities"]) == 3                                     # dry run wrote nothing
    # a human decision first: record 3 is a successor; the batch must keep it
    client.post(f"/api/project/{project}/records/resolve",
                json={"gid": 3, "outcome": "successor", "entity_id": "F000003", "edge_type": "rename"})
    d = client.post(f"/api/project/{project}/resolve-records",
                    json={"accept_floor": 95, "mint_unmatched": True}).json()
    assert d["counts"] == {"same": 1, "new": 1, "unresolved": 0, "kept": 1}
    auth = _auth(auth_dir)
    assert len(auth["entities"]) == 5
    assert _head(project, 0)["authority"]["source"] == "auto" and _head(project, 0)["authority"]["id"] == "F000001"
    assert _head(project, 1)["authority"]["outcome"] == "new"
    assert _head(project, 2)["authority"]["source"] == "human"
    # second run: everything kept; with overwrite, auto marks are re-decided, the human one still kept
    d2 = client.post(f"/api/project/{project}/resolve-records", json={"accept_floor": 95, "mint_unmatched": True}).json()
    assert d2["counts"]["kept"] == 3
    d3 = client.post(f"/api/project/{project}/resolve-records",
                     json={"accept_floor": 95, "overwrite": True, "dry_run": True}).json()
    assert d3["counts"]["kept"] == 1 and d3["counts"]["same"] == 2       # record 2 now matches its own entity


def test_project_without_a_record_authority_is_refused(client, project):
    client.patch(f"/api/project/{project}/config", json={"record_authority": ""})
    r = client.get(f"/api/project/{project}/records/candidates", params={"gid": 1})
    assert r.status_code == 400 and "record_authority" in r.text
    r = client.patch(f"/api/project/{project}/config", json={"record_authority": "nope.authority.json"})
    assert r.status_code == 400
    r = client.patch(f"/api/project/{project}/config", json={"record_authority": AUTH, "source_year": "1887"})
    assert r.status_code == 200
    ctx = client.get("/api/records/context", params={"folder": str(PROJECTS_ROOT / project / "annotations")}).json()
    assert ctx["record_authority"] == AUTH and ctx["source_year"] == 1887


def test_module_helpers():
    ident = {"name_hu": "A r.-t.", "name_de": "A A.-G.", "other_names": [{"name": "A Ltd", "lang": "en"}, "A & Co"]}
    assert rr.queries_for("ignored", ident) == [("A r.-t.", "hu"), ("A A.-G.", "de"), ("A Ltd", "en"), ("A & Co", None)]
    assert rr.queries_for("Fallback text", {}) == [("Fallback text", None)]
    assert rr.identity_of({"data": {"seat": "X", "founded": None, "other_names": []}}) == {"seat": "X"}
    assert rr.identity_of(None) == {}
    ent = {"attrs": {"seat": "Old"}, "slices": [{"attrs": {"seat": "New", "founded": "1873"}}]}
    assert rr.entity_facts(ent) == {"seat": "New", "founded": "1873"}
    b, why = rr.boost_for(ent, {"seat": "new", "founded": 1873})
    assert b == rr.BOOST_SEAT + rr.BOOST_FOUNDED and why == ["seat", "founded"]
    assert rr.boost_for(ent, {}) == (0.0, [])
    assert rr.display_name("txt", {"name_de": "D"}) == "D" and rr.display_name("txt", {}) == "txt"
    # a firm printed only in a third language (the 1877 "Aurora" in Naszód, Romanian)
    assert rr.display_name("", {"name_hu": None, "name_de": None,
                                "other_names": [{"name": "„Aurora“ societa de imprumutul", "lang": "ro"}]}) == "„Aurora“ societa de imprumutul"
    assert rr.display_name("", {"other_names": ["plain"]}) == "plain"


def test_bootstrap_never_matches_records_of_the_same_source(client, project, auth_dir):
    """A directory lists a firm once: within one source every record is a new
    entity, however similar the names — and the dry run says the same."""
    # start from an empty authority, like a first volume
    data = _auth(auth_dir); data["entities"] = []; data["slices_present"] = []
    (auth_dir / AUTH).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    srv._AUTH_CACHE.clear()
    ann = PROJECTS_ROOT / project / "annotations"
    _page(ann, "p2", [   # two near-identical generic names on one more page
        _sh("firm", 100, 100, 900, 300, group_id=4, structured=_structured(name_hu="Pesti magyar kereskedelmi bank", seat="Budapest")),
        _sh("firm", 100, 350, 900, 600, group_id=5, structured=_structured(name_hu="Pesti magyar kereskedelmi bank", seat="Budapest")),
    ])
    dry = client.post(f"/api/project/{project}/resolve-records", json={"dry_run": True, "mint_unmatched": True, "accept_floor": 85}).json()
    assert dry["counts"] == {"same": 0, "new": 5, "unresolved": 0, "kept": 0}
    real = client.post(f"/api/project/{project}/resolve-records", json={"mint_unmatched": True, "accept_floor": 85}).json()
    assert real["counts"] == dry["counts"]
    assert len(_auth(auth_dir)["entities"]) == 5
    # re-running with overwrite keeps every record on its own entity (its own slice is not "another record")
    again = client.post(f"/api/project/{project}/resolve-records", json={"dry_run": True, "overwrite": True, "accept_floor": 85}).json()
    assert again["counts"]["same"] == 5 and again["counts"]["new"] == 0
    by = {i["id"]: i for i in again["items"]}
    assert by[4]["best"]["id"] != by[5]["best"]["id"]


def test_sharing_a_generic_word_is_not_a_match(client, project, auth_dir):
    ann = PROJECTS_ROOT / project / "annotations"
    _page(ann, "p2", [_sh("firm", 100, 100, 900, 300, group_id=4,
                          structured=_structured(name_hu="Első magyar iparbank", name_de="Erste ungarische Gewerbebank"))])
    data = _auth(auth_dir)
    data["entities"].append({"id": "F000009", "name": "Pesti magyar kereskedelmi bank", "type": "firm",
                             "aliases": [{"name": "Pester ungarische Commercial-Bank", "lang": "de"}], "xref": {}, "attrs": {}, "slices": []})
    (auth_dir / AUTH).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8"); srv._AUTH_CACHE.clear()
    d = client.get(f"/api/project/{project}/records/candidates", params={"gid": 4}).json()
    bank = next((c for c in d["candidates"] if c["id"] == "F000009"), None)
    assert bank is None or bank["score"] < 70, bank
    assert srv._auth_whole_name_score("Galántai takarékpénztár r.-t.", "Galántai takarékpénztár", srv._load_authority(AUTH)) == 100.0
