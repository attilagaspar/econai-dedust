"""Project rename (POST /api/project/{name}/rename) and soft delete
(DELETE /api/project/{name} → projects/_trash)."""
import json

import pytest

from app import pipeline


@pytest.fixture()
def proj_root(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PROJECTS_ROOT", tmp_path)
    pipeline.create_project("alpha", "A", ["cell"])
    return tmp_path


def test_rename_moves_folder_and_updates_config(client, proj_root):
    r = client.post("/api/project/alpha/rename", json={"new_name": "beta"})
    assert r.status_code == 200, r.text
    assert not (proj_root / "alpha").exists()
    cfg = json.loads((proj_root / "beta" / "config.json").read_text(encoding="utf-8"))
    assert cfg["name"] == "beta"
    names = [p["name"] for p in pipeline.list_projects()]
    assert names == ["beta"]


def test_rename_rejects_bad_or_taken_names(client, proj_root):
    pipeline.create_project("taken", "A", [])
    assert client.post("/api/project/alpha/rename",
                       json={"new_name": "taken"}).status_code == 400
    assert client.post("/api/project/alpha/rename",
                       json={"new_name": "../evil"}).status_code == 400
    assert client.post("/api/project/alpha/rename",
                       json={"new_name": ""}).status_code == 400
    assert client.post("/api/project/ghost/rename",
                       json={"new_name": "x"}).status_code == 400
    assert (proj_root / "alpha").exists()          # untouched by failures


def test_delete_moves_to_trash_and_hides_from_list(client, proj_root):
    r = client.delete("/api/project/alpha")
    assert r.status_code == 200, r.text
    assert not (proj_root / "alpha").exists()
    trashed = list((proj_root / "_trash").iterdir())
    assert len(trashed) == 1 and trashed[0].name.startswith("alpha_")
    assert (trashed[0] / "config.json").exists()   # content preserved
    assert pipeline.list_projects() == []          # _trash invisible
    assert client.delete("/api/project/alpha").status_code == 404
