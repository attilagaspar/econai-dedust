"""Training on several projects (roadmap P13, phase 2): the multi-project
export, the config fields and the Prepare gate."""
import json
import shutil
import uuid

import pytest

from app.coco_convert import prepare_training_data
from app.pipeline import create_project, PROJECTS_ROOT
from app.server import _source_image_files

PALETTE = ["start", "stop", "ignore"]


def _box(label, x1, y1, x2, y2):
    return {"label": label, "points": [[x1, y1], [x2, y2]],
            "shape_type": "rectangle", "flags": {}}


def _page(ann_dir, stem, shapes, status="verified"):
    ann_dir.mkdir(parents=True, exist_ok=True)
    (ann_dir / f"{stem}.json").write_text(json.dumps({
        "version": "5.0.0", "flags": {"status": status}, "shapes": shapes,
        "imagePath": f"{stem}.jpg", "imageWidth": 800, "imageHeight": 1000,
    }), encoding="utf-8")


def _base(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text("MODEL:\n  ROI_HEADS:\n    NUM_CLASSES: 9\n", encoding="utf-8")
    return base


@pytest.fixture()
def books(tmp_path):
    """Own project with 2 pages, source 'y1' with 3 pages (one frozen test)."""
    own, y1 = tmp_path / "own" / "annotations", tmp_path / "y1" / "annotations"
    for s in ("a1", "a2"):
        _page(own, s, [_box("start", 10, 10, 100, 40)])
    for s in ("b1", "b2", "b3"):
        _page(y1, s, [_box("ignore", 0, 0, 800, 60)])
    _page(y1, "b4", [_box("start", 10, 10, 100, 40)], status="skip")
    return tmp_path, own, y1


def test_multi_export_prefixes_names_and_splits_per_project(books):
    tmp, own, y1 = books
    inter = tmp / "own" / "intermediate"
    res = prepare_training_data(
        "own", own, PALETTE, inter, _base(tmp), test_stems=["a2"],
        sources=[{"name": "y1", "ann_dir": y1, "test_stems": ["b3"]}])
    assert res["training_sources"] == ["y1"]
    assert res["sources"]["own"]["n_train_pages"] == 1
    assert res["sources"]["y1"] == {"n_train_pages": 2, "n_test_pages": 1,
                                    "n_excluded_status": 0, "n_skip": 1,
                                    "n_negatives": 0}
    assert res["n_train_pages"] == 3 and res["n_test_pages"] == 2
    train = json.loads((inter / "train.json").read_text(encoding="utf-8"))
    test = json.loads((inter / "test.json").read_text(encoding="utf-8"))
    assert sorted(i["file_name"] for i in train["images"]) == [
        "own/images/a1.jpg", "y1/images/b1.jpg", "y1/images/b2.jpg"]
    assert sorted(i["file_name"] for i in test["images"]) == [
        "own/images/a2.jpg", "y1/images/b3.jpg"]
    sh = (inter / "train.sh").read_text(encoding="utf-8")
    assert "--image_path_train      /workspace \\" in sh
    assert res["image_root"] == "/workspace"


def test_single_project_export_is_unchanged(books):
    tmp, own, _ = books
    inter = tmp / "own" / "intermediate"
    res = prepare_training_data("own", own, PALETTE, inter, _base(tmp))
    coco = json.loads((inter / "annotations.json").read_text(encoding="utf-8"))
    assert sorted(i["file_name"] for i in coco["images"]) == ["a1.jpg", "a2.jpg"]
    assert "sources" not in res and "training_sources" not in res
    assert "--image_path_train      /workspace/own/images \\" in (
        inter / "train.sh").read_text(encoding="utf-8")


def test_sources_need_the_whole_page_scope(books):
    tmp, own, y1 = books
    with pytest.raises(ValueError, match="whole-page scope"):
        prepare_training_data("own", own, PALETTE + ["firm"], tmp / "i", _base(tmp),
                              scope="within:firm",
                              sources=[{"name": "y1", "ann_dir": y1}])


def test_source_image_files_groups_other_projects(tmp_path):
    (tmp_path / "annotations.json").write_text(json.dumps({"images": [
        {"file_name": "own/images/a.jpg"}, {"file_name": "y1/images/b.jpg"},
        {"file_name": "y1/images/c.jpg"}, {"file_name": "plain.jpg"}]}), encoding="utf-8")
    assert _source_image_files(tmp_path, "own") == {"y1": ["b.jpg", "c.jpg"]}


@pytest.fixture()
def projects():
    tag = uuid.uuid4().hex[:6]
    names = [f"_pytest_src_{k}_{tag}" for k in ("pool", "y1", "y2", "odd")]
    for n in names[:3]:
        create_project(n, "A", PALETTE + ["firm"])
    create_project(names[3], "A", ["start", "table"])
    try:
        yield names
    finally:
        for n in names:
            shutil.rmtree(PROJECTS_ROOT / n, ignore_errors=True)


def test_config_project_lists_validate(client, projects):
    pool, y1, y2, _ = projects
    url = f"/api/project/{pool}/config"
    r = client.patch(url, json={"training_sources": [y1, y2, y1]})
    assert r.status_code == 200 and r.json()["config"]["training_sources"] == [y1, y2]
    assert client.patch(url, json={"training_sources": [pool]}).status_code == 400
    assert client.patch(url, json={"training_sources": ["_no_such_"]}).status_code == 404
    r = client.patch(url, json={"eval_projects": [y2]})
    assert r.status_code == 400 and "both train and be held out" in r.json()["detail"]
    r = client.patch(url, json={"training_sources": []})
    assert "training_sources" not in r.json()["config"]


def test_prepare_with_sources_and_the_label_gate(client, projects):
    pool, y1, y2, odd = projects
    _page(PROJECTS_ROOT / y1 / "annotations", "p1", [_box("start", 10, 10, 100, 40),
                                                     _box("firm", 5, 5, 700, 500)])
    _page(PROJECTS_ROOT / y2 / "annotations", "p1", [_box("stop", 10, 10, 100, 40)])
    client.patch(f"/api/project/{pool}/config",
                 json={"train_labels": PALETTE, "training_sources": [y1, y2]})
    r = client.post(f"/api/project/{pool}/prepare", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["model_labels"] == PALETTE and d["n_train_pages"] == 2
    assert d["sources"][y1]["n_train_pages"] == 1 and d["sources"][pool]["n_train_pages"] == 0

    client.patch(f"/api/project/{pool}/config", json={"training_sources": [y1, odd]})
    r = client.post(f"/api/project/{pool}/prepare", json={})
    assert r.status_code == 400 and odd in r.json()["detail"]
