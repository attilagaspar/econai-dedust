"""P1 review queue + P3 project status."""
import json

import pytest


@pytest.fixture()
def review_folder(tmp_path):
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)

    def cell(row, ocr, llm, human="", col=1, blank=False):
        sh = {"label": "cell", "points": [[10, row * 40], [200, row * 40 + 35]],
              "shape_type": "rectangle", "flags": {},
              "super_row": row, "super_column": col, "table": 0,
              "tesseract_output": {"ocr_text": ocr},
              "openai_output": {"response": llm},
              "human_output": {"human_corrected_text": human}}
        if blank:
            sh["blank"] = True
        return sh

    shapes = [
        cell(1, "100", "100"),          # agree, unverified
        cell(2, "250", "256"),          # DISAGREE
        cell(3, "99999", "99999"),      # OUTLIER (rest of column is ~120)
        cell(4, "120", "120"),
        cell(5, "130", "130"),
        cell(6, "110", "110"),
        cell(7, "140", "140"),
        cell(8, "5", "5", blank=True),  # blank → excluded
    ]
    (proj / "p1.json").write_text(json.dumps(
        {"shapes": shapes, "imagePath": "p1.jpg",
         "imageWidth": 400, "imageHeight": 400}), encoding="utf-8")
    return proj


def test_queue_disagree_and_outlier(client, review_folder):
    r = client.post("/api/review/queue", params={"folder": str(review_folder)},
                    json={"signals": ["disagree", "outlier"]})
    assert r.status_code == 200, r.text
    q = r.json()["queue"]
    # whole-cell shapes → row is None; identify by shape idx (0-based)
    whys = {(it["idx"], it["why"]) for it in q}
    assert (1, "OCR≠LLM") in whys                 # cell(2) is shapes[1]
    assert any(it["idx"] == 2 and "outlier" in it["why"] for it in q)  # cell(3)
    # blank cell (shapes[7]) never appears
    assert all(it["idx"] != 7 for it in q)
    # disagree (sev 4) ranks above outlier (sev 3)
    assert q[0]["sev"] >= q[-1]["sev"]


def test_queue_unverified_signal(client, review_folder):
    r = client.post("/api/review/queue", params={"folder": str(review_folder)},
                    json={"signals": ["unverified"]})
    idxs = {it["idx"] for it in r.json()["queue"]}
    assert 0 in idxs and 7 not in idxs      # agree-but-unverified in; blank out


def test_queue_excludes_verified_pages(client, review_folder):
    client.patch("/api/page/flags",
                 params={"folder": str(review_folder), "stem": "p1"},
                 json={"flags": {"status": "verified"}})
    r = client.post("/api/review/queue", params={"folder": str(review_folder)},
                    json={"signals": ["disagree"], "exclude_verified": True})
    assert r.json()["total"] == 0
    r2 = client.post("/api/review/queue", params={"folder": str(review_folder)},
                     json={"signals": ["disagree"], "exclude_verified": False})
    assert r2.json()["total"] >= 1


def test_project_status_counts(client, review_folder):
    r = client.get("/api/project/status", params={"folder": str(review_folder)})
    d = r.json()
    assert d["total"] == 1 and d["counts"]["predicted"] == 1
    client.patch("/api/page/flags",
                 params={"folder": str(review_folder), "stem": "p1"},
                 json={"flags": {"status": "verified"}})
    d2 = client.get("/api/project/status", params={"folder": str(review_folder)}).json()
    assert d2["counts"]["verified"] == 1 and d2["counts"]["predicted"] == 0


def test_cell_band_crop(client, page_folder):
    # /api/cell with y0/y1 returns a JPEG (row-band snippet for the strip)
    r = client.get("/api/cell", params={"folder": str(page_folder), "stem": "p1",
                                        "idx": 1, "y0": 100, "y1": 150})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
