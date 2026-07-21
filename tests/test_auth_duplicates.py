"""Authority duplicate report (POST /api/authority/duplicates) and the
targeted per-row edit endpoint (PATCH /api/page/shape/row-field) that the
report's editable table saves through."""
import json

import pytest
from PIL import Image


def _auth(id_, name):
    return {"id": id_, "name": name, "type": "settlement",
            "score": 100, "source": "auto", "ts": "t"}


@pytest.fixture()
def dup_folder(tmp_path):
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)

    def cell(sr, sc, rows=None, auth=None, human=None):
        sh = {"label": "cell", "points": [[sc * 100, sr * 50], [sc * 100 + 90, sr * 50 + 40]],
              "shape_type": "rectangle", "flags": {},
              "super_row": sr, "super_column": sc, "table": 0}
        if human is not None:
            sh["human_output"] = {"human_corrected_text": human}
        if auth:
            sh["authority"] = auth
        if rows is not None:
            sh["row_struct"] = {"version": 1, "origin": "t", "rows": [
                dict({"n": i + 1, "y0": 10.0 + 20 * i, "y1": 30.0 + 20 * i,
                      "ocr": "", "llm": "", "human": r.get("human", "")},
                     **({"authority": r["auth"]} if r.get("auth") else {}))
                for i, r in enumerate(rows)]}
        return sh

    def page(stem, shapes):
        (proj / f"{stem}.json").write_text(json.dumps(
            {"shapes": shapes, "imagePath": f"{stem}.jpg",
             "imageWidth": 400, "imageHeight": 300}), encoding="utf-8")
        Image.new("RGB", (400, 300), "white").save(proj / f"{stem}.jpg")

    # Kisvaszar (M1) resolved on p1 row 1 AND p2 whole-cell → duplicate.
    # Aporka (M2) resolved once → not in the report.
    blank_cell = {"label": "cell", "points": [[300, 50], [390, 90]],
                  "shape_type": "rectangle", "flags": {}, "blank": True,
                  "super_row": 1, "super_column": 3, "table": 0,
                  "human_output": {"human_corrected_text": "smudge"}}
    page("p1", [cell(1, 1, rows=[
        {"human": "Kisvaszar", "auth": _auth("M1", "Kisvaszar")},
        {"human": "Aporka", "auth": _auth("M2", "Aporka")}])])
    page("p2", [cell(1, 1, auth=_auth("M1", "Kisvaszar"), human="Kis-Vaszar"),
                cell(1, 2, human="no auth here"),
                blank_cell])
    return proj


def test_duplicates_grouped(client, dup_folder):
    r = client.post("/api/authority/duplicates", params={"folder": str(dup_folder)},
                    json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total_resolved"] == 3 and d["distinct_entities"] == 2
    assert d["duplicate_entities"] == 1
    g = d["groups"][0]
    assert g["id"] == "M1" and g["count"] == 2
    kinds = {(it["stem"], it["row_i"]) for it in g["items"]}
    assert kinds == {("p1", 0), ("p2", None)}
    row_item = next(it for it in g["items"] if it["stem"] == "p1")
    assert row_item["human"] == "Kisvaszar" and row_item["y0"] == 10.0


def test_duplicates_col_filter(client, dup_folder):
    # column 2 has no resolutions → empty report
    r = client.post("/api/authority/duplicates", params={"folder": str(dup_folder)},
                    json={"col_filter": "2"})
    assert r.json()["duplicate_entities"] == 0


def test_duplicates_pattern(client, dup_folder):
    # pattern 1,0 keeps only p1 → M1 appears once → no duplicates
    r = client.post("/api/authority/duplicates", params={"folder": str(dup_folder)},
                    json={"pattern": "1,0"})
    assert r.json()["duplicate_entities"] == 0


def test_unresolved_report(client, dup_folder):
    # counterpart mode: text present + no authority; blanks skipped;
    # resolved units excluded; grouped by folded text
    r = client.post("/api/authority/duplicates", params={"folder": str(dup_folder)},
                    json={"unresolved": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mode"] == "unresolved"
    assert d["duplicate_entities"] == 1          # only "no auth here"
    g = d["groups"][0]
    assert g["name"] == "no auth here" and g["count"] == 1 and g["type"] is None
    it = g["items"][0]
    assert (it["stem"], it["row_i"], it["authority"]) == ("p2", None, None)
    # min_count 2 in unresolved mode hides the single occurrence
    r2 = client.post("/api/authority/duplicates", params={"folder": str(dup_folder)},
                     json={"unresolved": True, "min_count": 2})
    assert r2.json()["duplicate_entities"] == 0


def test_lookup_report(client, dup_folder):
    # by id (M2 = single occurrence → still listed), by accent-folded name
    # ("kisvaszar" matches Kisvaszar), unknown terms land in not_found;
    # groups follow the order of the terms
    r = client.post("/api/authority/duplicates", params={"folder": str(dup_folder)},
                    json={"lookup": ["M2", "kisvaszar", "Nincsilyen"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mode"] == "lookup"
    assert [g["id"] for g in d["groups"]] == ["M2", "M1"]     # term order
    assert d["groups"][0]["count"] == 1                       # singles listed
    assert d["groups"][1]["count"] == 2
    assert d["not_found"] == ["Nincsilyen"]


def test_lookup_respects_page_and_col_filters(client, dup_folder):
    r = client.post("/api/authority/duplicates", params={"folder": str(dup_folder)},
                    json={"lookup": ["M1"], "pattern": "1,0"})   # p1 only
    d = r.json()
    assert d["groups"][0]["count"] == 1
    assert d["groups"][0]["items"][0]["stem"] == "p1"


def test_row_field_patch_human_and_authority(client, dup_folder):
    params = {"folder": str(dup_folder), "stem": "p1", "idx": 0}
    # human edit on row 0 also syncs the flat human layer
    r = client.patch("/api/page/shape/row-field", params=params,
                     json={"row_i": 0, "human": "Kisvaszar javítva"})
    assert r.status_code == 200, r.text
    sh = json.loads((dup_folder / "p1.json").read_text(encoding="utf-8"))["shapes"][0]
    assert sh["row_struct"]["rows"][0]["human"] == "Kisvaszar javítva"
    assert sh["human_output"]["human_corrected_text"].split("\n")[0] == "Kisvaszar javítva"
    assert sh["row_struct"]["rows"][1]["human"] == "Aporka"     # untouched

    # authority clear + assign, other rows untouched
    r = client.patch("/api/page/shape/row-field", params=params,
                     json={"row_i": 0, "set_authority": True, "authority": None})
    assert r.status_code == 200
    sh = json.loads((dup_folder / "p1.json").read_text(encoding="utf-8"))["shapes"][0]
    assert "authority" not in sh["row_struct"]["rows"][0]
    assert sh["row_struct"]["rows"][1]["authority"]["id"] == "M2"

    new_a = _auth("M9", "Vaszar")
    r = client.patch("/api/page/shape/row-field", params=params,
                     json={"row_i": 0, "set_authority": True, "authority": new_a})
    assert r.status_code == 200
    sh = json.loads((dup_folder / "p1.json").read_text(encoding="utf-8"))["shapes"][0]
    assert sh["row_struct"]["rows"][0]["authority"]["id"] == "M9"


def test_row_field_patch_bounds(client, dup_folder):
    params = {"folder": str(dup_folder), "stem": "p1", "idx": 0}
    assert client.patch("/api/page/shape/row-field", params=params,
                        json={"row_i": 99, "human": "x"}).status_code == 400
