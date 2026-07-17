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
    page("p1", [cell(1, 1, rows=[
        {"human": "Kisvaszar", "auth": _auth("M1", "Kisvaszar")},
        {"human": "Aporka", "auth": _auth("M2", "Aporka")}])])
    page("p2", [cell(1, 1, auth=_auth("M1", "Kisvaszar"), human="Kis-Vaszar"),
                cell(1, 2, human="no auth here")])
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
