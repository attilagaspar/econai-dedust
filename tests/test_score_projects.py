"""Per-project scoring of a pooled model and the training-candidate list
(roadmap P13, phase 3). The GPU run itself is exercised manually; these
cover the script, the target selection, the local scoring and the gates."""
import json
import shutil
import uuid

import pytest

from app.coco_convert import make_score_script
from app.pipeline import create_project, PROJECTS_ROOT
from app.server import _score_targets, _score_pulled
from app.training_meta import freeze_test_set

PALETTE = ["start", "stop", "ignore", "firm"]
CLASSES = ["start", "stop", "ignore"]


def _box(label, x1, y1, x2, y2):
    return {"label": label, "points": [[x1, y1], [x2, y2]],
            "shape_type": "rectangle", "flags": {}}


def _page(ann_dir, stem, shapes, status="verified", image=None):
    ann_dir.mkdir(parents=True, exist_ok=True)
    (ann_dir / f"{stem}.json").write_text(json.dumps({
        "version": "5.0.0", "flags": {"status": status}, "shapes": shapes,
        "imagePath": image or f"{stem}.png", "imageWidth": 800, "imageHeight": 1000,
    }), encoding="utf-8")


def test_score_script_links_test_images_without_copying():
    sh = make_score_script("pool", CLASSES, [
        {"project": "y1", "images": ["a.png", "b c.png"]}], ws="/workspace/me/")
    assert "rm -rf /workspace/me/pool/eval/y1 && mkdir -p /workspace/me/pool/eval/y1/images" in sh
    assert "ln -s ../../../../y1/images/a.png a.png" in sh
    assert "ln -s '../../../../y1/images/b c.png' 'b c.png'" in sh
    assert "--weights /workspace/me/layout-model-training/outputs/pool/" in sh
    assert "--labels start stop ignore" in sh and "--output /workspace/me/pool/eval/y1/pred" in sh


@pytest.fixture()
def series():
    tag = uuid.uuid4().hex[:6]
    pool, y1, y2, odd = (f"_pytest_score_{k}_{tag}" for k in ("pool", "y1", "y2", "odd"))
    for n in (pool, y1, y2):
        create_project(n, "A", PALETTE)
    create_project(odd, "A", ["start", "table"])
    _page(PROJECTS_ROOT / y1 / "annotations", "t1",
          [_box("start", 10, 10, 100, 40), _box("firm", 0, 0, 700, 500)], image="t1.jpg")
    _page(PROJECTS_ROOT / y1 / "annotations", "t2", [_box("stop", 10, 10, 100, 40)])
    _page(PROJECTS_ROOT / y2 / "annotations", "u1", [_box("ignore", 0, 0, 800, 60)])
    freeze_test_set(PROJECTS_ROOT / y1, stems=["t1"])
    freeze_test_set(PROJECTS_ROOT / y2, stems=["u1"])
    try:
        yield pool, y1, y2, odd
    finally:
        for n in (pool, y1, y2, odd):
            shutil.rmtree(PROJECTS_ROOT / n, ignore_errors=True)


def test_targets_are_sources_and_held_out_projects_with_test_sets(series):
    pool, y1, y2, _ = series
    cfg = {"labels": PALETTE, "training_sources": [y1], "eval_projects": [y2]}
    t = _score_targets(pool, cfg, CLASSES)               # pool has no test set → left out
    assert [(x["project"], x["role"], x["stems"], x["images"]) for x in t] == [
        (y1, "train", ["t1"], ["t1.jpg"]), (y2, "held out", ["u1"], ["u1.png"])]


def test_target_without_a_model_class_is_refused(series, client):
    pool, y1, _, odd = series
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        _score_targets(pool, {"labels": PALETTE, "eval_projects": [odd]}, CLASSES)
    assert e.value.status_code == 400 and odd in e.value.detail


def test_scoring_reads_pulled_predictions_and_counts_only_classes(series):
    pool, y1, _, _ = series
    t = _score_targets(pool, {"labels": PALETTE, "training_sources": [y1]}, CLASSES)
    pred = PROJECTS_ROOT / pool / "intermediate" / "eval" / y1
    _page(pred, "t1", [_box("start", 10, 10, 100, 40), _box("stop", 300, 300, 400, 340)])
    s = _score_pulled(pool, t, CLASSES)[y1]
    assert s["role"] == "train" and s["n_pages"] == 1
    assert s["totals"] == {"added": 0, "deleted": 1, "moved": 0, "ok": 1}   # firm not counted
    assert s["corrections_per_page"] == 1.0


def test_candidates_mark_roles_and_label_gaps(series, client):
    pool, y1, y2, odd = series
    client.patch(f"/api/project/{pool}/config",
                 json={"train_labels": CLASSES, "training_sources": [y1], "eval_projects": [y2]})
    r = client.get(f"/api/project/{pool}/training-candidates")
    assert r.status_code == 200
    d = r.json()
    assert d["classes"] == CLASSES
    rows = {c["name"]: c for c in d["candidates"]}
    assert rows[y1]["role"] == "train" and rows[y1]["n_trainable"] == 2 and rows[y1]["n_test"] == 1
    assert rows[y2]["role"] == "held out"
    assert rows[odd]["missing"] == ["stop", "ignore"] and "n_trainable" not in rows[odd]
    assert pool not in rows


def test_score_projects_gates(series, client):
    pool, y1, _, _ = series
    r = client.post(f"/api/project/{pool}/score-projects", json={})
    assert r.status_code == 400 and "no trained model" in r.json()["detail"]
    inter = PROJECTS_ROOT / pool / "intermediate"
    inter.mkdir(parents=True, exist_ok=True)
    (inter / "train_summary.json").write_text(json.dumps({"model_labels": CLASSES}), encoding="utf-8")
    r = client.post(f"/api/project/{pool}/score-projects", json={})
    assert r.status_code == 400 and "frozen test set" in r.json()["detail"]
    client.patch(f"/api/project/{pool}/config", json={"training_sources": [y1]})
    r = client.post(f"/api/project/{pool}/score-projects", json={})
    assert r.status_code == 400 and "Server" in r.json()["detail"]        # next gate: GPU server
