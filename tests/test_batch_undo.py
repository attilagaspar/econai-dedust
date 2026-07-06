"""Batch undo: snapshot the pages a batch will touch, restore them later."""
import json


def test_snapshot_and_restore(client, page_folder):
    # snapshot p1, then damage it, then restore
    r = client.post("/api/batch_snapshot", params={"folder": str(page_folder)},
                    json={"stems": ["p1"], "op": "llm"})
    assert r.status_code == 200 and r.json()["pages"] == 1

    r = client.get("/api/batch_snapshot", params={"folder": str(page_folder)})
    meta = r.json()["snapshot"]
    assert meta and meta["op"] == "llm" and meta["pages"] == 1

    # simulate a destructive batch: wipe all shapes
    client.put("/api/page/shapes", params={"folder": str(page_folder), "stem": "p1"},
               json={"shapes": []})
    assert json.loads((page_folder / "p1.json").read_text(encoding="utf-8"))["shapes"] == []

    r = client.post("/api/batch_snapshot/restore", params={"folder": str(page_folder)})
    assert r.status_code == 200 and r.json()["restored"] == 1
    shapes = json.loads((page_folder / "p1.json").read_text(encoding="utf-8"))["shapes"]
    assert len(shapes) == 6           # the fixture page is back


def test_restore_without_snapshot(client, tmp_path):
    # real layout: the annotations folder lives inside its own project dir
    ann = tmp_path / "proj" / "annotations"
    ann.mkdir(parents=True)
    (ann / "x.json").write_text("{}", encoding="utf-8")
    r = client.post("/api/batch_snapshot/restore", params={"folder": str(ann)})
    assert r.status_code == 404
