"""Copying a dataset declaration between projects: the catalog of every
project's declarations and the copy endpoint (renames the `name` field,
never overwrites)."""
import json
import shutil
import time
import uuid

import pytest

from app.pipeline import create_project, PROJECTS_ROOT


def _rm(path):
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.3)


DECL = {"name": "egyeni", "version": 1,
        "scope": {"labels": ["table_cell"], "pattern": "1", "tables": [0], "header": {"5": "birtokos"}},
        "record": {"unit": "lattice_row", "key": {"slot": 1, "column": 3, "dtype": "text"}},
        "variables": [{"name": "court", "column": 1, "inherit": "ditto"}, {"name": "name", "column": 3}]}


@pytest.fixture()
def two_projects():
    tag = uuid.uuid4().hex[:6]
    a, b = f"_smoke_dca_{tag}", f"_smoke_dcb_{tag}"
    for n in (a, b):
        create_project(n, "A", ["table_cell"])
        (PROJECTS_ROOT / n / "annotations").mkdir(exist_ok=True)
    (PROJECTS_ROOT / a / "datasets").mkdir()
    (PROJECTS_ROOT / a / "datasets" / "egyeni.dataset.json").write_text(json.dumps(DECL), encoding="utf-8")
    try:
        yield a, b
    finally:
        for n in (a, b):
            _rm(PROJECTS_ROOT / n)


def test_catalog_and_copy(client, two_projects):
    a, b = two_projects
    cat = client.get("/api/datasets/catalog").json()["datasets"]
    mine = [x for x in cat if x["project"] == a]
    assert mine == [{"project": a, "name": "egyeni", "unit": "lattice_row", "pattern": "1",
                     "header": {"5": "birtokos"}, "variables": 2}]
    folder_b = str(PROJECTS_ROOT / b / "annotations")
    r = client.post("/api/datasets/copy", params={"folder": folder_b}, json={"from_project": a, "name": "egyeni"})
    assert r.status_code == 200 and r.json()["name"] == "egyeni"
    copied = json.loads((PROJECTS_ROOT / b / "datasets" / "egyeni.dataset.json").read_text(encoding="utf-8"))
    assert copied == DECL
    assert [d["name"] for d in client.get("/api/datasets", params={"folder": folder_b}).json()["datasets"]] == ["egyeni"]
    assert client.post("/api/datasets/copy", params={"folder": folder_b}, json={"from_project": a, "name": "egyeni"}).status_code == 409
    r = client.post("/api/datasets/copy", params={"folder": folder_b}, json={"from_project": a, "name": "egyeni", "as_name": "egyeni 1877"})
    assert r.status_code == 200 and r.json()["name"] == "egyeni_1877"
    assert json.loads((PROJECTS_ROOT / b / "datasets" / "egyeni_1877.dataset.json").read_text(encoding="utf-8"))["name"] == "egyeni_1877"
    assert client.post("/api/datasets/copy", params={"folder": folder_b}, json={"from_project": a, "name": "nope"}).status_code == 404
    assert client.post("/api/datasets/copy", params={"folder": folder_b}, json={"from_project": "../x", "name": "egyeni"}).status_code == 400
