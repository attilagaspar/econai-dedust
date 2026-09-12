"""Soft page deletion: /api/pages/delete moves a page's annotation JSON,
image, and predictions JSON into <project>/_trash_pages (recoverable)."""
import json

import pytest


@pytest.fixture()
def del_folder(tmp_path):
    proj = tmp_path / "proj"
    ann = proj / "annotations"
    ann.mkdir(parents=True)
    (proj / "predictions").mkdir()
    from PIL import Image
    for stem in ("p1", "p2"):
        (ann / f"{stem}.json").write_text(json.dumps(
            {"shapes": [], "imagePath": f"{stem}.jpg",
             "imageWidth": 100, "imageHeight": 100}), encoding="utf-8")
        Image.new("RGB", (100, 100), "white").save(ann / f"{stem}.jpg")
    (proj / "predictions" / "p1.json").write_text(
        json.dumps({"shapes": []}), encoding="utf-8")
    return ann


def test_delete_pages_moves_all_files_to_trash(client, del_folder):
    r = client.post("/api/pages/delete", params={"folder": str(del_folder)},
                    json={"stems": ["p1"]})
    assert r.status_code == 200
    d = r.json()
    assert d["deleted"] == 1 and d["not_found"] == 0

    proj = del_folder.parent
    trash = proj / "_trash_pages"
    # gone from live folders …
    assert not (del_folder / "p1.json").exists()
    assert not (del_folder / "p1.jpg").exists()
    assert not (proj / "predictions" / "p1.json").exists()
    # … intact in the trash
    assert (trash / "p1.json").exists()
    assert (trash / "p1.jpg").exists()
    assert (trash / "predictions" / "p1.json").exists()
    # the other page untouched
    assert (del_folder / "p2.json").exists() and (del_folder / "p2.jpg").exists()
    # page listing no longer shows p1
    stems = [p["stem"] for p in
             client.get("/api/pages", params={"folder": str(del_folder)}).json()["pages"]]
    assert stems == ["p2"]


def test_delete_pages_counts_missing_and_rejects_empty(client, del_folder):
    r = client.post("/api/pages/delete", params={"folder": str(del_folder)},
                    json={"stems": ["p2", "ghost"]})
    assert r.status_code == 200
    d = r.json()
    assert d["deleted"] == 1 and d["not_found"] == 1

    r = client.post("/api/pages/delete", params={"folder": str(del_folder)},
                    json={"stems": []})
    assert r.status_code == 400


def test_delete_pages_redelete_overwrites_trash_copy(client, del_folder):
    # delete p1, restore it manually, delete again — trash copy is replaced
    client.post("/api/pages/delete", params={"folder": str(del_folder)},
                json={"stems": ["p1"]})
    trash = del_folder.parent / "_trash_pages"
    (del_folder / "p1.json").write_text(json.dumps({"shapes": [], "marker": 2}),
                                        encoding="utf-8")
    r = client.post("/api/pages/delete", params={"folder": str(del_folder)},
                    json={"stems": ["p1"]})
    assert r.json()["deleted"] == 1
    assert json.loads((trash / "p1.json").read_text())["marker"] == 2
