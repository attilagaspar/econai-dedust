"""Model classes as a subset of the palette (roadmap P13, phase 1):
config `train_labels`, the export, fine-tune's class check and scoring."""
import json
import shutil
import uuid

import pytest

from app.coco_convert import prepare_training_data
from app.eval_diff import evaluate_test_set
from app.pipeline import create_project, PROJECTS_ROOT

PALETTE = ["start", "stop", "ignore", "firm"]


def _box(label, x1, y1, x2, y2, **extra):
    return {"label": label, "points": [[x1, y1], [x2, y2]],
            "shape_type": "rectangle", "flags": {}, **extra}


def _page(ann_dir, stem, shapes, status="verified"):
    (ann_dir / f"{stem}.json").write_text(json.dumps({
        "version": "5.0.0", "flags": {"status": status}, "shapes": shapes,
        "imagePath": f"{stem}.jpg", "imageWidth": 800, "imageHeight": 1000,
    }), encoding="utf-8")


@pytest.fixture()
def ann_dir(tmp_path):
    d = tmp_path / "proj" / "annotations"
    d.mkdir(parents=True)
    _page(d, "p1", [_box("ignore", 0, 0, 800, 80), _box("start", 50, 100, 400, 130),
                    _box("firm", 40, 95, 760, 600)])
    _page(d, "p2", [_box("firm", 40, 95, 760, 900)])          # only a non-class
    _page(d, "p3", [_box("start", 50, 100, 400, 130)], status="predicted")
    return d


def _prepare(ann_dir, **kw):
    inter = ann_dir.parent / "intermediate"
    base = ann_dir.parent / "base.yaml"
    base.write_text("MODEL:\n  ROI_HEADS:\n    NUM_CLASSES: 9\n", encoding="utf-8")
    return prepare_training_data("p", ann_dir, PALETTE, inter, base, **kw), inter


def test_subset_drops_other_labels_and_keeps_palette_order(ann_dir):
    res, inter = _prepare(ann_dir, train_labels=["ignore", "start", "stop"])
    assert res["model_labels"] == ["start", "stop", "ignore"]
    coco = json.loads((inter / "annotations.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in coco["categories"]] == ["start", "stop", "ignore"]
    assert len(coco["annotations"]) == 2                     # firm boxes gone
    assert "NUM_CLASSES: 3" in (inter / "configs" / "p" /
                                "fast_rcnn_R_50_FPN_3x.yaml").read_text(encoding="utf-8")


def test_page_with_only_other_labels_is_a_negative(ann_dir):
    res, inter = _prepare(ann_dir, train_labels=["start", "stop", "ignore"])
    assert res["n_train_pages"] == 2                         # p1 + p2; p3 not verified
    assert res["n_negatives"] == 1                           # p2
    assert "FILTER_EMPTY_ANNOTATIONS False" in (inter / "train.sh").read_text(encoding="utf-8")


def test_no_subset_is_the_whole_palette(ann_dir):
    res, _ = _prepare(ann_dir)
    assert res["model_labels"] == PALETTE
    assert res["n_negatives"] == 0


def test_unknown_train_label_is_refused(ann_dir):
    with pytest.raises(ValueError, match="not in the project's labels"):
        _prepare(ann_dir, train_labels=["start", "table"])


def test_scoring_counts_only_the_model_classes(tmp_path):
    ann, pred = tmp_path / "ann", tmp_path / "pred"
    ann.mkdir(); pred.mkdir()
    _page(ann, "t1", [_box("start", 50, 100, 400, 130), _box("firm", 40, 95, 760, 600)])
    _page(pred, "t1", [_box("start", 50, 100, 400, 130)])
    everything = evaluate_test_set(ann, pred, ["t1"])
    classes = evaluate_test_set(ann, pred, ["t1"], labels=["start", "stop", "ignore"])
    assert everything["corrections_total"] == 1              # firm counted as missed
    assert classes["corrections_total"] == 0
    assert "firm" not in classes["per_label"]
    assert classes["labels"] == ["start", "stop", "ignore"]


@pytest.fixture()
def two_projects():
    a = f"_pytest_tl_a_{uuid.uuid4().hex[:6]}"
    b = f"_pytest_tl_b_{uuid.uuid4().hex[:6]}"
    create_project(a, "A", PALETTE)
    create_project(b, "A", PALETTE[:3])
    try:
        yield a, b
    finally:
        shutil.rmtree(PROJECTS_ROOT / a, ignore_errors=True)
        shutil.rmtree(PROJECTS_ROOT / b, ignore_errors=True)


def test_config_train_labels_set_clear_and_refuse(client, two_projects):
    a, _ = two_projects
    r = client.patch(f"/api/project/{a}/config", json={"train_labels": ["ignore", "start", "stop"]})
    assert r.status_code == 200
    assert r.json()["config"]["train_labels"] == ["start", "stop", "ignore"]
    r = client.patch(f"/api/project/{a}/config", json={"train_labels": ["nope"]})
    assert r.status_code == 400
    r = client.patch(f"/api/project/{a}/config", json={"train_labels": PALETTE})
    assert "train_labels" not in r.json()["config"]          # whole palette = cleared


def test_finetune_class_check_uses_the_chosen_classes(client, two_projects):
    a, b = two_projects
    inter = PROJECTS_ROOT / b / "intermediate"
    inter.mkdir(parents=True, exist_ok=True)
    (inter / "train_summary.json").write_text(
        json.dumps({"model_labels": ["start", "stop", "ignore"]}), encoding="utf-8")

    r = client.post(f"/api/project/{a}/finetune-from/{b}", json={})
    assert r.status_code == 400 and "Label mismatch" in r.json()["detail"]

    client.patch(f"/api/project/{a}/config", json={"train_labels": ["start", "stop", "ignore"]})
    r = client.post(f"/api/project/{a}/finetune-from/{b}", json={})
    assert r.status_code == 400 and "Server" in r.json()["detail"]   # next gate: GPU server
