"""Label palette editing: add, propagating rename, guarded delete, region flag."""
import json
import shutil
import uuid

import pytest

from app.pipeline import create_project, PROJECTS_ROOT


@pytest.fixture()
def proj(tmp_path):
    name = f"_pytest_lbl_{uuid.uuid4().hex[:6]}"
    pdir = create_project(name, "A", ["cell", "header"])
    ann = pdir / "annotations"
    shapes = [{"label": "cell", "points": [[0, 0], [10, 10]]},
              {"label": "header", "points": [[0, 20], [10, 30]]},
              {"label": "cell", "points": [[0, 40], [10, 50]]}]
    (ann / "p1.json").write_text(json.dumps(
        {"shapes": shapes, "imagePath": "p1.jpg", "imageWidth": 100, "imageHeight": 100}))
    (ann / "p2.json").write_text(json.dumps(
        {"shapes": [{"label": "cell", "points": [[0, 0], [5, 5]]}],
         "imagePath": "p2.jpg", "imageWidth": 100, "imageHeight": 100}))
    try:
        yield name, ann
    finally:
        shutil.rmtree(PROJECTS_ROOT / name, ignore_errors=True)


def _cfg(name):
    return json.loads((PROJECTS_ROOT / name / "config.json").read_text(encoding="utf-8"))


def test_add_label(client, proj):
    name, ann = proj
    r = client.post("/api/labels", json={"folder": str(ann), "action": "add",
                                         "label": "firm_description"})
    assert r.status_code == 200
    assert "firm_description" in r.json()["labels"]
    assert "firm_description" in _cfg(name)["labels"]
    # duplicate add is a no-op, invalid names rejected
    client.post("/api/labels", json={"folder": str(ann), "action": "add",
                                     "label": "firm_description"})
    assert _cfg(name)["labels"].count("firm_description") == 1
    r = client.post("/api/labels", json={"folder": str(ann), "action": "add",
                                         "label": "has spaces"})
    assert r.status_code == 400


def test_rename_propagates_to_shapes(client, proj):
    name, ann = proj
    r = client.post("/api/labels", json={"folder": str(ann), "action": "rename",
                                         "label": "cell", "new_label": "balance_sheet_a"})
    d = r.json()
    assert r.status_code == 200
    assert d["shapes_touched"] == 3 and d["pages_touched"] == 2
    assert "balance_sheet_a" in d["labels"] and "cell" not in d["labels"]
    p1 = json.loads((ann / "p1.json").read_text(encoding="utf-8"))
    assert [s["label"] for s in p1["shapes"]] == ["balance_sheet_a", "header", "balance_sheet_a"]


def test_remove_guarded_while_in_use(client, proj):
    name, ann = proj
    r = client.post("/api/labels", json={"folder": str(ann), "action": "remove",
                                         "label": "cell"})
    assert r.status_code == 400 and "3 shape(s)" in r.json()["detail"]
    # an unused label deletes fine
    client.post("/api/labels", json={"folder": str(ann), "action": "add", "label": "unused"})
    r = client.post("/api/labels", json={"folder": str(ann), "action": "remove",
                                         "label": "unused"})
    assert r.status_code == 200 and "unused" not in r.json()["labels"]


def test_region_flag_initializes_from_defaults(client, proj):
    name, ann = proj
    # give the project one canonical region label + one custom label
    client.post("/api/labels", json={"folder": str(ann), "action": "add", "label": "text_block"})
    client.post("/api/labels", json={"folder": str(ann), "action": "add", "label": "firm_description"})
    r = client.post("/api/labels", json={"folder": str(ann), "action": "set_region",
                                         "label": "firm_description", "is_region": True})
    regions = r.json()["region_labels"]
    # text_block (canonical) survived the initialization; firm_description joined
    assert "text_block" in regions and "firm_description" in regions
    assert "cell" not in regions
    # unmark again
    r = client.post("/api/labels", json={"folder": str(ann), "action": "set_region",
                                         "label": "firm_description", "is_region": False})
    assert "firm_description" not in r.json()["region_labels"]


def test_non_project_folder_404(client, tmp_path):
    d = tmp_path / "loose"
    d.mkdir()
    r = client.post("/api/labels", json={"folder": str(d), "action": "add", "label": "x"})
    assert r.status_code == 404
