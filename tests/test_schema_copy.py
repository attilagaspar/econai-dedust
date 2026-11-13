"""Copying an extraction schema between projects: the catalog of every
project's schemas and the copy endpoint (never overwriting)."""
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


@pytest.fixture()
def two_projects():
    tag = uuid.uuid4().hex[:6]
    a, b = f"_smoke_sca_{tag}", f"_smoke_scb_{tag}"
    for n in (a, b):
        create_project(n, "A", ["firm"])
        (PROJECTS_ROOT / n / "annotations").mkdir(exist_ok=True)
    (PROJECTS_ROOT / a / "schemas").mkdir()
    (PROJECTS_ROOT / a / "schemas" / "identity.json").write_text(
        json.dumps({"type": "object", "properties": {"name_hu": {"type": "string"}}}), encoding="utf-8")
    try:
        yield a, b
    finally:
        for n in (a, b):
            _rm(PROJECTS_ROOT / n)


def test_catalog_lists_every_projects_schemas_and_copy_brings_one_over(client, two_projects):
    a, b = two_projects
    cat = client.get("/api/schemas/catalog").json()["schemas"]
    mine = [x for x in cat if x["project"] == a]
    assert mine == [{"project": a, "name": "identity", "schema": {"type": "object", "properties": {"name_hu": {"type": "string"}}}}]
    assert not [x for x in cat if x["project"] == b]
    folder_b = str(PROJECTS_ROOT / b / "annotations")
    r = client.post("/api/schemas/copy", params={"folder": folder_b}, json={"from_project": a, "name": "identity"})
    assert r.status_code == 200 and r.json()["name"] == "identity"
    assert json.loads((PROJECTS_ROOT / b / "schemas" / "identity.json").read_text(encoding="utf-8"))["type"] == "object"
    assert [x["name"] for x in client.get("/api/schemas", params={"folder": folder_b}).json()["schemas"]] == ["identity"]
    # never overwrites; a new name is fine; missing sources and bad names are refused
    assert client.post("/api/schemas/copy", params={"folder": folder_b}, json={"from_project": a, "name": "identity"}).status_code == 409
    r = client.post("/api/schemas/copy", params={"folder": folder_b}, json={"from_project": a, "name": "identity", "as_name": "identity v2"})
    assert r.status_code == 200 and r.json()["name"] == "identity_v2"
    assert client.post("/api/schemas/copy", params={"folder": folder_b}, json={"from_project": a, "name": "nope"}).status_code == 404
    assert client.post("/api/schemas/copy", params={"folder": folder_b}, json={"from_project": "../x", "name": "identity"}).status_code == 400
