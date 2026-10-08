"""The editor's dataset-declaration window: save/load/delete, test build
(preview), page→slot map, and sum discovery."""
import json
import random
import shutil
import uuid

import pytest

from app.pipeline import create_project, PROJECTS_ROOT


def _cell(label, x1, sr, sc, rows_text):
    rows = [{"n": i + 1, "y0": i * 20, "y1": (i + 1) * 20, "human": t}
            for i, t in enumerate(rows_text)]
    return {"label": label, "points": [[x1, 0], [x1 + 100, 20 * len(rows_text)]],
            "shape_type": "rectangle", "flags": {}, "super_row": sr,
            "super_column": sc, "table": 0, "row_struct": {"rows": rows}}


@pytest.fixture()
def proj(client):
    """4 pages: odd pages carry the table (pattern 1,0), 30 rows each,
    c = a + b holds except in one row; d is noise."""
    name = f"_pytest_ddecl_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", ["text_cell", "numerical_cell"])
    ann = PROJECTS_ROOT / name / "annotations"
    rnd = random.Random(4)
    for p in range(1, 5):
        if p % 2 == 0:
            shapes = []
        else:
            names, a, b, c, d = [], [], [], [], []
            for i in range(30):
                x, y = rnd.randint(1, 400), rnd.randint(1, 400)
                names.append(f"Place{p}_{i}")
                a.append(str(x)); b.append(str(y))
                c.append(str(x + y + (7 if (p, i) == (1, 3) else 0)))
                d.append(str(rnd.randint(1, 900)))
            shapes = [_cell("text_cell", 0, 1, 1, names),
                      _cell("numerical_cell", 100, 1, 2, a),
                      _cell("numerical_cell", 200, 1, 3, b),
                      _cell("numerical_cell", 300, 1, 4, c),
                      _cell("numerical_cell", 400, 1, 5, d)]
        (ann / f"pg_{p}.json").write_text(json.dumps(
            {"imagePath": f"pg_{p}.jpg", "flags": {}, "shapes": shapes}), encoding="utf-8")
    try:
        yield name, str(ann)
    finally:
        shutil.rmtree(PROJECTS_ROOT / name, ignore_errors=True)


def _decl(**over):
    d = {"name": "tbl", "scope": {"labels": ["text_cell", "numerical_cell"], "pattern": "1,0"},
         "record": {"unit": "internal_row",
                    "key": {"slot": 1, "column": 1, "dtype": "text"}},
         "variables": [{"name": "place", "column": 1, "dtype": "text"},
                       {"name": "a", "column": 2, "dtype": "int"},
                       {"name": "b", "column": 3, "dtype": "int"},
                       {"name": "c", "column": 4, "dtype": "int"},
                       {"name": "d", "column": 5, "dtype": "int"}]}
    d.update(over)
    return d


def test_save_load_backup_delete(client, proj):
    _, folder = proj
    r = client.put(f"/api/dataset/tbl/declaration?folder={folder}", json={"declaration": _decl()})
    assert r.status_code == 200, r.text
    ds = client.get(f"/api/datasets?folder={folder}").json()["datasets"]
    assert [x["name"] for x in ds] == ["tbl"]
    got = client.get(f"/api/dataset/tbl/declaration?folder={folder}").json()["declaration"]
    assert got["variables"][1]["name"] == "a"
    # second save keeps the previous version as .prev
    d2 = _decl(identities=[{"total": "c", "parts": ["a", "b"]}])
    assert client.put(f"/api/dataset/tbl/declaration?folder={folder}",
                      json={"declaration": d2}).status_code == 200
    ddir = PROJECTS_ROOT / proj[0] / "datasets"
    assert (ddir / "tbl.dataset.json.prev").exists()
    # delete = rename, no longer listed
    assert client.delete(f"/api/dataset/tbl/declaration?folder={folder}").status_code == 200
    assert client.get(f"/api/datasets?folder={folder}").json()["datasets"] == []
    assert any(p.name.startswith("tbl.dataset.json.deleted-") for p in ddir.iterdir())


def test_save_refuses_invalid(client, proj):
    _, folder = proj
    bad = _decl(identities=[{"total": "c", "parts": ["nope"]}])
    r = client.put(f"/api/dataset/tbl/declaration?folder={folder}", json={"declaration": bad})
    assert r.status_code == 400 and "unknown variable" in r.json()["detail"]
    clash = _decl(parse={"decimal": ",", "thousands": [","]})
    r = client.put(f"/api/dataset/tbl/declaration?folder={folder}", json={"declaration": clash})
    assert r.status_code == 400 and "decimal" in r.json()["detail"]
    r = client.put(f"/api/dataset/bad%20name/declaration?folder={folder}", json={"declaration": _decl()})
    assert r.status_code == 400


def test_page_map_matches_pattern(client, proj):
    _, folder = proj
    m = client.post(f"/api/dataset/page-map?folder={folder}", json={"pattern": "1,0"}).json()
    assert m["stems"] == ["pg_1", "pg_2", "pg_3", "pg_4"]
    assert m["slots"] == [1, None, 1, None]
    m = client.post(f"/api/dataset/page-map?folder={folder}",
                    json={"pattern": "1,0", "pages": "3-4"}).json()
    assert m["slots"] == [None, None, 1, None]


def test_preview_builds_without_saving(client, proj):
    _, folder = proj
    d = _decl(identities=[{"total": "c", "parts": ["a", "b"]}])
    r = client.post(f"/api/dataset/preview?folder={folder}", json={"declaration": d})
    p = r.json()
    assert r.status_code == 200 and not p["problems"]
    assert p["records"] == 60 and p["pages"] == 2
    a = next(v for v in p["variables"] if v["name"] == "a")
    assert a["ok"] == 60 and a["error"] == 0 and a["samples"]
    assert p["identities"][0]["ok"] == 59 and p["identities"][0]["mismatch"] == 1
    assert not (PROJECTS_ROOT / proj[0] / "datasets" / "tbl.dataset.json").exists()
    # semantic problems are returned, not raised
    p2 = client.post(f"/api/dataset/preview?folder={folder}",
                     json={"declaration": _decl(scope={"pattern": "0"})}).json()
    assert p2["problems"]


def test_suggest_finds_the_real_sum_only(client, proj):
    _, folder = proj
    r = client.post(f"/api/dataset/suggest-identities?folder={folder}",
                    json={"declaration": _decl()})
    s = r.json()["suggestions"]
    assert r.status_code == 200
    found = {(x["total"], tuple(x["parts"])) for x in s}
    assert ("c", ("a", "b")) in found
    top = next(x for x in s if x["total"] == "c")
    assert top["hold"] == 59 and top["testable"] == 60 and top["strength"] == "strong"
    assert not any(x["total"] == "d" for x in s)          # noise column: nothing
