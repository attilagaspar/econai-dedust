"""§H5 record chaining for one parent label (app/records.py + endpoints).

Pure: column ordering (auto gutter / forced 1 / 2, wide box resets the
flow), cross-page chaining via `continuation`, locked ids, idempotence,
orphan detection, forward resweep seeding, page-order check. Endpoints on a
throwaway `_smoke_` project: config PATCH, sweep (dry run → no writes;
real run → ids written, registry rebuilt), records listing with flags,
shape PATCH for the marks, and the rows PATCH leaving the per-shape fields
alone.
"""
import json
import shutil
import time
import uuid

import pytest

from app import records as R
from app.pipeline import create_project, PROJECTS_ROOT


# ── helpers ──────────────────────────────────────────────────────────────────

def _sh(label, x1, y1, x2, y2, text=None, **extra):
    d = {"label": label, "points": [[x1, y1], [x2, y2]], "group_id": None,
         "shape_type": "rectangle", "flags": {}}
    if text is not None:
        d["human_output"] = {"human_corrected_text": text}
    d.update(extra)
    return d


def _doc(shapes, status="verified", w=1000, h=800):
    return {"version": "5.0.1", "flags": {"status": status}, "shapes": shapes,
            "imagePath": "x.jpg", "imageWidth": w, "imageHeight": h}


def _two_col_page(cont_first=False, **extra0):
    """Two columns: left boxes at y 100/300, right boxes at y 100/300."""
    return _doc([
        _sh("firm", 550, 300, 900, 450, text="R2"),      # right col, 2nd
        _sh("firm", 100, 300, 450, 450, text="L2"),      # left col, 2nd
        _sh("firm", 550, 100, 900, 250, text="R1"),      # right col, 1st
        _sh("firm", 100, 100, 450, 250, text="L1",       # left col, 1st
            **({"continuation": True} if cont_first else {}), **extra0),
        _sh("page_header", 100, 10, 900, 40),            # another label: ignored
    ])


def _labels(res, data):
    return [data["shapes"][i].get("label") for i in res]


# ── reading order ────────────────────────────────────────────────────────────

def test_reading_order_two_columns_auto():
    d = _two_col_page()
    order = R.reading_order(d["shapes"], "firm", 1000, "auto")
    assert order == [3, 1, 2, 0]           # L1, L2, R1, R2


def test_reading_order_forced_single_column_is_y_then_x():
    d = _two_col_page()
    order = R.reading_order(d["shapes"], "firm", 1000, 1)
    assert order == [3, 2, 1, 0]           # L1, R1, L2, R2


def test_reading_order_forced_two_columns_without_gutter():
    # boxes overlap the page centre, no empty x-run → mode 2 splits at w/2
    shapes = [_sh("firm", 400, 300, 700, 400), _sh("firm", 100, 100, 520, 200),
              _sh("firm", 480, 100, 900, 200)]
    assert R.split_x([R._bbox(s) for s in shapes], 1000, "auto") is None
    order = R.reading_order(shapes, "firm", 1000, 2)
    assert order == [1, 2, 0]              # centres 310 / 550 / 690 → left: 1; right: 2, 0 by y


def test_wide_box_resets_column_flow():
    d = _two_col_page()
    d["shapes"].append(_sh("firm", 80, 500, 920, 560))    # full-width (84 %) element
    d["shapes"].append(_sh("firm", 550, 600, 900, 700))   # below it, right
    d["shapes"].append(_sh("firm", 100, 600, 450, 700))   # below it, left
    order = R.reading_order(d["shapes"], "firm", 1000, "auto")
    assert order == [3, 1, 2, 0, 5, 7, 6]  # band 1 col-major, wide, band 2 col-major


def test_split_x_needs_boxes_on_both_sides():
    boxes = [(100, 100, 400, 200), (100, 300, 400, 400)]     # all left
    assert R.split_x(boxes, 1000, "auto") is None
    assert R.split_x(boxes, 1000, 2) == 500.0


# ── the sweep ────────────────────────────────────────────────────────────────

def test_sweep_numbers_records_and_chains_across_pages():
    p1 = _two_col_page()
    p2 = _two_col_page(cont_first=True)      # L1 of page 2 continues R2 of page 1
    res = R.sweep([("p1", p1), ("p2", p2)], "firm", "auto")
    a1, a2 = res["assignments"]["p1"], res["assignments"]["p2"]
    assert [a1[i] for i in (3, 1, 2, 0)] == [1, 2, 3, 4]
    assert a2[3] == 4                        # the continuation joins record 4
    assert [a2[i] for i in (1, 2, 0)] == [5, 6, 7]
    assert res["records"][4] == [
        {"stem": "p1", "idx": 0, "bbox": [550.0, 300.0, 900.0, 450.0], "continuation": False, "locked": False},
        {"stem": "p2", "idx": 3, "bbox": [100.0, 100.0, 450.0, 250.0], "continuation": True, "locked": False}]
    assert res["orphans"] == []
    assert res["next_id"] == 8 and res["n_boxes"] == 8
    assert res["changed"] == {"p1": 4, "p2": 4}


def test_sweep_is_idempotent_and_reports_no_change():
    p1, p2 = _two_col_page(), _two_col_page(cont_first=True)
    res = R.sweep([("p1", p1), ("p2", p2)], "firm")
    for stem, d in (("p1", p1), ("p2", p2)):
        assert R.apply_assignments(d, res["assignments"][stem]) is True
    again = R.sweep([("p1", p1), ("p2", p2)], "firm")
    assert again["assignments"] == res["assignments"]
    assert again["changed"] == {}
    for d in (p1, p2):
        assert R.apply_assignments(d, again["assignments"]["p1" if d is p1 else "p2"]) is False


def test_sweep_respects_locked_ids_and_continues_from_them():
    p1 = _two_col_page()
    p1["shapes"][2]["group_id"] = 40          # R1 hand-assigned
    p1["shapes"][2]["group_locked"] = True
    p2 = _two_col_page(cont_first=True)
    res = R.sweep([("p1", p1), ("p2", p2)], "firm")
    a1, a2 = res["assignments"]["p1"], res["assignments"]["p2"]
    assert (a1[3], a1[1], a1[2], a1[0]) == (1, 2, 40, 41)    # continues from 40
    assert a2[3] == 41                                         # continuation joins R2
    assert sorted(res["records"]) == [1, 2, 40, 41, 42, 43, 44]
    assert res["records"][40][0]["locked"] is True
    # an unlocked group_id is NOT respected: it is just a stale value
    p1["shapes"][2].pop("group_locked")
    res2 = R.sweep([("p1", p1), ("p2", p2)], "firm")
    assert res2["assignments"]["p1"][2] == 3


def test_locked_fragment_joins_an_earlier_record():
    p1 = _two_col_page()
    p2 = _two_col_page()
    p2["shapes"][1]["group_id"] = 1           # L2 of page 2 belongs to record 1
    p2["shapes"][1]["group_locked"] = True
    res = R.sweep([("p1", p1), ("p2", p2)], "firm")
    assert [f["stem"] for f in res["records"][1]] == ["p1", "p2"]
    assert res["assignments"]["p2"][1] == 1
    assert res["next_id"] == 8                # no id wasted, none duplicated
    ids = [g for a in res["assignments"].values() for g in a.values()]
    assert len(ids) == 8 and max(ids) == 7


def test_orphan_continuation_starts_its_own_record():
    p1 = _two_col_page(cont_first=True)       # first box of the volume marked
    res = R.sweep([("p1", p1)], "firm")
    assert res["assignments"]["p1"][3] == 1
    assert [o["idx"] for o in res["orphans"]] == [3]
    coll = R.collect_records([("p1", p1)], "firm")
    # before the ids are written nothing is a record yet
    assert coll["records"] == {} and len(coll["unswept"]) == 4
    R.apply_assignments(p1, res["assignments"]["p1"])
    coll = R.collect_records([("p1", p1)], "firm")
    assert coll["records"][1]["orphan"] is True
    assert coll["records"][2]["orphan"] is False
    assert coll["records"][1]["name"] == "L1"


def test_forward_resweep_seed_keeps_earlier_ids():
    p1, p2 = _two_col_page(), _two_col_page(cont_first=True)
    res = R.sweep([("p1", p1), ("p2", p2)], "firm")
    R.apply_assignments(p1, res["assignments"]["p1"])
    R.apply_assignments(p2, res["assignments"]["p2"])
    # the human now unmarks the continuation on p2 → resweep forward from p2
    p2["shapes"][3].pop("continuation")
    start, carry = R.seed_from_pages([("p1", p1)], "firm")
    assert (start, carry) == (5, 4)
    res2 = R.sweep([("p2", p2)], "firm", start_id=start, carry_id=carry)
    assert [res2["assignments"]["p2"][i] for i in (3, 1, 2, 0)] == [5, 6, 7, 8]
    # and a leading continuation would have joined record 4
    p2["shapes"][3]["continuation"] = True
    res3 = R.sweep([("p2", p2)], "firm", start_id=start, carry_id=carry)
    assert res3["assignments"]["p2"][3] == 4


def test_registry_and_spans():
    p1, p2, p3 = _two_col_page(), _two_col_page(cont_first=True), _two_col_page()
    p3["shapes"][3]["continuation"] = True
    pages = [("p1", p1), ("p2", p2), ("p3", p3)]
    res = R.sweep(pages, "firm")
    for s, d in pages:
        R.apply_assignments(d, res["assignments"][s])
    coll = R.collect_records(pages, "firm")
    assert coll["records"][4]["page_span"] == ["p1", "p2"] and coll["records"][4]["n_pages"] == 2
    assert coll["records"][7]["page_span"] == ["p2", "p3"]
    reg = R.build_registry(coll, "firm", "auto")
    assert reg["meta"]["record_label"] == "firm" and reg["meta"]["n_records"] == 10
    assert reg["records"]["4"]["fragments"][1] == {"stem": "p2", "idx": 3,
                                                   "bbox": [100.0, 100.0, 450.0, 250.0]}
    assert reg["records"]["4"]["first_stem"] == "p1"


def test_page_order_check():
    ok = R.page_order_check([f"vol_{i}" for i in range(1, 12)])
    assert ok["ok"] and ok["prefixes"] == ["vol_"]
    bad = R.page_order_check(["vol_1", "vol_2", "vol_4", "plate", "other_1"])
    assert not bad["ok"]
    assert bad["non_numeric"] == ["plate"]
    assert bad["gaps"] == [{"prefix": "vol_", "missing": [3], "n_missing": 1}]
    assert bad["prefixes"] == ["other_", "vol_"]
    assert R.page_order_check(["a_1", "a_1"])["duplicates"] == ["a_1"]


def test_config_helpers():
    assert R.record_label_for({}) is None
    assert R.record_label_for({"record_label": " firm "}) == "firm"
    assert R.layout_columns_for({}) == "auto"
    assert R.layout_columns_for({"layout_columns": "2"}) == 2
    with pytest.raises(ValueError):
        R.normalize_layout(3)
    with pytest.raises(ValueError):
        R.normalize_layout("two")


# ── endpoints on a throwaway project ─────────────────────────────────────────

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


@pytest.fixture()
def rec_project():
    name = f"_smoke_records_{uuid.uuid4().hex[:6]}"
    create_project(name, "B", ["firm", "page_header"])
    pdir = PROJECTS_ROOT / name
    ann = pdir / "annotations"
    ann.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    for stem, doc in (("v_1", _two_col_page()), ("v_2", _two_col_page(cont_first=True)),
                      ("v_3", _doc([_sh("firm", 0, 0, 100, 30)], status="skip")),
                      ("v_4", _two_col_page())):
        (ann / f"{stem}.json").write_text(json.dumps(doc), encoding="utf-8")
        Image.new("RGB", (1000, 800), "white").save(ann / f"{stem}.jpg")
    _settle(pdir / "config.json")
    try:
        yield name, pdir
    finally:
        _rm(pdir)


def _page(pdir, stem):
    return json.loads((pdir / "annotations" / f"{stem}.json").read_text(encoding="utf-8"))


def test_sweep_endpoint_needs_record_label(client, rec_project):
    name, pdir = rec_project
    r = client.post(f"/api/project/{name}/sweep-records", json={})
    assert r.status_code == 400 and "record label" in r.json()["detail"].lower()
    r = client.patch(f"/api/project/{name}/config", json={"record_label": "nope"})
    assert r.status_code == 400
    r = client.patch(f"/api/project/{name}/config", json={"layout_columns": "5"})
    assert r.status_code == 400


def test_sweep_endpoint_dry_run_then_write_and_browse(client, rec_project):
    name, pdir = rec_project
    r = client.patch(f"/api/project/{name}/config",
                     json={"record_label": "firm", "layout_columns": "auto"})
    assert r.status_code == 200 and r.json()["config"]["record_label"] == "firm"
    assert "layout_columns" not in r.json()["config"]          # auto = absent
    ctx = client.get("/api/records/context", params={"folder": str(pdir / "annotations")}).json()
    assert ctx == {"project": name, "record_label": "firm", "layout_columns": "auto",
                   "labels": ["firm", "page_header"]}

    # dry run: nothing written, page-order check reported
    r = client.post(f"/api/project/{name}/sweep-records", json={"dry_run": True})
    d = r.json()
    assert r.status_code == 200, d
    assert d["dry_run"] and d["pages"] == 3 and d["boxes"] == 12 and d["records"] == 11
    assert d["pages_written"] == 0 and d["boxes_changed"] == 12
    assert d["page_order"]["ok"] is False            # v_3 (skip) leaves a gap in the sweep order
    assert d["page_order"]["gaps"][0]["missing"] == [3]
    assert all(s.get("group_id") is None for s in _page(pdir, "v_1")["shapes"])
    assert not (pdir / "intermediate" / "records.json").exists()

    # real sweep: ids written, skip page untouched, registry rebuilt
    r = client.post(f"/api/project/{name}/sweep-records", json={})
    d = r.json()
    assert r.status_code == 200 and d["pages_written"] == 3
    v1, v2, v4 = _page(pdir, "v_1"), _page(pdir, "v_2"), _page(pdir, "v_4")
    assert [v1["shapes"][i]["group_id"] for i in (3, 1, 2, 0)] == [1, 2, 3, 4]
    assert v2["shapes"][3]["group_id"] == 4
    assert v1["shapes"][4].get("group_id") is None    # page_header: not a record box
    assert _page(pdir, "v_3")["shapes"][0].get("group_id") is None
    assert [v4["shapes"][i]["group_id"] for i in (3, 1, 2, 0)] == [8, 9, 10, 11]
    reg = json.loads((pdir / "intermediate" / "records.json").read_text(encoding="utf-8"))
    assert reg["meta"]["n_records"] == 11
    assert reg["records"]["4"]["page_span"] == ["v_1", "v_2"]

    # idempotent: a second sweep writes nothing
    r = client.post(f"/api/project/{name}/sweep-records", json={})
    assert r.json()["pages_written"] == 0 and r.json()["boxes_changed"] == 0

    # browser listing: record 4 spans two pages → flagged with max_pages=1
    r = client.get(f"/api/project/{name}/records", params={"max_pages": 1})
    d = r.json()
    assert r.status_code == 200 and d["n_records"] == 11 and d["n_unswept"] == 0
    rec4 = next(x for x in d["records"] if x["id"] == 4)
    assert rec4["flags"] == ["long"] and rec4["n_fragments"] == 2
    assert rec4["fragments"][1]["continuation"] is True
    assert rec4["name"] == "R2"
    r = client.get(f"/api/project/{name}/records", params={"max_pages": 1, "flagged_only": "true"})
    assert [x["id"] for x in r.json()["records"]] == [4]


def test_shape_patch_marks_and_forward_resweep(client, rec_project):
    name, pdir = rec_project
    client.patch(f"/api/project/{name}/config", json={"record_label": "firm"})
    client.post(f"/api/project/{name}/sweep-records", json={})
    ann = str(pdir / "annotations")
    # the human decides L1 of v_2 is NOT a continuation → clear + resweep forward
    r = client.patch("/api/page/shape", params={"folder": ann, "stem": "v_2", "idx": 3},
                     json={"continuation": False})
    assert r.status_code == 200 and r.json()["shape"]["continuation"] is None
    r = client.post(f"/api/project/{name}/sweep-records", json={"from_stem": "v_2"})
    d = r.json()
    assert r.status_code == 200 and d["pages"] == 2 and d["from_stem"] == "v_2"
    assert [_page(pdir, "v_1")["shapes"][i]["group_id"] for i in (3, 1, 2, 0)] == [1, 2, 3, 4]
    assert _page(pdir, "v_2")["shapes"][3]["group_id"] == 5          # now its own record
    assert [_page(pdir, "v_4")["shapes"][i]["group_id"] for i in (3, 1, 2, 0)] == [9, 10, 11, 12]
    # lock an id by hand, mark a continuation, orphan shows in the listing
    r = client.patch("/api/page/shape", params={"folder": ann, "stem": "v_4", "idx": 3},
                     json={"group_id": 77, "group_locked": True, "continuation": True})
    assert r.json()["shape"] == {"label": "firm", "continuation": True, "group_id": 77,
                                 "group_locked": True}
    r = client.post(f"/api/project/{name}/sweep-records", json={})
    assert _page(pdir, "v_4")["shapes"][3]["group_id"] == 77
    assert [o["stem"] for o in r.json()["orphans"]] == ["v_4"]
    d = client.get(f"/api/project/{name}/records").json()
    rec77 = next(x for x in d["records"] if x["id"] == 77)
    assert rec77["flags"] == ["orphan"] and rec77["fragments"][0]["locked"] is True
    assert d["n_orphans"] == 1
    # clearing the id drops the lock too
    r = client.patch("/api/page/shape", params={"folder": ann, "stem": "v_4", "idx": 3},
                     json={"clear_group_id": True})
    assert r.json()["shape"]["group_id"] is None and r.json()["shape"]["group_locked"] is None
    r = client.post(f"/api/project/{name}/sweep-records", json={"from_stem": "zzz"})
    assert r.status_code == 400


def test_rows_patch_keeps_per_shape_record_fields(client, rec_project):
    """PATCH /api/page/shape/rows rebuilds row_struct rows from a whitelist;
    the record fields live on the shape, not on rows, and must survive."""
    name, pdir = rec_project
    ann = str(pdir / "annotations")
    client.patch("/api/page/shape", params={"folder": ann, "stem": "v_1", "idx": 3},
                 json={"group_id": 5, "group_locked": True, "continuation": True})
    r = client.patch("/api/page/shape/rows", params={"folder": ann, "stem": "v_1", "idx": 3},
                     json={"rows": [{"y0": 0.0, "y1": 0.5, "human": "a"},
                                    {"y0": 0.5, "y1": 1.0, "human": "b"}]})
    assert r.status_code == 200
    sh = _page(pdir, "v_1")["shapes"][3]
    assert (sh["group_id"], sh["group_locked"], sh["continuation"]) == (5, True, True)
    assert len(sh["row_struct"]["rows"]) == 2
