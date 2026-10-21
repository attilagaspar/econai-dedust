"""Dashboard project settings on the config PATCH (`notes`, `tags`) and the
project-list payload, on a throwaway `_smoke_` project under a temporary
projects root (nothing touches the live projects/ folder)."""
import json

import pytest

from app import pipeline
from app.pipeline import normalize_tags

NAME = "_smoke_dashcfg"


@pytest.fixture()
def proj_root(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PROJECTS_ROOT", tmp_path)
    pipeline.create_project(NAME, "A", ["cell"])
    pipeline.create_project(NAME + "_2", "A", ["cell"])
    yield tmp_path
    # tmp_path is discarded by pytest; the explicit cleanup keeps the
    # "_smoke_ projects are deleted afterwards" rule visible.
    for d in (tmp_path / NAME, tmp_path / (NAME + "_2")):
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    f.unlink()


def _cfg(root, name=NAME):
    return json.loads((root / name / "config.json").read_text(encoding="utf-8"))


# ── normalize_tags ───────────────────────────────────────────────────────────

def test_normalize_tags_trims_dedupes_keeps_order():
    assert normalize_tags([" compass ", "1930s", "compass", "", "  ", "done"]) == \
        ["compass", "1930s", "done"]
    assert normalize_tags([]) == []


@pytest.mark.parametrize("bad", ["compass", {"a": 1}, 3, None, ["ok", 5], ["ok", None]])
def test_normalize_tags_refuses_non_string_lists(bad):
    with pytest.raises(ValueError):
        normalize_tags(bad)


# ── notes ────────────────────────────────────────────────────────────────────

def test_notes_round_trip(client, proj_root):
    r = client.patch(f"/api/project/{NAME}/config", json={"notes": "first batch\nsecond line"})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["notes"] == "first batch\nsecond line"
    assert _cfg(proj_root)["notes"] == "first batch\nsecond line"
    assert client.get(f"/api/project/{NAME}").json()["config"]["notes"] == "first batch\nsecond line"

    # other keys untouched by a notes-only PATCH
    assert _cfg(proj_root)["labels"] == ["cell"]

    # blank clears the key
    r = client.patch(f"/api/project/{NAME}/config", json={"notes": "   "})
    assert r.status_code == 200
    assert "notes" not in _cfg(proj_root)


def test_notes_refuses_non_string(client, proj_root):
    assert client.patch(f"/api/project/{NAME}/config", json={"notes": ["a"]}).status_code == 422
    assert client.patch(f"/api/project/{NAME}/config", json={"notes": 7}).status_code == 422
    assert "notes" not in _cfg(proj_root)


# ── tags ─────────────────────────────────────────────────────────────────────

def test_tags_normalised_and_listed(client, proj_root):
    r = client.patch(f"/api/project/{NAME}/config",
                     json={"tags": [" compass ", "1930s", "compass", ""]})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["tags"] == ["compass", "1930s"]
    assert _cfg(proj_root)["tags"] == ["compass", "1930s"]

    # the project list carries tags (and [] for a project without any)
    by_name = {p["name"]: p for p in client.get("/api/projects").json()["projects"]}
    assert by_name[NAME]["tags"] == ["compass", "1930s"]
    assert by_name[NAME + "_2"]["tags"] == []

    # [] clears the key
    r = client.patch(f"/api/project/{NAME}/config", json={"tags": []})
    assert r.status_code == 200
    assert "tags" not in _cfg(proj_root)
    assert {p["name"]: p for p in client.get("/api/projects").json()["projects"]}[NAME]["tags"] == []


def test_tags_refuse_invalid_types(client, proj_root):
    assert client.patch(f"/api/project/{NAME}/config", json={"tags": "compass"}).status_code == 422
    assert client.patch(f"/api/project/{NAME}/config", json={"tags": ["ok", 5]}).status_code == 400
    assert client.patch(f"/api/project/{NAME}/config", json={"tags": [None]}).status_code == 400
    assert "tags" not in _cfg(proj_root)


def test_pipeline_json_untouched_by_notes_and_tags(client, proj_root):
    before = (proj_root / NAME / "pipeline.json").read_text(encoding="utf-8")
    client.patch(f"/api/project/{NAME}/config", json={"notes": "n", "tags": ["t"]})
    assert (proj_root / NAME / "pipeline.json").read_text(encoding="utf-8") == before
