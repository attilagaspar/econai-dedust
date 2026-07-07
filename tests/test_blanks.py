"""Structural-blank ink scan: mark inkless lattice cells `blank`, honor the
flag on export, keep it through the rows-PATCH whitelist."""
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

import app.server as srv


def test_band_ink_discriminates():
    # inked band → has ink; uniform paper → blank. (Exact Otsu value is
    # unconstrained for degenerate inputs; the discrimination is what matters.)
    g = np.full((40, 40), 240, np.uint8)
    g[10:20, 10:30] = 30
    assert srv._band_has_ink(g, 0, 40, srv._otsu_threshold_np(g))
    blank = np.full((40, 40), 245, np.uint8)
    tb = min(srv._otsu_threshold_np(blank), 180)
    assert not srv._band_has_ink(blank, 0, 40, tb)


@pytest.fixture()
def blank_page(tmp_path):
    """A page image + JSON: col 1 has ink ('7'), col 2 is truly blank."""
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)
    img = Image.new("RGB", (400, 200), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 120, 90], outline="black")
    d.text((45, 45), "7", fill="black")            # inked cell
    d.rectangle([140, 20, 240, 90], outline="black")  # empty cell (only border)
    img.save(proj / "b1.jpg")
    shapes = [
        {"label": "cell", "points": [[20, 20], [120, 90]], "shape_type": "rectangle",
         "flags": {}, "super_row": 1, "super_column": 1, "table": 0},
        {"label": "cell", "points": [[140, 20], [240, 90]], "shape_type": "rectangle",
         "flags": {}, "super_row": 1, "super_column": 2, "table": 0},
    ]
    (proj / "b1.json").write_text(json.dumps(
        {"shapes": shapes, "imagePath": "b1.jpg",
         "imageHeight": 200, "imageWidth": 400}), encoding="utf-8")
    return proj


def _shapes(folder):
    return json.loads((folder / "b1.json").read_text(encoding="utf-8"))["shapes"]


def test_mark_blanks_marks_only_empty_cell(client, blank_page):
    r = client.post("/api/batch/mark_blanks", params={"folder": str(blank_page)},
                    json={"stems": ["b1"]})
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["cells_blank"] == 1
    sh = _shapes(blank_page)
    assert not sh[0].get("blank")      # the '7' cell has ink
    assert sh[1].get("blank") is True  # the empty cell is flagged


def test_mark_blanks_idempotent_and_clears(client, blank_page):
    client.post("/api/batch/mark_blanks", params={"folder": str(blank_page)},
                json={"stems": ["b1"]})
    # a second run marks nothing new
    r = client.post("/api/batch/mark_blanks", params={"folder": str(blank_page)},
                    json={"stems": ["b1"]})
    assert r.json()["totals"]["cells_blank"] == 0


def test_blank_exports_as_empty_even_with_stray_ocr(client, blank_page):
    # give the blank cell a stray OCR dash, then mark + export
    sh = _shapes(blank_page)
    sh[1]["tesseract_output"] = {"ocr_text": "-"}
    client.put("/api/page/shapes", params={"folder": str(blank_page), "stem": "b1"},
               json={"shapes": sh})
    client.post("/api/batch/mark_blanks", params={"folder": str(blank_page)},
                json={"stems": ["b1"]})
    import io, openpyxl
    r = client.get("/api/export/excel", params={"folder": str(blank_page),
                   "scope": "page", "stem": "b1", "layer": "best_ocr"})
    assert r.status_code == 200
    vals = {str(c.value) for row in openpyxl.load_workbook(io.BytesIO(r.content))
            .worksheets[0].iter_rows() for c in row if c.value}
    assert "-" not in vals             # the blank cell's stray dash is suppressed


def test_blank_survives_rows_patch(client, page_folder):
    # a per-row blank flag must not be wiped when rows are rebuilt
    rows = [{"y0": 100.0, "y1": 150.0, "ocr": "", "llm": "", "human": "", "blank": True},
            {"y0": 150.0, "y1": 200.0, "ocr": "5", "llm": "", "human": ""}]
    r = client.patch("/api/page/shape/rows",
                     params={"folder": str(page_folder), "stem": "p1", "idx": 1},
                     json={"rows": rows, "origin": "manual"})
    assert r.status_code == 200
    saved = json.loads((page_folder / "p1.json").read_text(encoding="utf-8"))
    assert saved["shapes"][1]["row_struct"]["rows"][0]["blank"] is True
