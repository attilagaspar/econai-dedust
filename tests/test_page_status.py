"""Page status: bulk set-status, the 'skip' (clutter) value and its exclusions
(review queue, apply-predictions, scoreboard work-total)."""
import json

import pytest


@pytest.fixture()
def status_folder(tmp_path):
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)

    def page(stem, status=None, disagree=False):
        sh = [{"label": "cell", "points": [[10, 10], [200, 45]],
               "shape_type": "rectangle", "flags": {},
               "super_row": 1, "super_column": 1, "table": 0,
               "tesseract_output": {"ocr_text": "250"},
               "openai_output": {"response": "256" if disagree else "250"},
               "human_output": {"human_corrected_text": ""}}]
        doc = {"shapes": sh, "imagePath": f"{stem}.jpg",
               "imageWidth": 400, "imageHeight": 400}
        if status:
            doc["flags"] = {"status": status}
        (proj / f"{stem}.json").write_text(json.dumps(doc), encoding="utf-8")

    page("p1", disagree=True)
    page("p2", disagree=True)
    page("p3", disagree=True)
    return proj


def test_bulk_set_status(client, status_folder):
    r = client.post("/api/pages/status", params={"folder": str(status_folder)},
                    json={"stems": ["p1", "p3"], "status": "skip"})
    assert r.status_code == 200 and r.json()["changed"] == 2
    for stem, exp in [("p1", "skip"), ("p2", None), ("p3", "skip")]:
        d = json.loads((status_folder / f"{stem}.json").read_text())
        assert (d.get("flags") or {}).get("status") == exp


def test_bulk_set_status_rejects_unknown(client, status_folder):
    r = client.post("/api/pages/status", params={"folder": str(status_folder)},
                    json={"stems": ["p1"], "status": "banana"})
    assert r.status_code == 400


def test_scoreboard_excludes_skip_from_work(client, status_folder):
    client.post("/api/pages/status", params={"folder": str(status_folder)},
                json={"stems": ["p1"], "status": "skip"})
    d = client.get("/api/project/status", params={"folder": str(status_folder)}).json()
    assert d["total"] == 3
    assert d["counts"]["skip"] == 1
    assert d["total_work"] == 2          # clutter dropped from the denominator


def test_review_queue_excludes_skip(client, status_folder):
    # all 3 pages disagree -> all 3 would be flagged; mark p2 skip -> gone
    client.post("/api/pages/status", params={"folder": str(status_folder)},
                json={"stems": ["p2"], "status": "skip"})
    q = client.post("/api/review/queue", params={"folder": str(status_folder)},
                    json={"signals": ["disagree"]}).json()["queue"]
    stems = {it["stem"] for it in q}
    assert "p1" in stems and "p3" in stems and "p2" not in stems
