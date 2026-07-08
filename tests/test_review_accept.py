"""POST /api/review/accept — the shared review-decision endpoint
(desktop strip + mobile page). Empty value → structural blank; the returned
`prev` + `restore_blank` implement exact undo."""
import json

import pytest


@pytest.fixture()
def accept_folder(tmp_path):
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)
    shapes = [
        {  # 0: whole cell, OCR≠LLM
            "label": "cell", "points": [[10, 10], [200, 45]],
            "shape_type": "rectangle", "flags": {},
            "super_row": 1, "super_column": 1, "table": 0,
            "tesseract_output": {"ocr_text": "250"},
            "openai_output": {"response": "256"},
            "human_output": {"human_corrected_text": ""},
        },
        {  # 1: cell with internal rows, row 2 disagrees
            "label": "cell", "points": [[10, 50], [200, 130]],
            "shape_type": "rectangle", "flags": {},
            "super_row": 2, "super_column": 1, "table": 0,
            "tesseract_output": {"ocr_text": "10\n20"},
            "openai_output": {"response": "10\n21"},
            "row_struct": {"rows": [
                {"n": 1, "y0": 50, "y1": 90,  "ocr": "10", "llm": "10", "human": ""},
                {"n": 2, "y0": 90, "y1": 130, "ocr": "20", "llm": "21", "human": ""},
            ]},
        },
    ]
    (proj / "p1.json").write_text(json.dumps(
        {"shapes": shapes, "imagePath": "p1.jpg",
         "imageWidth": 400, "imageHeight": 400}), encoding="utf-8")
    return proj


def _accept(client, folder, **body):
    return client.post("/api/review/accept", params={"folder": str(folder)},
                       json=body)


def _load(folder):
    return json.loads((folder / "p1.json").read_text(encoding="utf-8"))["shapes"]


def test_accept_value_whole_cell(client, accept_folder):
    r = _accept(client, accept_folder, stem="p1", idx=0, value="253")
    assert r.status_code == 200
    d = r.json()
    assert d["blank"] is False
    assert d["prev"] == {"human": "", "blank": False}
    sh = _load(accept_folder)[0]
    assert sh["human_output"]["human_corrected_text"] == "253"
    assert "blank" not in sh
    # resolved → out of the queue
    q = client.post("/api/review/queue", params={"folder": str(accept_folder)},
                    json={"signals": ["disagree"]}).json()["queue"]
    assert all(not (it["idx"] == 0 and it["row"] is None) for it in q)


def test_accept_empty_marks_blank(client, accept_folder):
    r = _accept(client, accept_folder, stem="p1", idx=0, value="  ")
    assert r.json()["blank"] is True
    sh = _load(accept_folder)[0]
    assert sh["blank"] is True
    q = client.post("/api/review/queue", params={"folder": str(accept_folder)},
                    json={"signals": ["disagree", "unverified"]}).json()["queue"]
    assert all(not (it["idx"] == 0 and it["row"] is None) for it in q)


def test_accept_value_clears_blank(client, accept_folder):
    _accept(client, accept_folder, stem="p1", idx=0, value="")       # → blank
    _accept(client, accept_folder, stem="p1", idx=0, value="253")    # → value
    sh = _load(accept_folder)[0]
    assert "blank" not in sh
    assert sh["human_output"]["human_corrected_text"] == "253"


def test_undo_via_restore_blank(client, accept_folder):
    d = _accept(client, accept_folder, stem="p1", idx=0, value="253").json()
    # undo = re-post the previous state verbatim
    r = _accept(client, accept_folder, stem="p1", idx=0,
                value=d["prev"]["human"], restore_blank=d["prev"]["blank"])
    assert r.status_code == 200
    sh = _load(accept_folder)[0]
    assert sh["human_output"]["human_corrected_text"] == ""
    assert "blank" not in sh          # restored to unreviewed, NOT to blank
    # and the cell is flagged again
    q = client.post("/api/review/queue", params={"folder": str(accept_folder)},
                    json={"signals": ["disagree"]}).json()["queue"]
    assert any(it["idx"] == 0 for it in q)


def test_accept_row_syncs_flat(client, accept_folder):
    r = _accept(client, accept_folder, stem="p1", idx=1, row=2, value="22")
    assert r.status_code == 200
    sh = _load(accept_folder)[1]
    rows = sh["row_struct"]["rows"]
    assert rows[1]["human"] == "22"
    # flat Human re-derived from the rows
    assert sh["human_output"]["human_corrected_text"] == "\n22"


def test_accept_row_empty_marks_row_blank(client, accept_folder):
    d = _accept(client, accept_folder, stem="p1", idx=1, row=2, value="").json()
    assert d["blank"] is True
    rows = _load(accept_folder)[1]["row_struct"]["rows"]
    assert rows[1]["blank"] is True
    # undo restores the unreviewed row
    _accept(client, accept_folder, stem="p1", idx=1, row=2,
            value=d["prev"]["human"], restore_blank=d["prev"]["blank"])
    rows = _load(accept_folder)[1]["row_struct"]["rows"]
    assert "blank" not in rows[1] and (rows[1]["human"] or "") == ""


def test_accept_bad_row_400(client, accept_folder):
    r = _accept(client, accept_folder, stem="p1", idx=1, row=99, value="x")
    assert r.status_code == 400


def test_accept_bad_idx_400(client, accept_folder):
    r = _accept(client, accept_folder, stem="p1", idx=42, value="x")
    assert r.status_code == 400
