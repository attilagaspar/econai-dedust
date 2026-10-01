"""P9.6 Trash page: list / restore (collision-refusing) / purge for both
trash locations — projects/_trash and <project>/_trash_pages."""
import json
import shutil
import uuid

import pytest

from app import trash
from app.pipeline import create_project, delete_project, PROJECTS_ROOT


@pytest.fixture()
def proj(client):
    name = f"_pytest_trash_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", ["cell"])
    ann = PROJECTS_ROOT / name / "annotations"
    for stem in ("p1", "p2"):
        (ann / f"{stem}.json").write_text(json.dumps(
            {"imagePath": f"{stem}.jpg", "flags": {}, "shapes": []}),
            encoding="utf-8")
        (ann / f"{stem}.jpg").write_bytes(b"xx")
    try:
        yield name
    finally:
        shutil.rmtree(PROJECTS_ROOT / name, ignore_errors=True)
        # purge any leftover trashed copies of this project
        troot = PROJECTS_ROOT / "_trash"
        if troot.exists():
            for d in troot.iterdir():
                if d.name.startswith(name):
                    shutil.rmtree(d, ignore_errors=True)


def test_project_trash_roundtrip(client, proj):
    delete_project(proj)
    listing = client.get("/api/trash").json()
    entry = next(p for p in listing["projects"] if p["name"] == proj)
    assert entry["files"] >= 4

    r = client.post("/api/trash/restore-project", json={"entry": entry["entry"]})
    assert r.status_code == 200 and r.json()["restored"] == proj
    assert (PROJECTS_ROOT / proj / "config.json").exists()


def test_project_restore_refuses_live_collision(client, proj):
    delete_project(proj)
    entry = next(p for p in client.get("/api/trash").json()["projects"]
                 if p["name"] == proj)["entry"]
    create_project(proj, "A", ["cell"])            # live project reappears
    r = client.post("/api/trash/restore-project", json={"entry": entry})
    assert r.status_code == 409
    assert "never overwrites" in r.json()["detail"]
    # cleanup the trashed copy
    client.post("/api/trash/purge-project", json={"entry": entry})


def test_project_purge_reports_bytes(client, proj):
    delete_project(proj)
    entry = next(p for p in client.get("/api/trash").json()["projects"]
                 if p["name"] == proj)["entry"]
    r = client.post("/api/trash/purge-project", json={"entry": entry})
    assert r.status_code == 200 and r.json()["bytes"] > 0
    assert not (PROJECTS_ROOT / "_trash" / entry).exists()


def test_pages_trash_roundtrip_and_collision(client, proj):
    ann = PROJECTS_ROOT / proj / "annotations"
    folder = str(ann)
    # soft-delete p1 via the existing endpoint
    r = client.post(f"/api/pages/delete?folder={folder}", json={"stems": ["p1"]})
    assert r.json()["deleted"] == 1

    listing = client.get("/api/trash").json()
    assert any(pg["project"] == proj and pg["stem"] == "p1"
               for pg in listing["pages"])

    # collision: live page with the same stem → refused
    (ann / "p1.json").write_text("{}", encoding="utf-8")
    r = client.post("/api/trash/restore-pages",
                    json={"project": proj, "stems": ["p1"]})
    assert r.json()["refused"] == ["p1"]

    # remove the collision → restore succeeds
    (ann / "p1.json").unlink()
    r = client.post("/api/trash/restore-pages",
                    json={"project": proj, "stems": ["p1"]})
    assert r.json()["restored"] == ["p1"]
    assert (ann / "p1.json").exists() and (ann / "p1.jpg").exists()


def test_pages_purge_all(client, proj):
    ann = PROJECTS_ROOT / proj / "annotations"
    folder = str(ann)
    client.post(f"/api/pages/delete?folder={folder}", json={"stems": ["p1", "p2"]})
    r = client.post("/api/trash/purge-pages", json={"project": proj})
    assert r.status_code == 200
    d = r.json()
    assert d["purged"] == 2 and d["bytes"] > 0
    assert not (PROJECTS_ROOT / proj / "_trash_pages").exists()
