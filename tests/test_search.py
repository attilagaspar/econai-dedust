"""GET /api/search — folder-wide full-text search feeding the editor's
search bar. Rows-aware, case/accent-insensitive, label + layer filters."""
import json

import pytest
from PIL import Image


@pytest.fixture()
def search_folder(tmp_path):
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)

    def page(stem, shapes):
        (proj / f"{stem}.json").write_text(json.dumps(
            {"shapes": shapes, "imagePath": f"{stem}.jpg",
             "imageWidth": 600, "imageHeight": 400}), encoding="utf-8")
        Image.new("RGB", (600, 400), "white").save(proj / f"{stem}.jpg")

    def shape(label, human=None, llm=None, rows=None, sr=None, sc=None):
        sh = {"label": label, "points": [[10, 10], [200, 100]],
              "shape_type": "rectangle", "flags": {}}
        if sr is not None:
            sh["super_row"], sh["super_column"], sh["table"] = sr, sc, 0
        if human is not None:
            sh["human_output"] = {"human_corrected_text": human}
        if llm is not None:
            sh["openai_output"] = {"response": llm}
        if rows is not None:
            sh["row_struct"] = {"version": 1, "origin": "t", "rows": [
                {"n": i + 1, "y0": 10.0 + 20 * i, "y1": 30.0 + 20 * i,
                 "ocr": "", "llm": r.get("llm", ""), "human": r.get("human", "")}
                for i, r in enumerate(rows)]}
        return sh

    page("p1", [
        shape("cell", sr=1, sc=1, rows=[
            {"human": "Tótszentpál"}, {"human": "Varjaskér"}, {"human": ""}]),
        shape("cell", sr=1, sc=2, human="123\n456"),
        shape("header", human="Somogy vármegye adatai"),
    ])
    page("p2", [
        shape("cell", sr=1, sc=1, llm="Tótszentpál község"),
    ])
    return proj


def _search(client, folder, **params):
    p = {"folder": str(folder), "q": "tótszentpál"}
    p.update(params)
    r = client.get("/api/search", params=p)
    assert r.status_code == 200, r.text
    return r.json()


def test_search_rows_and_flat(client, search_folder):
    d = _search(client, search_folder)
    assert d["total"] == 2
    r0, r1 = d["results"]
    assert (r0["stem"], r0["row_n"], r0["layer"]) == ("p1", 1, "human")
    assert (r1["stem"], r1["row_n"], r1["layer"]) == ("p2", None, "llm")


def test_search_accent_and_case_insensitive(client, search_folder):
    assert _search(client, search_folder, q="TOTSZENTPAL")["total"] == 2
    assert _search(client, search_folder, q="varjasker")["total"] == 1


def test_search_label_filter(client, search_folder):
    d = _search(client, search_folder, q="somogy", label="cell")
    assert d["total"] == 0
    d = _search(client, search_folder, q="somogy", label="header")
    assert d["total"] == 1 and d["results"][0]["label"] == "header"


def test_search_layer_filter(client, search_folder):
    # human layer only → the p2 LLM-only cell must not match
    d = _search(client, search_folder, layer="human")
    assert d["total"] == 1 and d["results"][0]["stem"] == "p1"


def test_search_limit_and_truncated(client, search_folder):
    d = _search(client, search_folder, limit=1)
    assert d["total"] == 2 and d["truncated"] and len(d["results"]) == 1


def test_search_empty_query_rejected(client, search_folder):
    r = client.get("/api/search", params={"folder": str(search_folder), "q": "  "})
    assert r.status_code == 400
