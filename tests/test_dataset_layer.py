"""Dataset layer (knowledge_base/10_dataset_layer.md): declaration listing
(GET /api/datasets), the builder (GET /api/dataset/<name>/build) and the
diagnostics ladder (POST /api/dataset/<name>/diagnose)."""
import json

import pytest
from PIL import Image


def _auth(id_, name):
    return {"id": id_, "name": name, "type": "settlement",
            "score": 100, "source": "auto", "ts": "t"}


def _cell(sr, sc, rows=None, text=None, auth=None, label="cell"):
    sh = {"label": label, "points": [[sc * 100, sr * 60], [sc * 100 + 90, sr * 60 + 50]],
          "shape_type": "rectangle", "flags": {},
          "super_row": sr, "super_column": sc, "table": 0}
    if text is not None:
        sh["human_output"] = {"human_corrected_text": text}
    if auth:
        sh["authority"] = auth
    if rows is not None:
        sh["row_struct"] = {"version": 1, "origin": "t", "rows": [
            dict({"n": i + 1, "y0": 0.1 + 0.4 * i, "y1": 0.45 + 0.4 * i,
                  "ocr": r.get("ocr", ""), "llm": r.get("llm", ""),
                  "human": r.get("human", "")},
                 **({"authority": r["auth"]} if r.get("auth") else {}),
                 **({"blank": True} if r.get("blank") else {}))
            for i, r in enumerate(rows)]}
    return sh


DECL = {
    "name": "smoke_main",
    "version": 1,
    "scope": {"labels": ["cell"], "pattern": "1,1", "tables": [0]},
    "record": {"unit": "internal_row",
               "key": {"slot": 1, "column": 2, "dtype": "entity",
                       "authority": "places_hu"}},
    "variables": [
        {"name": "settlement", "slot": 1, "column": 2, "dtype": "entity"},
        {"name": "area_total", "slot": 1, "column": 3, "dtype": "number", "min": 0},
        {"name": "n_tractors", "slot": 2, "column": 6, "dtype": "int",
         "min": 0, "max": 500},
    ],
    "parse": {"missing": ["-", "—", "·", ""], "thousands": [" ", "."],
              "decimal": ","},
}


@pytest.fixture()
def ds_folder(tmp_path):
    """Two-slot book (pattern 1,1), two cycles, one deliberate defect per
    diagnostic rung:
      cycle 0: p1 (slot 1: key + area), p2 (slot 2: tractors, one 'abc')
      cycle 1: p3 (slot 1: area column MISSING, one duplicate key, one
                   unresolved key), p4 (slot 2: one '-' missing, one 600>max)
    """
    ann = tmp_path / "proj" / "annotations"
    ann.mkdir(parents=True)
    dsd = tmp_path / "proj" / "datasets"
    dsd.mkdir()
    (dsd / "smoke_main.dataset.json").write_text(
        json.dumps(DECL, ensure_ascii=False), encoding="utf-8")

    def page(stem, shapes, flags=None):
        (ann / f"{stem}.json").write_text(json.dumps(
            {"shapes": shapes, "flags": flags or {}, "imagePath": f"{stem}.jpg",
             "imageWidth": 900, "imageHeight": 500}), encoding="utf-8")
        Image.new("RGB", (900, 500), "white").save(ann / f"{stem}.jpg")

    page("p1", [
        _cell(1, 2, rows=[{"human": "Aporka", "auth": _auth("M2", "Aporka")},
                          {"human": "Kisvaszar", "auth": _auth("M1", "Kisvaszar")}]),
        _cell(1, 3, rows=[{"human": "1 234"}, {"llm": "12,5"}]),
    ])
    page("p2", [
        _cell(1, 6, rows=[{"human": "3"}, {"human": "abc"}]),
    ])
    page("p3", [
        _cell(1, 2, rows=[{"human": "Kisvaszar", "auth": _auth("M1", "Kisvaszar")},
                          {"human": "Új falu"}]),
        # column 3 (area_total) deliberately missing on this slot-1 page
    ])
    page("p4", [
        _cell(1, 6, rows=[{"human": "-"}, {"human": "600"}]),
    ])
    return ann


def test_list_datasets(client, ds_folder):
    r = client.get("/api/datasets", params={"folder": str(ds_folder)})
    assert r.status_code == 200, r.text
    ds = r.json()["datasets"]
    assert len(ds) == 1
    assert ds[0]["name"] == "smoke_main" and ds[0]["variables"] == 3
    assert "error" not in ds[0]


def test_build_records_join_and_parse(client, ds_folder):
    r = client.get("/api/dataset/smoke_main/build", params={"folder": str(ds_folder)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["cycles"] == 2 and d["pages"] == 4
    assert d["n_records"] == 4 and d["returned"] == 4
    recs = d["records"]

    r0, r1, r2, r3 = recs
    # positional join: slot-2 values land on the right internal row
    assert r0["key"]["id"] == "M2" and r0["key"]["text"] == "Aporka"
    assert r0["values"]["area_total"]["value"] == 1234.0        # "1 234"
    assert r0["values"]["n_tractors"]["value"] == 3
    assert r0["values"]["n_tractors"]["stem"] == "p2"           # provenance
    assert r1["values"]["area_total"]["value"] == 12.5          # "12,5" via LLM
    assert r1["values"]["area_total"]["layer"] == "llm"
    assert r1["values"]["n_tractors"]["status"] == "error"      # "abc"
    # cycle 1: area column missing → absent; "-" → missing; 600 parses fine
    assert r2["values"]["area_total"]["status"] == "absent"
    assert r2["values"]["n_tractors"]["status"] == "missing"    # "-"
    assert r3["values"]["n_tractors"]["value"] == 600


def test_diagnose_ladder(client, ds_folder):
    r = client.post("/api/dataset/smoke_main/diagnose",
                    params={"folder": str(ds_folder)}, json={})
    assert r.status_code == 200, r.text
    d = r.json()
    by = {}
    for g in d["groups"]:
        by.setdefault(g["check"], []).append(g)

    # 1 structure: the missing area column on p3
    st = [it for g in by["structure"] for it in g["items"]]
    assert any(it["stem"] == "p3" and "[3]" in it["detail"] for it in st)
    # 2 parse: "abc" in n_tractors
    pa = [it for g in by["parse"] for it in g["items"]]
    assert len(pa) == 1 and pa[0]["stem"] == "p2" and "'abc'" in pa[0]["detail"]
    assert by["parse"][0]["variable"] == "n_tractors"
    # 3 range: 600 > 500
    ra = [it for g in by["range"] for it in g["items"]]
    assert len(ra) == 1 and ra[0]["stem"] == "p4" and "600" in ra[0]["detail"]
    # 3 unresolved: "Új falu" (key column doubles as the settlement variable)
    un = [it for g in by["unresolved"] for it in g["items"]]
    assert any("Új falu" in it["detail"] for it in un)
    # 3 duplicate key: Kisvaszar on p1 and p3
    du = [it for g in by["duplicate_key"] for it in g["items"]]
    assert {it["stem"] for it in du} == {"p1", "p3"} and len(du) == 2
    # items carry what the report table needs
    assert pa[0]["row_i"] == 1 and pa[0]["human"] == "abc"
    assert pa[0]["y0"] is not None and pa[0]["idx"] is not None


def test_diagnose_page_restriction(client, ds_folder):
    # restrict to cycle 0 (pages 1-2): the p3/p4 findings disappear
    r = client.post("/api/dataset/smoke_main/diagnose",
                    params={"folder": str(ds_folder)}, json={"pages": "1-2"})
    d = r.json()
    stems = {it["stem"] for g in d["groups"] for it in g["items"]}
    assert stems <= {"p1", "p2"}
    assert d["records"] == 2


def test_skip_page_reported_not_crashed(client, ds_folder):
    # mark p2 clutter: its slot drops out, structure finding appears
    p2 = json.loads((ds_folder / "p2.json").read_text(encoding="utf-8"))
    p2["flags"]["status"] = "skip"
    (ds_folder / "p2.json").write_text(json.dumps(p2), encoding="utf-8")
    r = client.post("/api/dataset/smoke_main/diagnose",
                    params={"folder": str(ds_folder)}, json={})
    d = r.json()
    st = [it for g in d["groups"] if g["check"] == "structure" for it in g["items"]]
    assert any(it["stem"] == "p2" and "clutter" in it["detail"] for it in st)


def test_row_count_mismatch_is_structure_finding(client, ds_folder):
    # p2's tractor cell gets a 3rd internal row → join with the 2-row key fails
    p2 = json.loads((ds_folder / "p2.json").read_text(encoding="utf-8"))
    rows = p2["shapes"][0]["row_struct"]["rows"]
    rows.append({"n": 3, "y0": 0.9, "y1": 0.99, "ocr": "", "llm": "", "human": "7"})
    (ds_folder / "p2.json").write_text(json.dumps(p2), encoding="utf-8")
    r = client.post("/api/dataset/smoke_main/diagnose",
                    params={"folder": str(ds_folder)}, json={})
    st = [it for g in r.json()["groups"] if g["check"] == "structure"
          for it in g["items"]]
    assert any(it["stem"] == "p2" and "do not join" in it["detail"] for it in st)


def test_bad_declaration_rejected(client, ds_folder):
    dsd = ds_folder.parent / "datasets"
    bad = dict(DECL, name="bad",
               variables=[{"name": "x", "slot": 5, "column": 1, "dtype": "float"}])
    (dsd / "bad.dataset.json").write_text(json.dumps(bad), encoding="utf-8")
    r = client.get("/api/dataset/bad/build", params={"folder": str(ds_folder)})
    assert r.status_code == 400
    assert "slot 5" in r.json()["detail"] and "dtype" in r.json()["detail"]
    # the list endpoint reports rather than crashes
    ls = client.get("/api/datasets", params={"folder": str(ds_folder)}).json()
    entry = next(e for e in ls["datasets"] if e["name"] == "bad")
    assert "error" in entry


def test_unknown_dataset_404(client, ds_folder):
    r = client.get("/api/dataset/nope/build", params={"folder": str(ds_folder)})
    assert r.status_code == 404


def test_join_survives_different_lattice_banding(client, tmp_path):
    """foldbirtok1935 reality (2026-08-24): the county bands are segmented
    differently on the two pages of a pair (e.g. 2 bands vs 1) while the
    internal rows run continuously. The join is per column SEQUENCE, so this
    must produce clean records, not findings."""
    ann = tmp_path / "proj" / "annotations"
    ann.mkdir(parents=True)
    dsd = tmp_path / "proj" / "datasets"
    dsd.mkdir()
    decl = dict(DECL, name="banded",
                variables=[v for v in DECL["variables"] if v["slot"] == 1
                           or v["name"] == "n_tractors"])
    (dsd / "banded.dataset.json").write_text(json.dumps(decl), encoding="utf-8")

    def page(stem, shapes):
        (ann / f"{stem}.json").write_text(json.dumps(
            {"shapes": shapes, "flags": {}, "imagePath": f"{stem}.jpg",
             "imageWidth": 900, "imageHeight": 500}), encoding="utf-8")
        Image.new("RGB", (900, 500), "white").save(ann / f"{stem}.jpg")

    # key page: TWO bands (2 + 1 internal rows); slot-2 page: ONE band of 3
    page("p1", [
        _cell(1, 2, rows=[{"human": "Aporka"}, {"human": "Kisvaszar"}]),
        _cell(1, 3, rows=[{"human": "10"}, {"human": "20"}]),
        _cell(2, 2, rows=[{"human": "Szentes"}]),
        _cell(2, 3, rows=[{"human": "30"}]),
    ])
    page("p2", [
        _cell(1, 6, rows=[{"human": "1"}, {"human": "2"}, {"human": "3"}]),
    ])
    r = client.get("/api/dataset/banded/build", params={"folder": str(ann)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["n_records"] == 3
    by_key = {rec["key"]["text"]: rec for rec in d["records"]}
    assert by_key["Szentes"]["values"]["n_tractors"]["value"] == 3
    assert by_key["Szentes"]["values"]["area_total"]["value"] == 30.0
    assert by_key["Szentes"]["lattice_row"] == 2          # band = provenance
    dg = client.post("/api/dataset/banded/diagnose",
                     params={"folder": str(ann)}, json={}).json()
    assert not [g for g in dg["groups"] if g["check"] == "structure"]


# ── Keyed join (record.join = "keyed") ─────────────────────────────────────

KEYED_DECL = {
    "name": "smoke_keyed",
    "scope": {"labels": ["cell"], "pattern": "1,1", "tables": [0]},
    "record": {"unit": "internal_row", "join": "keyed",
               "key": {"slot": 1, "column": 2, "dtype": "entity",
                       "columns": {"2": 1}}},
    "variables": [
        {"name": "area", "slot": 1, "column": 3, "dtype": "number"},
        {"name": "horses", "slot": 2, "column": 4, "dtype": "int"},
    ],
}


@pytest.fixture()
def keyed_folder(tmp_path):
    """Both slots repeat the settlement column; slot 2 lists the rows in a
    DIFFERENT order (the case positional join gets wrong), plus one row only
    on the key page, one only on slot 2, and one duplicated on slot 2."""
    ann = tmp_path / "proj" / "annotations"
    ann.mkdir(parents=True)
    dsd = tmp_path / "proj" / "datasets"
    dsd.mkdir()
    (dsd / "smoke_keyed.dataset.json").write_text(
        json.dumps(KEYED_DECL, ensure_ascii=False), encoding="utf-8")

    def page(stem, shapes):
        (ann / f"{stem}.json").write_text(json.dumps(
            {"shapes": shapes, "flags": {}, "imagePath": f"{stem}.jpg",
             "imageWidth": 900, "imageHeight": 500}), encoding="utf-8")
        Image.new("RGB", (900, 500), "white").save(ann / f"{stem}.jpg")

    page("p1", [
        _cell(1, 2, rows=[{"human": "Aporka"}, {"human": "Kisvaszar"},
                          {"human": "Szentes"}]),
        _cell(1, 3, rows=[{"human": "10"}, {"human": "20"}, {"human": "30"}]),
    ])
    page("p2", [
        # reversed order + "Orphan" (not on p1) + "Dupla" twice (ambiguous)
        _cell(1, 1, rows=[{"human": "Kisvaszar"}, {"human": "Aporka"},
                          {"human": "Orphan"}, {"human": "Dupla"},
                          {"human": "Dupla"}]),
        _cell(1, 4, rows=[{"human": "2"}, {"human": "1"}, {"human": "9"},
                          {"human": "8"}, {"human": "7"}]),
    ])
    return ann


def test_keyed_join_matches_by_key_not_position(client, keyed_folder):
    r = client.get("/api/dataset/smoke_keyed/build",
                   params={"folder": str(keyed_folder)})
    assert r.status_code == 200, r.text
    recs = r.json()["records"]
    by_key = {rec["key"]["text"]: rec for rec in recs}
    # despite the reversed order on p2, values land on the right settlement
    assert by_key["Aporka"]["values"]["horses"]["value"] == 1
    assert by_key["Kisvaszar"]["values"]["horses"]["value"] == 2
    assert by_key["Aporka"]["values"]["area"]["value"] == 10.0
    # Szentes exists only on the key page → horses absent
    assert by_key["Szentes"]["values"]["horses"]["status"] == "absent"


def test_keyed_join_mismatches_are_loud(client, keyed_folder):
    r = client.post("/api/dataset/smoke_keyed/diagnose",
                    params={"folder": str(keyed_folder)}, json={})
    st = [it for g in r.json()["groups"] if g["check"] == "structure"
          for it in g["items"]]
    details = " | ".join(it["detail"] for it in st)
    assert "no row with key 'Szentes'" in details          # key page → slot 2
    assert "'Orphan' matches no row" in details            # slot 2 → key page
    assert "'Dupla' appears more than once" in details     # ambiguous
    # the ambiguous key must NOT be silently joined anywhere
    b = client.get("/api/dataset/smoke_keyed/build",
                   params={"folder": str(keyed_folder)}).json()
    assert all(rec["key"]["text"] != "Dupla" for rec in b["records"])
