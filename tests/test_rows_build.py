"""POST /api/rows/build — the single row-structure step (detect / anchor-project)."""
import json

import pytest
from PIL import Image


@pytest.fixture()
def rb_folder(tmp_path):
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)
    Image.new("RGB", (500, 300), "white").save(proj / "p1.jpg")

    def cell(col, y0, y1, human="", ocr="", blank=False, rs=None):
        sh = {"label": "cell", "points": [[col * 100, y0], [col * 100 + 90, y1]],
              "shape_type": "rectangle", "flags": {},
              "super_row": 1, "super_column": col, "table": 0,
              "human_output": {"human_corrected_text": human},
              "tesseract_output": {"ocr_text": ocr}}
        if blank:
            sh["blank"] = True
        if rs:
            sh["row_struct"] = rs
        return sh

    # lattice row 1: col1 = anchor (3 human lines), col2 = empty, col3 = blank
    shapes = [
        cell(1, 40, 130, human="a\nb\nc"),
        cell(2, 40, 130),
        cell(3, 40, 130, blank=True),
    ]
    (proj / "p1.json").write_text(json.dumps(
        {"shapes": shapes, "imagePath": "p1.jpg", "imageWidth": 500, "imageHeight": 300}),
        encoding="utf-8")
    return proj


def _shapes(folder):
    return json.loads((folder / "p1.json").read_text(encoding="utf-8"))["shapes"]


def test_detect_no_anchor_layer_count(client, rb_folder):
    # no anchor → each eligible cell gets its own structure; source=human sets
    # col1's count to 3; col2 has no human → no_rows; col3 blank → skipped
    r = client.post("/api/rows/build", params={"folder": str(rb_folder)},
                    json={"source": "human"})
    assert r.status_code == 200, r.text
    t = r.json()["totals"]
    assert t["built"] == 1 and t["no_rows"] == 1 and t["skipped"] == 1
    sh = _shapes(rb_folder)
    assert len(sh[0]["row_struct"]["rows"]) == 3
    assert [rw["human"] for rw in sh[0]["row_struct"]["rows"]] == ["a", "b", "c"]
    assert "row_struct" not in sh[1] and "row_struct" not in sh[2]


def test_anchor_projection(client, rb_folder):
    # anchor col 1 (3 human lines) → projected onto col 2; blank col 3 skipped
    r = client.post("/api/rows/build", params={"folder": str(rb_folder)},
                    json={"source": "human", "anchor_pattern": "1"})
    assert r.status_code == 200, r.text
    t = r.json()["totals"]
    assert t["built"] == 1 and t["projected"] == 1
    sh = _shapes(rb_folder)
    assert len(sh[1]["row_struct"]["rows"]) == 3        # projected
    assert sh[1]["row_struct"]["origin"] == "projected"
    assert "row_struct" not in sh[2]                    # blank never touched


def test_anchor_project_existing_structure(client, rb_folder):
    # give col 1 a hand-made structure (2 rows), then project it verbatim onto
    # col 2 with source=existing — the exact y-bands must transfer
    sh = _shapes(rb_folder)
    sh[0]["row_struct"] = {"version": 1, "origin": "manual", "rows": [
        {"n": 1, "y0": 45.0, "y1": 88.0, "ocr": "", "llm": "", "human": ""},
        {"n": 2, "y0": 88.0, "y1": 128.0, "ocr": "", "llm": "", "human": ""}]}
    client.put("/api/page/shapes",
               params={"folder": str(rb_folder), "stem": "p1"}, json={"shapes": sh})
    r = client.post("/api/rows/build", params={"folder": str(rb_folder)},
                    json={"source": "existing", "anchor_pattern": "1"})
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["projected"] == 1
    out = _shapes(rb_folder)
    # col1 (anchor) kept its structure; col2 got 2 rows projected to its own bbox
    assert len(out[0]["row_struct"]["rows"]) == 2
    assert out[1]["row_struct"]["origin"] == "projected"
    assert len(out[1]["row_struct"]["rows"]) == 2


def test_overwrite_semantics(client, rb_folder):
    # first build, then a second build without overwrite must not rebuild
    client.post("/api/rows/build", params={"folder": str(rb_folder)},
                json={"source": "human"})
    r = client.post("/api/rows/build", params={"folder": str(rb_folder)},
                    json={"source": "human"})
    assert r.json()["totals"]["built"] == 0            # already has structure
    r2 = client.post("/api/rows/build", params={"folder": str(rb_folder)},
                     json={"source": "human", "overwrite": True})
    assert r2.json()["totals"]["built"] == 1


def test_col_filter(client, rb_folder):
    # restrict to column 2 → nothing built (col2 has no human text, no anchor)
    r = client.post("/api/rows/build", params={"folder": str(rb_folder)},
                    json={"source": "human", "col_filter": "2"})
    assert r.json()["totals"]["built"] == 0


def test_anchor_empty_slot_skips_page(client, rb_folder):
    # pattern ",1": page 0's slot is empty → that page is skipped (the non-empty
    # slot elsewhere makes it a real anchor pattern, not a no-anchor blank)
    r = client.post("/api/rows/build", params={"folder": str(rb_folder)},
                    json={"source": "human", "anchor_pattern": ",1", "stems": ["p1"]})
    assert r.json()["totals"]["pages"] == 0     # only page skipped
    r2 = client.post("/api/rows/build", params={"folder": str(rb_folder)},
                     json={"source": "human", "anchor_pattern": "1,", "stems": ["p1"]})
    assert r2.json()["totals"]["pages"] == 1    # page 0 slot "1" → anchored
