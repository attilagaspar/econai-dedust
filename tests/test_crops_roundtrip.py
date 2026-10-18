"""Crop round trip — crops as pages + write-back (app/crops.py, endpoints).

Pure: shifting a shape with its content (box + absolute row bands), the
reading-order k, provenance matching, table-id renumbering, the write of one
crop onto a page. Endpoints on throwaway `_smoke_` projects: derive (crop
geometry + provenance incl. group_id / continuation, idempotence, provenance
refresh, stale detection + policies, protection of human / reviewed crop
pages, vanished boxes, carried children with crop_origin stamps, page order),
write-back (translation incl. row_struct, content kept, parent box + foreign
shapes untouched, replacement of an earlier write-back only, several crops on
one page, table renumbering, stale skipped, dry run, status filter), and the
rows PATCH leaving crop_origin alone.
"""
import json
import shutil
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app import crops as C
from app.pipeline import create_project, PROJECTS_ROOT


# ── helpers ──────────────────────────────────────────────────────────────────

def _sh(label, x1, y1, x2, y2, text=None, **extra):
    d = {"label": label, "points": [[x1, y1], [x2, y2]], "group_id": None,
         "shape_type": "rectangle", "flags": {}}
    if text is not None:
        d["human_output"] = {"human_corrected_text": text}
    d.update(extra)
    return d


def _page(ann: Path, stem: str, shapes, status="verified", w=1000, h=800):
    doc = {"version": "5.0.1", "flags": {"status": status}, "shapes": shapes,
           "imagePath": f"{stem}.jpg", "imageWidth": w, "imageHeight": h}
    ann.mkdir(parents=True, exist_ok=True)
    (ann / f"{stem}.json").write_text(json.dumps(doc), encoding="utf-8")
    im = Image.new("RGB", (w, h), "white")
    # a dark marker in the top-left of every parent box: lets a test prove the
    # crop image shows the right region
    for s in shapes:
        if s["label"] == "entry":
            x1, y1 = s["points"][0]
            for dx in range(4):
                for dy in range(4):
                    im.putpixel((int(x1) + dx, int(y1) + dy), (0, 0, 0))
    im.save(ann / f"{stem}.jpg")
    return doc


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _settle(path):
    for _ in range(20):
        try:
            json.loads(path.read_text(encoding="utf-8"))
            return
        except OSError:
            time.sleep(0.1)


def _rm(path):
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.3)


LABELS = ["entry", "name", "address", "header"]


def _source_pages(ann: Path):
    """p1: two entries in two columns (reading order: left then right) with
    children, a header outside; p2: one entry marked continuation with a
    record id; p3: corrected page with one entry; p4: predicted (not cut)."""
    _page(ann, "p1", [
        _sh("header", 100, 10, 900, 40, text="HEAD"),
        _sh("entry", 520, 100, 900, 300, group_id=2),                 # right column
        _sh("name", 530, 110, 880, 140, text="Right name"),
        _sh("entry", 100, 100, 480, 300, group_id=1),                 # left column
        _sh("name", 110, 110, 470, 140, text="Left name"),
        _sh("address", 110, 150, 470, 190, text="Left street"),
    ])
    _page(ann, "p2", [
        _sh("entry", 100, 50, 480, 250, group_id=2, continuation=True),
    ])
    _page(ann, "p3", [_sh("entry", 100, 50, 480, 250)], status="corrected")
    _page(ann, "p4", [_sh("entry", 100, 50, 480, 250)], status="predicted")


@pytest.fixture()
def projects():
    tag = uuid.uuid4().hex[:6]
    src, tgt = f"_smoke_crop_src_{tag}", f"_smoke_crop_tgt_{tag}"
    create_project(src, "A", LABELS)
    _source_pages(PROJECTS_ROOT / src / "annotations")
    _settle(PROJECTS_ROOT / src / "config.json")
    try:
        yield src, tgt
    finally:
        _rm(PROJECTS_ROOT / src)
        _rm(PROJECTS_ROOT / tgt)


def _derive(client, src, tgt, **kw):
    body = {"label": "entry", "target": tgt, **kw}
    r = client.post(f"/api/project/{src}/derive-crops", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _write_back(client, tgt, **kw):
    r = client.post(f"/api/project/{tgt}/write-back", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


# ── pure ─────────────────────────────────────────────────────────────────────

def test_shift_shape_moves_box_and_row_bands_and_keeps_content():
    sh = _sh("name", 10, 20, 110, 60, text="X", super_row=1, super_column=2, table=0,
             row_struct={"version": 1, "rows": [{"n": 1, "y0": 22.0, "y1": 40.0, "human": "a"},
                                                {"n": 2, "y0": 40.0, "y1": 58.0, "human": "b"}]},
             authority={"id": "A1"}, structured={"data": {"k": 1}}, blank=True,
             clip={"id": 4}, tesseract_output={"ocr_text": "o"}, _idx=7)
    out = C.shift_shape(sh, 100, 1000)
    assert out["points"] == [[110, 1020], [210, 1060]]
    assert [(r["y0"], r["y1"]) for r in out["row_struct"]["rows"]] == [(1022.0, 1040.0), (1040.0, 1058.0)]
    assert out["row_struct"]["rows"][0]["human"] == "a"
    for k in ("human_output", "super_row", "super_column", "table", "authority",
              "structured", "blank", "clip", "tesseract_output"):
        assert out[k] == sh[k]
    assert "_idx" not in out
    assert sh["points"] == [[10, 20], [110, 60]]           # the input is untouched
    # clipping + degenerate
    assert C.shift_shape(sh, 0, 0, clip=(50, 0, 500, 500))["points"] == [[50, 20], [110, 60]]
    assert C.shift_shape(sh, 0, 0, clip=(200, 0, 500, 500)) is None


def test_crop_stems_and_natural_order():
    assert C.crop_stem("page_2", 3) == "page_2__crop3"
    assert C.parse_crop_stem("page_2__crop3") == ("page_2", 3)
    assert C.parse_crop_stem("page_2") is None
    stems = ["page_10__crop0", "page_2__crop10", "page_2__crop2", "page_2__crop0", "page_1__crop1"]
    assert sorted(stems, key=C.natural_key) == [
        "page_1__crop1", "page_2__crop0", "page_2__crop2", "page_2__crop10", "page_10__crop0"]


def test_ordered_parents_follow_reading_order():
    data = {"imageWidth": 1000, "shapes": [
        _sh("entry", 520, 100, 900, 300), _sh("name", 0, 0, 10, 10),
        _sh("entry", 100, 100, 480, 300), _sh("entry", 100, 400, 480, 600)]}
    assert [i for i, _ in C.ordered_parents(data, "entry")] == [2, 3, 0]   # left column, then right


def test_renumber_tables_only_on_collision():
    shapes = [{"table": 0}, {"table": 0}, {"table": 2}, {"table": 5}, {}]
    C.renumber_tables(shapes, {0, 1, 2})
    assert [s.get("table") for s in shapes] == [6, 6, 7, 5, None]
    shapes = [{"table": 0}, {"table": 1}]
    C.renumber_tables(shapes, set())
    assert [s["table"] for s in shapes] == [0, 1]


def test_locate_source_shape_follows_index_shift_and_detects_moves():
    data = {"shapes": [_sh("header", 0, 0, 10, 10), _sh("entry", 100, 100, 480, 300)]}
    prov = {"label": "entry", "bbox": [100, 100, 480, 300], "shape_idx": 1}
    assert C.locate_source_shape(data, prov) == 1
    data["shapes"].insert(0, _sh("name", 0, 0, 5, 5))        # index shifts
    assert C.locate_source_shape(data, prov) == 2
    data["shapes"][2]["points"] = [[100, 100], [480, 320]]   # moved
    assert C.locate_source_shape(data, prov) is None


# ── A. derive ────────────────────────────────────────────────────────────────

def test_derive_creates_crops_with_geometry_and_provenance(client, projects):
    src, tgt = projects
    d = _derive(client, src, tgt, margin=10)
    assert d["target_created"] is True
    assert (d["pages_selected"], d["pages_with_boxes"], d["boxes"], d["created"]) == (3, 3, 4, 4)
    assert d["stale"] == [] and d["unchanged"] == 0
    tann = PROJECTS_ROOT / tgt / "annotations"
    assert _read(PROJECTS_ROOT / tgt / "config.json")["labels"] == LABELS
    stems = sorted(p.stem for p in tann.glob("*.json"))
    assert stems == ["p1__crop0", "p1__crop1", "p2__crop0", "p3__crop0"]
    # k follows reading order: crop0 = LEFT entry (shape 3), crop1 = right (shape 1)
    c0 = _read(tann / "p1__crop0.json")
    ps = c0["flags"]["crop_source"]
    assert ps["project"] == src and ps["stem"] == "p1" and ps["shape_idx"] == 3
    assert ps["bbox"] == [100.0, 100.0, 480.0, 300.0]
    assert ps["crop_rect"] == [90, 90, 490, 310]
    assert ps["label"] == "entry" and ps["margin"] == 10.0 and ps["source_status"] == "verified"
    assert ps["group_id"] == 1 and ps["continuation"] is False and ps["ts"]
    assert c0["flags"]["status"] == "predicted" and c0["shapes"] == []
    assert (c0["imageWidth"], c0["imageHeight"]) == (400, 220)
    with Image.open(tann / "p1__crop0.jpg") as im:
        assert im.size == (400, 220)
        assert im.getpixel((11, 11))[0] < 60      # the box's marker sits at (margin, margin)
        assert im.getpixel((2, 2))[0] > 200
    c1 = _read(tann / "p1__crop1.json")["flags"]["crop_source"]
    assert c1["shape_idx"] == 1 and c1["group_id"] == 2
    # continuation + record id carried through
    c2 = _read(tann / "p2__crop0.json")["flags"]["crop_source"]
    assert c2["group_id"] == 2 and c2["continuation"] is True
    assert _read(tann / "p3__crop0.json")["flags"]["crop_source"]["source_status"] == "corrected"
    # the predicted page was not cut; the editor's page order is source order then k
    r = client.get("/api/pages", params={"folder": str(tann)})
    assert [p["stem"] for p in r.json()["pages"]] == ["p1__crop0", "p1__crop1", "p2__crop0", "p3__crop0"]


def test_derive_is_idempotent_and_refreshes_record_fields_only(client, projects):
    src, tgt = projects
    _derive(client, src, tgt)
    tann = PROJECTS_ROOT / tgt / "annotations"
    jpg = tann / "p1__crop0.jpg"
    before = jpg.stat().st_mtime_ns
    first = _read(tann / "p1__crop0.json")
    time.sleep(0.05)
    d = _derive(client, src, tgt)
    assert (d["created"], d["unchanged"], d["refreshed"], d["replaced"]) == (0, 4, 0, 0)
    assert d["stale"] == [] and d["target_created"] is False
    assert jpg.stat().st_mtime_ns == before
    assert _read(tann / "p1__crop0.json") == first
    # the sweep renumbers / marks the source box → only the provenance moves
    sp = PROJECTS_ROOT / src / "annotations" / "p1.json"
    doc = _read(sp)
    doc["shapes"][3]["group_id"] = 11
    doc["shapes"][3]["continuation"] = True
    _write(sp, doc)
    d = _derive(client, src, tgt)
    assert (d["unchanged"], d["refreshed"]) == (3, 1)
    ps = _read(tann / "p1__crop0.json")["flags"]["crop_source"]
    assert ps["group_id"] == 11 and ps["continuation"] is True
    assert ps["ts"] == first["flags"]["crop_source"]["ts"] and ps["refreshed"]
    assert jpg.stat().st_mtime_ns == before
    entry = next(c for c in d["crops"] if c["crop_stem"] == "p1__crop0")
    assert entry["action"] == "refreshed" and entry["refreshed_fields"] == ["continuation", "group_id"]


def test_derive_stale_policies_and_protection(client, projects):
    src, tgt = projects
    _derive(client, src, tgt)
    tann = PROJECTS_ROOT / tgt / "annotations"
    sp = PROJECTS_ROOT / src / "annotations" / "p1.json"
    doc = _read(sp)
    doc["shapes"][3]["points"] = [[100, 100], [480, 320]]        # the left entry grows
    _write(sp, doc)
    # keep (default): reported, nothing touched
    old = _read(tann / "p1__crop0.json")
    d = _derive(client, src, tgt)
    assert d["replaced"] == 0 and d["unchanged"] == 3
    assert [(s["crop_stem"], s["reason"], s["protected"], s["action"]) for s in d["stale"]] == \
        [("p1__crop0", "moved", False, "kept")]
    assert _read(tann / "p1__crop0.json") == old
    # replace: a pristine predicted crop page is regenerated
    d = _derive(client, src, tgt, policy="replace")
    assert d["replaced"] == 1 and d["stale"][0]["action"] == "replaced"
    new = _read(tann / "p1__crop0.json")
    assert new["flags"]["crop_source"]["bbox"] == [100.0, 100.0, 480.0, 320.0]
    assert new["imageHeight"] == 244
    # a crop page with human work is protected under replace, replaced under force
    doc["shapes"][3]["points"] = [[100, 100], [480, 340]]
    _write(sp, doc)
    new["shapes"].append(_sh("name", 5, 5, 50, 20, text="typed by a person"))
    _write(tann / "p1__crop0.json", new)
    d = _derive(client, src, tgt, policy="replace")
    assert d["replaced"] == 0 and d["protected"] == 1
    assert d["stale"][0]["protected"] is True and d["stale"][0]["action"] == "kept"
    assert len(_read(tann / "p1__crop0.json")["shapes"]) == 1
    d = _derive(client, src, tgt, policy="force")
    assert d["replaced"] == 1
    forced = _read(tann / "p1__crop0.json")
    assert forced["shapes"] == [] and forced["flags"]["crop_source"]["bbox"][3] == 340.0
    # a reviewed (verified) crop page is protected too, even without shapes
    forced["flags"]["status"] = "verified"
    _write(tann / "p1__crop0.json", forced)
    doc["shapes"][3]["points"] = [[100, 100], [480, 360]]
    _write(sp, doc)
    d = _derive(client, src, tgt, policy="replace")
    assert d["replaced"] == 0 and d["stale"][0]["protected"] is True
    assert d["stale"][0]["crop_status"] == "verified"


def test_derive_reports_vanished_boxes_and_never_deletes(client, projects):
    src, tgt = projects
    _derive(client, src, tgt)
    sp = PROJECTS_ROOT / src / "annotations" / "p1.json"
    doc = _read(sp)
    del doc["shapes"][1]                                        # the right entry vanishes
    _write(sp, doc)
    d = _derive(client, src, tgt, policy="force")
    tann = PROJECTS_ROOT / tgt / "annotations"
    assert (tann / "p1__crop1.json").exists() and (tann / "p1__crop1.jpg").exists()
    assert [(s["crop_stem"], s["reason"], s["action"]) for s in d["stale"]] == \
        [("p1__crop1", "vanished", "kept")]
    assert d["boxes"] == 3 and d["replaced"] == 0
    assert (d["unchanged"], d["refreshed"]) == (2, 1)     # the left entry's index shifted 3 → 2


def test_derive_filters_dry_run_and_validation(client, projects):
    src, tgt = projects
    d = _derive(client, src, tgt, dry_run=True, statuses=["verified"], pattern="^p1$")
    assert d["dry_run"] is True and d["created"] == 2 and d["target_created"] is True
    assert not (PROJECTS_ROOT / tgt).exists()
    d = _derive(client, src, tgt, statuses=["predicted"])
    assert d["created"] == 1 and sorted(p.stem for p in (PROJECTS_ROOT / tgt / "annotations").glob("*.json")) == ["p4__crop0"]
    for body in ({"label": "nope", "target": tgt}, {"label": "entry", "target": src},
                 {"label": "entry", "target": tgt, "policy": "wipe"},
                 {"label": "entry", "target": tgt, "pattern": "("},
                 {"label": "entry", "target": "_trash"}):
        assert client.post(f"/api/project/{src}/derive-crops", json=body).status_code == 400
    assert client.post("/api/project/_smoke_no_such_project/derive-crops",
                       json={"label": "entry", "target": tgt}).status_code == 404


def test_derive_with_children_translates_clips_and_stamps_source(client, projects):
    src, tgt = projects
    d = _derive(client, src, tgt, with_children=True, margin=10)
    assert d["children_carried"] == 3 and d["source_pages_stamped"] == 1
    tann = PROJECTS_ROOT / tgt / "annotations"
    c0 = _read(tann / "p1__crop0.json")
    assert [s["label"] for s in c0["shapes"]] == ["name", "address"]
    assert c0["shapes"][0]["points"] == [[20, 20], [380, 50]]           # 110-90, 110-90
    assert c0["shapes"][0]["human_output"]["human_corrected_text"] == "Left name"
    assert "crop_origin" not in c0["shapes"][0]
    assert _read(tann / "p1__crop1.json")["shapes"][0]["points"] == [[20, 20], [370, 50]]
    # the source copies now belong to the crop round trip
    sdoc = _read(PROJECTS_ROOT / src / "annotations" / "p1.json")
    assert sdoc["shapes"][4]["crop_origin"] == {"project": tgt, "stem": "p1__crop0"}
    assert sdoc["shapes"][2]["crop_origin"] == {"project": tgt, "stem": "p1__crop1"}
    assert "crop_origin" not in sdoc["shapes"][0] and "crop_origin" not in sdoc["shapes"][3]
    # so the first write-back replaces them instead of duplicating
    for stem in ("p1__crop0", "p1__crop1"):
        cd = _read(tann / f"{stem}.json")
        cd["flags"]["status"] = "verified"
        _write(tann / f"{stem}.json", cd)
    wb = _write_back(client, tgt)
    assert wb["shapes_written"] == 3 and wb["shapes_replaced"] == 3
    sdoc = _read(PROJECTS_ROOT / src / "annotations" / "p1.json")
    assert [s["label"] for s in sdoc["shapes"]] == ["header", "entry", "name", "entry", "name", "address"]
    assert sdoc["shapes"][4]["points"] == [[110, 110], [470, 140]]


# ── B. write back ────────────────────────────────────────────────────────────

def _annotate_crop(tann: Path, stem: str, shapes, status="verified"):
    doc = _read(tann / f"{stem}.json")
    doc["shapes"] = shapes
    doc["flags"]["status"] = status
    _write(tann / f"{stem}.json", doc)


def _crop0_content():
    return [
        _sh("name", 20, 20, 380, 50, text="Left name", super_row=1, super_column=1, table=0,
            authority={"id": "X1", "name": "Left"}, structured={"data": {"n": "Left"}},
            clip={"id": 3}, tesseract_output={"ocr_text": "Lett name"}, score=0.9,
            row_struct={"version": 1, "origin": "manual",
                        "rows": [{"n": 1, "y0": 22.0, "y1": 35.0, "ocr": "a", "llm": "", "human": "A"},
                                 {"n": 2, "y0": 35.0, "y1": 48.0, "ocr": "b", "llm": "", "human": "B", "blank": True}]}),
        _sh("address", 20, 60, 380, 100, text="Left street", super_row=2, super_column=1, table=0, blank=True),
    ]


def test_write_back_translates_and_stamps_and_leaves_the_rest(client, projects):
    src, tgt = projects
    _derive(client, src, tgt, margin=10)
    tann = PROJECTS_ROOT / tgt / "annotations"
    _annotate_crop(tann, "p1__crop0", _crop0_content())
    sp = PROJECTS_ROOT / src / "annotations" / "p1.json"
    before = _read(sp)
    before["shapes"][1]["table"] = 0                 # the right entry already uses table 0
    _write(sp, before)
    d = _write_back(client, tgt)
    assert (d["crop_pages_selected"], d["source_pages"], d["written"], d["unchanged"]) == (1, 1, 1, 0)
    assert (d["shapes_written"], d["shapes_replaced"]) == (2, 0)
    assert d["stale"] == [] and d["missing_source"] == []
    after = _read(sp)
    # the original six shapes are untouched, in place
    assert after["shapes"][:4] == before["shapes"][:4]
    assert after["shapes"][-2:] == before["shapes"][-2:]
    new = after["shapes"][4:6]                       # inserted right after the parent (index 3)
    assert [s["label"] for s in new] == ["name", "address"]
    assert new[0]["points"] == [[110, 110], [470, 140]]
    assert new[1]["points"] == [[110, 150], [470, 190]]
    assert [(r["y0"], r["y1"]) for r in new[0]["row_struct"]["rows"]] == [(112.0, 125.0), (125.0, 138.0)]
    assert new[0]["row_struct"]["rows"][1]["blank"] is True and new[0]["row_struct"]["rows"][0]["human"] == "A"
    for k in ("human_output", "authority", "structured", "clip", "tesseract_output", "score",
              "super_row", "super_column"):
        assert new[0][k] == _crop0_content()[0][k]
    assert new[1]["blank"] is True
    assert new[0]["crop_origin"] == {"project": tgt, "stem": "p1__crop0"}
    assert new[0]["group_id"] == 1 and new[1]["group_id"] == 1      # record id from the provenance
    assert new[0]["table"] == 1 and new[1]["table"] == 1            # renumbered off the page's table 0
    assert "_idx" not in new[0]
    # the source page's parent box itself is still there, untouched
    assert after["shapes"][3]["points"] == [[100, 100], [480, 300]] and "crop_origin" not in after["shapes"][3]


def test_write_back_is_repeatable_and_replaces_only_its_own_shapes(client, projects):
    src, tgt = projects
    _derive(client, src, tgt, margin=10)
    tann = PROJECTS_ROOT / tgt / "annotations"
    _annotate_crop(tann, "p1__crop0", _crop0_content())
    _annotate_crop(tann, "p1__crop1", [_sh("name", 20, 20, 300, 50, text="Right name v1", table=0)])
    sp = PROJECTS_ROOT / src / "annotations" / "p1.json"
    d = _write_back(client, tgt)
    assert d["written"] == 1 and d["shapes_written"] == 3
    one = _read(sp)
    # an unrelated shape added by hand afterwards + a foreign crop_origin shape
    one["shapes"].append(_sh("header", 100, 700, 900, 750, text="foot"))
    one["shapes"].append(_sh("name", 110, 200, 300, 230, text="other crop's",
                             crop_origin={"project": "elsewhere", "stem": "p1__crop0"}))
    _write(sp, one)
    # a second run with the same crops writes nothing
    d = _write_back(client, tgt)
    assert (d["written"], d["unchanged"], d["shapes_replaced"]) == (0, 1, 3)
    assert _read(sp) == one
    # editing one crop replaces exactly its shapes
    _annotate_crop(tann, "p1__crop1", [_sh("name", 20, 20, 300, 50, text="Right name v2"),
                                       _sh("address", 20, 60, 300, 90, text="Right street")])
    d = _write_back(client, tgt)
    assert (d["written"], d["shapes_written"], d["shapes_replaced"]) == (1, 4, 3)
    two = _read(sp)
    texts = [(s["label"], s.get("human_output", {}).get("human_corrected_text"), s.get("crop_origin"))
             for s in two["shapes"]]
    assert ("name", "Right name v2", {"project": tgt, "stem": "p1__crop1"}) in texts
    assert ("address", "Right street", {"project": tgt, "stem": "p1__crop1"}) in texts
    assert not any(t[1] == "Right name v1" for t in texts)
    assert ("name", "Left name", {"project": tgt, "stem": "p1__crop0"}) in texts
    assert ("header", "foot", None) in texts
    assert ("name", "other crop's", {"project": "elsewhere", "stem": "p1__crop0"}) in texts
    assert sum(1 for s in two["shapes"] if s["label"] == "entry") == 2
    # the v2 shapes took the place of the v1 shape (page order stable): they
    # sit before the hand-added footer, right where v1 was
    order = [t[1] for t in texts]
    assert order.index("Right name v2") < order.index("Right street") < order.index("foot")
    assert order.index("Right name v2") == 2       # v1 was inserted after the right entry (index 1)


def test_write_back_skips_stale_filters_and_dry_run(client, projects):
    src, tgt = projects
    _derive(client, src, tgt)
    tann = PROJECTS_ROOT / tgt / "annotations"
    _annotate_crop(tann, "p1__crop0", [_sh("name", 5, 5, 50, 20, text="L")])
    _annotate_crop(tann, "p1__crop1", [_sh("name", 5, 5, 50, 20, text="R")])
    _annotate_crop(tann, "p2__crop0", [_sh("name", 5, 5, 50, 20, text="P2")], status="corrected")
    sp = PROJECTS_ROOT / src / "annotations" / "p1.json"
    doc = _read(sp)
    doc["shapes"][1]["points"] = [[520, 100], [900, 320]]      # the right entry moved → crop1 stale
    _write(sp, doc)
    d = _write_back(client, tgt, dry_run=True)
    assert d["dry_run"] is True and d["written"] == 1 and d["crop_pages_selected"] == 2
    assert [(s["crop_stem"], s["source_stem"]) for s in d["stale"]] == [("p1__crop1", "p1")]
    assert _read(sp) == doc                                     # dry run wrote nothing
    assert [c["status"] for c in d["pages"][0]["crops"]] == ["ok", "stale"]
    d = _write_back(client, tgt, statuses=["verified", "corrected"])
    assert d["written"] == 2 and d["shapes_written"] == 2 and len(d["stale"]) == 1
    p1 = _read(sp)
    assert [s.get("crop_origin", {}).get("stem") for s in p1["shapes"] if s.get("crop_origin")] == ["p1__crop0"]
    p2 = _read(PROJECTS_ROOT / src / "annotations" / "p2.json")
    assert p2["shapes"][1]["human_output"]["human_corrected_text"] == "P2"
    assert p2["shapes"][1]["group_id"] == 2                     # the record id rides along
    # a crop page without provenance is counted, not written; a missing source is reported
    _write(tann / "loose.json", {"flags": {"status": "verified"}, "shapes": [_sh("name", 0, 0, 5, 5)]})
    Image.new("RGB", (10, 10)).save(tann / "loose.jpg")
    gone = _read(tann / "p3__crop0.json")
    gone["flags"]["status"] = "verified"
    gone["flags"]["crop_source"]["stem"] = "p_missing"
    _write(tann / "p3__crop0.json", gone)
    d = _write_back(client, tgt)
    assert d["no_provenance"] == 1
    assert d["missing_source"] == [{"source_project": src, "source_stem": "p_missing", "crops": ["p3__crop0"]}]
    assert client.post("/api/project/_smoke_no_such_project/write-back", json={}).status_code == 404
    assert client.post(f"/api/project/{tgt}/write-back", json={"pattern": "("}).status_code == 400


def test_crop_origin_survives_rows_patch(client, projects):
    src, tgt = projects
    _derive(client, src, tgt)
    tann = PROJECTS_ROOT / tgt / "annotations"
    _annotate_crop(tann, "p1__crop0", _crop0_content())
    _write_back(client, tgt)
    sann = PROJECTS_ROOT / src / "annotations"
    r = client.patch("/api/page/shape/rows", params={"folder": str(sann), "stem": "p1", "idx": 4},
                     json={"rows": [{"y0": 112.0, "y1": 138.0, "human": "AB"}]})
    assert r.status_code == 200, r.text
    sh = _read(sann / "p1.json")["shapes"][4]
    assert sh["crop_origin"] == {"project": tgt, "stem": "p1__crop0"} and sh["group_id"] == 1
    assert sh["row_struct"]["rows"][0]["human"] == "AB"
