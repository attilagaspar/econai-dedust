"""Newspaper flow → Markdown export: /api/export/newsflow-md builds one file
from flow_order-stamped shapes (titles as headings, best-text paragraphs,
page + article markers)."""
import json

import pytest


def _shape(label, order, gid, **outputs):
    sh = {"label": label, "points": [[0, 0], [10, 10]], "shape_type": "rectangle",
          "flags": {}, "flow_order": order, "group_id": gid}
    sh.update(outputs)
    return sh


@pytest.fixture()
def md_folder(tmp_path):
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)
    # page n1: carryover text, a 1-col title, text (LLM beats OCR), a 3-col title, image
    (proj / "n1.json").write_text(json.dumps({"shapes": [
        _shape("cikkszoveg", 0, 0, tesseract_output={"ocr_text": "carryover szoveg"}),
        _shape("cim1sav", 1, 1, tesseract_output={"ocr_text": "KULFOLD"}),
        _shape("cikkszoveg", 2, 1,
               tesseract_output={"ocr_text": "ocr valtozat"},
               openai_output={"response": "llm valtozat"}),
        _shape("cim3sav", 3, 2, human_output={"human_corrected_text": "HIREK"}),
        _shape("kep", 4, 2),
    ], "imagePath": "n1.jpg", "imageWidth": 100, "imageHeight": 100}), encoding="utf-8")
    # page n2: never flow-reconstructed
    (proj / "n2.json").write_text(json.dumps({"shapes": [
        {"label": "cikkszoveg", "points": [[0, 0], [5, 5]],
         "shape_type": "rectangle", "flags": {}},
    ], "imagePath": "n2.jpg", "imageWidth": 100, "imageHeight": 100}), encoding="utf-8")
    return proj


def test_newsflow_md_export(client, md_folder):
    r = client.post("/api/export/newsflow-md", params={"folder": str(md_folder)},
                    json={"stems": ["n1", "n2"],
                          "roles": {"cikkszoveg": "text", "cim1sav": "title",
                                    "cim3sav": "title", "kep": "breaker"}})
    assert r.status_code == 200
    assert r.headers["x-econai-pages"] == "1"
    assert r.headers["x-econai-missing"] == "1"
    assert r.headers["x-econai-articles"] == "2"
    md = r.text
    # page markers, both pages, in order
    assert md.index("<!-- ===== page n1 =====") < md.index("<!-- ===== page n2 =====")
    assert "<!-- no flow reconstruction on this page -->" in md
    # carryover marker precedes article 1
    assert md.index("<!-- article n1:0 carryover -->") < md.index("<!-- article n1:1 -->")
    # heading levels from the label's column span
    assert "\n### KULFOLD" in md
    assert "\n# HIREK" in md
    # best-text precedence: LLM beats OCR
    assert "llm valtozat" in md and "ocr valtozat" not in md
    # breaker placeholder
    assert "*[kep]*" in md
    # flow order preserved: carryover text before the first heading
    assert md.index("carryover szoveg") < md.index("### KULFOLD")
