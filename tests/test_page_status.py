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


# ── the guarantee behind "leave this page unannotated": a page whose status is
#    "skip" is NEVER populated by apply-predictions ────────────────────────────

def test_apply_predictions_never_populates_skip_pages(client, tmp_path, monkeypatch):
    import app.pipeline as pipeline
    monkeypatch.setattr(pipeline, "PROJECTS_ROOT", tmp_path)
    ann = tmp_path / "tp" / "annotations"
    pred = tmp_path / "tp" / "predictions"
    ann.mkdir(parents=True); pred.mkdir()
    pshape = [{"label": "cell", "points": [[1, 1], [50, 50]],
               "shape_type": "rectangle", "flags": {}}]
    for stem, flags in [("unannotated", {"status": "skip"}), ("normal", {})]:
        (ann / f"{stem}.json").write_text(json.dumps(
            {"shapes": [], "flags": flags, "imagePath": f"{stem}.jpg",
             "imageWidth": 100, "imageHeight": 100}), encoding="utf-8")
        (pred / f"{stem}.json").write_text(json.dumps({"shapes": pshape}),
                                           encoding="utf-8")
    r = client.post("/api/project/tp/apply-predictions")
    assert r.status_code == 200
    d = r.json()
    assert d["applied"] == 1 and d["skipped_clutter"] == 1
    kept = json.loads((ann / "unannotated.json").read_text())
    assert kept["shapes"] == []                      # rubbish never landed
    normal = json.loads((ann / "normal.json").read_text())
    assert len(normal["shapes"]) == 1                # normal page still populated
