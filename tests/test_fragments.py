"""Derive record fragments from start / stop / ignore roles (app/fragments.py
+ POST /api/project/{name}/derive-fragments).

Pure: single column, two columns (continuation at the right column's top),
full-width stop (bands), start at the column top, a page without starts and
stops (continuation page vs closed record), ignore boxes masked out of the
ink trim, the replacement rule (hand-drawn + edited derived shapes
protected, identical fragments kept, re-run idempotent). Endpoints on a
throwaway `_smoke_` project: config PATCH validation, dry run → no writes +
empty-pages list, real run, re-run no-op, chained sweep, and the crop
round trip cutting the derived fragments.
"""
import json
import shutil
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import fragments as F
from app.pipeline import create_project, PROJECTS_ROOT

ROLES = {"name": "start", "heading": "stop", "advert": "ignore"}
REC = "entry"


# ── helpers ──────────────────────────────────────────────────────────────────

def _sh(label, x1, y1, x2, y2, **extra):
    d = {"label": label, "points": [[x1, y1], [x2, y2]], "group_id": None,
         "shape_type": "rectangle", "flags": {}}
    d.update(extra)
    return d


def _doc(shapes, status="verified", w=1000, h=800):
    return {"version": "5.0.1", "flags": {"status": status}, "shapes": shapes,
            "imagePath": "x.jpg", "imageWidth": w, "imageHeight": h}


def _ink(lines, w=1000, h=800):
    """An InkMap from black rectangles (x1, y1, x2, y2) on a white page."""
    im = Image.new("L", (w, h), 255)
    dr = ImageDraw.Draw(im)
    for x1, y1, x2, y2 in lines:
        dr.rectangle([x1, y1, x2 - 1, y2 - 1], fill=0)
    import numpy as np
    return F.InkMap(np.asarray(im) < 160)


def _text_lines(x1, x2, y_from, y_to, step=30, height=18):
    return [(x1, y, x2, y + height) for y in range(y_from, y_to, step)]


def _boxes(res):
    return [[round(v) for v in F._bbox(f)] for f in res["fragments"]]


def _conts(res):
    return [bool(f.get("continuation")) for f in res["fragments"]]


# ── pure derivation ──────────────────────────────────────────────────────────

def test_single_column_two_starts():
    # text 100..700 in one column; starts at 100 and 400; a gap 370..400
    lines = _text_lines(100, 900, 100, 370) + _text_lines(100, 900, 400, 700)
    doc = _doc([_sh("name", 100, 100, 400, 118), _sh("name", 100, 400, 400, 418)])
    res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0)
    assert res["starts"] == 2 and res["stops"] == 0 and res["columns"] == 1
    assert _boxes(res) == [[100, 100, 900, 358], [100, 400, 900, 688]]
    assert _conts(res) == [False, False]          # nothing above the first start
    assert res["open_at_end"] is True
    for f in res["fragments"]:
        assert f["label"] == REC and f[F.MARKER_KEY]["kind"] == F.MARKER_KIND
        assert f[F.MARKER_KEY]["bbox"] == [float(v) for v in F._bbox(f)]
    assert res["fragments"][0][F.MARKER_KEY]["start_label"] == "name"


def test_two_columns_right_top_is_a_continuation():
    # left column 100..450, right 550..900; left: start at 100, text to 700
    # (record open at the column end); right: text 100..250 (continuation),
    # start at 300, text to 700
    lines = (_text_lines(100, 450, 100, 700) + _text_lines(550, 900, 100, 250)
             + _text_lines(550, 900, 300, 700))
    doc = _doc([_sh("name", 550, 300, 800, 318), _sh("name", 100, 100, 350, 118)])
    res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0, carry_open=False)
    assert res["columns"] == 2
    assert _boxes(res) == [[100, 100, 450, 688], [550, 100, 900, 238], [550, 300, 900, 708]]
    assert _conts(res) == [False, True, False]
    assert res["fragments"][1][F.MARKER_KEY]["continuation"] is True
    assert res["fragments"][1][F.MARKER_KEY]["start_label"] is None


def test_full_width_stop_cuts_bands_and_closes_the_record():
    # start at 100 (left col), wide heading 400..430, text after it 440..480
    # belongs to no record, start at 500, text to 700
    lines = (_text_lines(100, 900, 100, 370) + [(50, 400, 950, 430)]
             + _text_lines(100, 900, 440, 490) + _text_lines(100, 900, 500, 700))
    doc = _doc([_sh("name", 100, 100, 400, 118), _sh("heading", 50, 400, 950, 430),
                _sh("name", 100, 500, 400, 518)])
    res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0)
    assert _boxes(res) == [[100, 100, 900, 358], [100, 500, 900, 698]]
    assert _conts(res) == [False, False]
    # the same page with a record open from before: leading text above the
    # first start at 100 (y 40..90) is a continuation
    lines2 = lines + _text_lines(100, 900, 40, 90)
    res2 = F.derive_page(doc, ROLES, REC, ink=_ink(lines2), margin=0, carry_open=True)
    assert _boxes(res2)[0] == [100, 40, 900, 88] and _conts(res2)[0] is True
    # a wide stop at the very end leaves the record closed
    doc3 = _doc([_sh("name", 100, 100, 400, 118), _sh("heading", 50, 700, 950, 730)])
    assert F.derive_page(doc3, ROLES, REC, ink=_ink(lines), margin=0)["open_at_end"] is False


def test_wide_start_is_a_full_width_fragment_and_opens_the_record():
    lines = [(60, 100, 940, 130)] + _text_lines(100, 450, 150, 400) + _text_lines(550, 900, 150, 400)
    doc = _doc([_sh("name", 60, 100, 940, 130)])
    res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0, carry_open=False)
    # auto with no narrow boxes = a single column: the wide start simply
    # heads one fragment that runs on to the end of the ink below it
    assert _boxes(res) == [[60, 100, 940, 408]] and _conts(res) == [False]
    assert res["open_at_end"] is True
    # two columns declared: the wide start is a band of its own, then one
    # continuation per column below it
    res2 = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0, carry_open=False, layout=2)
    assert _boxes(res2) == [[60, 100, 940, 130], [100, 150, 450, 408], [550, 150, 900, 408]]
    assert _conts(res2) == [False, True, True]


def test_single_column_wide_starts_head_their_fragments():
    # A one-column volume whose starts are full-width display lines (the
    # 1877 Compass): every start is "wide", yet it must head one fragment
    # running to the next start — never a heading-only fragment followed by
    # a continuation. Regression for the 2026-10-19 dry run (1 start → 3
    # fragments / 2 continuations instead of 2 / 1).
    lines = (_text_lines(80, 920, 20, 90) + [(60, 100, 940, 130)] + _text_lines(80, 920, 150, 400)
             + [(60, 420, 940, 450)] + _text_lines(80, 920, 470, 700))
    doc = _doc([_sh("name", 60, 100, 940, 130), _sh("name", 60, 420, 940, 450)])
    for layout in (1, "auto"):
        res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0, carry_open=True, layout=layout)
        # the headings are the widest lines of the column, so the page-level
        # text block (and every fragment's x) spans them
        assert _boxes(res) == [[60, 20, 940, 98], [60, 100, 940, 408], [60, 420, 940, 698]], layout
        assert _conts(res) == [True, False, False], layout
        assert res["open_at_end"] is True


def test_start_at_column_top_has_no_leading_fragment():
    lines = _text_lines(100, 900, 0, 300)
    doc = _doc([_sh("name", 100, 0, 400, 18)])
    res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0, carry_open=True)
    assert len(res["fragments"]) == 1 and _conts(res) == [False]
    assert _boxes(res) == [[100, 0, 900, 288]]


def test_page_without_starts_or_stops():
    lines = _text_lines(100, 450, 100, 700) + _text_lines(550, 900, 100, 700)
    doc = _doc([_sh("advert", 560, 500, 890, 600)])     # only an ignore box
    # record open → one continuation per column (layout forced: no boxes to
    # find the gutter from)
    res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0, carry_open=True, layout=2)
    assert _boxes(res) == [[100, 100, 450, 688], [550, 100, 900, 688]]
    assert _conts(res) == [True, True] and res["open_at_end"] is True
    assert res["starts"] == 0 and res["stops"] == 0 and res["ignores"] == 1
    # record closed → nothing
    res2 = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0, carry_open=False, layout=2)
    assert res2["fragments"] == [] and res2["open_at_end"] is False
    # an empty column (no ink) yields nothing even when open
    res3 = F.derive_page(doc, ROLES, REC, ink=_ink([]), margin=0, carry_open=True, layout=2)
    assert res3["fragments"] == []


def test_ignore_boxes_are_masked_out_of_the_ink_trim():
    # start at 100, text to 300, then an advertisement block 500..700
    lines = _text_lines(100, 900, 100, 300) + [(100, 500, 900, 700)]
    doc = _doc([_sh("name", 100, 100, 400, 118), _sh("advert", 100, 500, 900, 700)])
    res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0)
    assert _boxes(res) == [[100, 100, 900, 298]]
    # without the ignore role the fragment runs down to the advertisement
    res2 = F.derive_page(doc, {"name": "start"}, REC, ink=_ink(lines), margin=0)
    assert _boxes(res2) == [[100, 100, 900, 700]]


def test_scan_edges_and_running_head_line_are_not_ink(tmp_path):
    # Regression (2026-10-19, compass_1877 p70): a dark scanner edge along
    # the page top and a page number in the margin, on the running head's
    # line but outside its ignore box, pinned the leading continuation to
    # y≈4 and swallowed the head. Edges are cleared and a furniture ignore
    # box masks its whole line.
    w, h = 1000, 800
    im = Image.new("L", (w, h), 255)
    dr = ImageDraw.Draw(im)
    dr.rectangle([0, 0, w - 1, 5], fill=0)            # scanner edge
    dr.rectangle([0, 0, 5, h - 1], fill=0)
    dr.rectangle([30, 60, 70, 80], fill=0)            # page number in the margin
    dr.rectangle([200, 60, 800, 80], fill=0)          # running head (the ignore box)
    dr.ellipse([400, 24, 450, 56], fill=0)            # a stain below the edge frame (p38)
    for k in range(40):                                # scattered dust, wide but empty (p82)
        dr.point((150 + k * 17, 60 + (k * 7) % 40), fill=0)
        dr.point((151 + k * 17, 60 + (k * 7) % 40), fill=0)
    dr.rectangle([25, 104, 300, 107], fill=0)         # a scanner-edge hairline, 4 rows (p446)
    for x1, y1, x2, y2 in _text_lines(100, 900, 120, 400):
        dr.rectangle([x1, y1, x2 - 1, y2 - 1], fill=0)
    dr.rectangle([100, 420, 190, 438], fill=0)        # a short last line (two words) is text
    dr.ellipse([600, 520, 640, 560], fill=0)          # a blot further down is not
    path = tmp_path / "p.jpg"; im.save(path)
    ink = F.InkMap.from_image(path, 160)
    doc = _doc([_sh("advert", 200, 60, 800, 80)], w=w, h=h)
    res = F.derive_page(doc, ROLES, REC, ink=ink, margin=0, carry_open=True, layout=1)
    assert _boxes(res) == [[100, 120, 900, 439]] and _conts(res) == [True]


def test_fragment_width_is_the_pages_text_block_not_a_speck():
    # A speck in the margin on the rows of one fragment used to pull that
    # fragment's edge out to the page edge. The x extent is now decided
    # once per column slot from the whole page's ink histogram.
    lines = _text_lines(100, 900, 50, 700)
    lines.append((20, 300, 26, 306))                   # speck in the left margin
    lines.append((970, 500, 976, 506))                 # and one in the right margin
    doc = _doc([_sh("name", 100, 50, 900, 68), _sh("name", 100, 380, 900, 398)])
    res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=0, carry_open=False, layout=1)
    assert _boxes(res) == [[100, 50, 900, 368], [100, 380, 900, 698]]
    # no ink in the slot: the median edges of the role boxes stand in
    blank = F.InkMap.blank(1000, 800)
    assert F._slot_extent(blank, 0, 1000,
                          [[120, 50, 880, 68], [110, 380, 890, 398], [115, 700, 885, 720]]) == (115, 885)
    assert F._slot_extent(blank, 0, 1000, []) is None


def test_margin_and_no_ink_map():
    lines = _text_lines(100, 900, 100, 300)
    doc = _doc([_sh("name", 100, 100, 400, 118)])
    res = F.derive_page(doc, ROLES, REC, ink=_ink(lines), margin=5)
    assert _boxes(res) == [[95, 100, 905, 303]]       # top = the start's top, never above
    res2 = F.derive_page(doc, ROLES, REC, ink=None, carry_open=False)
    assert _boxes(res2) == [[0, 100, 1000, 800]]       # untrimmed: the column slot
    res3 = F.derive_page(doc, ROLES, REC, ink=None, carry_open=True)
    assert _boxes(res3) == [[0, 0, 1000, 100], [0, 100, 1000, 800]]   # no ink map: nothing is dropped


# ── replacement rule ─────────────────────────────────────────────────────────

def _derived(x1, y1, x2, y2, cont=False, **extra):
    sh = _sh(REC, x1, y1, x2, y2, **extra)
    if cont:
        sh["continuation"] = True
    sh[F.MARKER_KEY] = {"kind": F.MARKER_KIND, "bbox": [x1, y1, x2, y2],
                        "continuation": cont, "start_label": "name", "start_idx": 0, "column": 0}
    return sh


def test_is_replaceable():
    assert F.is_replaceable(_derived(100, 100, 900, 300))
    assert not F.is_replaceable(_sh(REC, 100, 100, 900, 300))                       # hand-drawn
    moved = _derived(100, 100, 900, 300); moved["points"] = [[100, 100], [900, 320]]
    assert not F.is_replaceable(moved)
    assert not F.is_replaceable(_derived(100, 100, 900, 300, continuation=True))   # mark toggled
    assert not F.is_replaceable(_derived(100, 100, 900, 300, group_locked=True))
    assert not F.is_replaceable(_derived(100, 100, 900, 300,
                                         human_output={"human_corrected_text": "x"}))
    assert not F.is_replaceable(_derived(100, 100, 900, 300, row_struct={"rows": [{}]}))
    assert not F.is_replaceable(_derived(100, 100, 900, 300, flags={"blank": True}))
    # a swept id does not protect: ids are the sweep's, not the human's
    assert F.is_replaceable(_derived(100, 100, 900, 300, group_id=7))


def test_merge_keeps_identical_fragments_replaces_moved_ones_and_protects_hand_work():
    old_same = _derived(100, 100, 900, 300, group_id=3)
    old_stale = _derived(100, 400, 900, 500)
    hand = _sh(REC, 100, 600, 900, 700, human_output={"human_corrected_text": "by hand"})
    other = _sh("name", 100, 100, 400, 118)
    doc = _doc([other, old_same, old_stale, hand])
    new = [F._fragment([100, 100, 900, 300], REC, False, (0, None, "name"), 0),
           F._fragment([100, 400, 900, 520], REC, False, (0, None, "name"), 0),
           F._fragment([100, 600, 900, 690], REC, False, (0, None, "name"), 0)]   # overlaps hand
    m = F.merge_fragments(doc, REC, new)
    assert m == {"derived": 3, "written": 1, "replaced": 1, "unchanged": 1,
                 "protected": 1, "suppressed": 1, "changed": True}
    labels = [(s["label"], F._bbox(s)) for s in doc["shapes"]]
    assert labels == [("name", (100, 100, 400, 118)), (REC, (100, 100, 900, 300)),
                      (REC, (100, 600, 900, 700)), (REC, (100, 400, 900, 520))]
    assert doc["shapes"][1]["group_id"] == 3                 # the identical one survived
    # second merge with the same fragments: nothing changes
    m2 = F.merge_fragments(doc, REC, new)
    assert m2["changed"] is False and m2["written"] == 0 and m2["replaced"] == 0


def test_normalize_roles():
    assert F.normalize_roles({"a": "start", "b": "", "c": None}) == {"a": "start"}
    assert F.normalize_roles(None) == {}
    with pytest.raises(ValueError):
        F.normalize_roles({"a": "begin"})
    with pytest.raises(ValueError):
        F.normalize_roles({"zz": "start"}, labels=["a"])
    with pytest.raises(ValueError):
        F.normalize_roles({"entry": "start"}, labels=["entry"], record_label="entry")
    assert F.record_roles_for({"record_roles": {"a": "bogus"}}) == {}


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


def _page_image(path, lines, w=1000, h=800):
    im = Image.new("RGB", (w, h), "white")
    dr = ImageDraw.Draw(im)
    for x1, y1, x2, y2 in lines:
        dr.rectangle([x1, y1, x2 - 1, y2 - 1], fill=(0, 0, 0))
    im.save(path)


@pytest.fixture()
def frag_project():
    name = f"_smoke_fragments_{uuid.uuid4().hex[:6]}"
    create_project(name, "B", [REC, "name", "heading", "advert"])
    pdir = PROJECTS_ROOT / name
    ann = pdir / "annotations"
    ann.mkdir(parents=True, exist_ok=True)
    # f_1: one column, two starts, text 100..370 and 400..700 (record open at the end)
    (ann / "f_1.json").write_text(json.dumps(_doc(
        [_sh("name", 100, 100, 400, 118), _sh("name", 100, 400, 400, 418)])), encoding="utf-8")
    _page_image(ann / "f_1.jpg", _text_lines(100, 900, 100, 370) + _text_lines(100, 900, 400, 700))
    # f_2: no starts / stops, text everywhere → a continuation page
    (ann / "f_2.json").write_text(json.dumps(_doc([])), encoding="utf-8")
    _page_image(ann / "f_2.jpg", _text_lines(100, 900, 100, 700))
    # f_3: text 100..250 (continuation), heading at 300 closes, start at 400
    (ann / "f_3.json").write_text(json.dumps(_doc(
        [_sh("heading", 100, 300, 900, 330), _sh("name", 100, 400, 400, 418)], status="predicted")),
        encoding="utf-8")
    _page_image(ann / "f_3.jpg", _text_lines(100, 900, 100, 250) + [(100, 300, 900, 330)]
                + _text_lines(100, 900, 400, 700))
    # f_4: skip page
    (ann / "f_4.json").write_text(json.dumps(_doc([], status="skip")), encoding="utf-8")
    _page_image(ann / "f_4.jpg", [])
    _settle(pdir / "config.json")
    try:
        yield name, pdir
    finally:
        _rm(pdir)


def _page(pdir, stem):
    return json.loads((pdir / "annotations" / f"{stem}.json").read_text(encoding="utf-8"))


def _rec_boxes(doc):
    return [([round(v) for v in F._bbox(s)], bool(s.get("continuation")))
            for s in doc["shapes"] if s["label"] == REC]


def test_config_roles_validation(client, frag_project):
    name, pdir = frag_project
    r = client.patch(f"/api/project/{name}/config", json={"record_label": REC})
    assert r.status_code == 200
    r = client.patch(f"/api/project/{name}/config", json={"record_roles": {"name": "begin"}})
    assert r.status_code == 400 and "begin" in r.json()["detail"]
    r = client.patch(f"/api/project/{name}/config", json={"record_roles": {REC: "start"}})
    assert r.status_code == 400 and "output" in r.json()["detail"]
    r = client.patch(f"/api/project/{name}/config", json={"record_roles": {"nope": "start"}})
    assert r.status_code == 400
    r = client.patch(f"/api/project/{name}/config",
                     json={"record_roles": {**ROLES, "unused": ""}})
    assert r.status_code == 200 and r.json()["config"]["record_roles"] == ROLES
    ctx = client.get("/api/records/context", params={"folder": str(pdir / "annotations")}).json()
    assert ctx["record_roles"] == ROLES and ctx["record_label"] == REC
    # clearing
    r = client.patch(f"/api/project/{name}/config", json={"record_roles": {}})
    assert r.status_code == 200 and "record_roles" not in r.json()["config"]
    # no start role → the derivation refuses
    r = client.post(f"/api/project/{name}/derive-fragments", json={"dry_run": True})
    assert r.status_code == 400 and "start" in r.json()["detail"]


def test_derive_endpoint_dry_run_write_rerun_sweep_and_cut(client, frag_project):
    name, pdir = frag_project
    ann = pdir / "annotations"
    assert client.patch(f"/api/project/{name}/config",
                        json={"record_label": REC, "record_roles": ROLES}).status_code == 200
    before = {s: (ann / f"{s}.json").read_text(encoding="utf-8") for s in ("f_1", "f_2", "f_3")}

    # dry run: counts, candidate continuation pages, no writes
    r = client.post(f"/api/project/{name}/derive-fragments", json={"dry_run": True, "margin": 0})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["dry_run"] and d["pages_scanned"] == 3 and d["pages_selected"] == 3
    assert d["empty_pages"] == ["f_2"]
    rows = {p["stem"]: p for p in d["pages"]}
    assert rows["f_1"]["fragments"] == 2 and rows["f_1"]["continuations"] == 0
    assert rows["f_2"]["fragments"] == 1 and rows["f_2"]["continuations"] == 1
    assert rows["f_3"]["fragments"] == 2 and rows["f_3"]["continuations"] == 1
    assert d["pages_written"] == 3 and d["written"] == 5
    for s, txt in before.items():
        assert (ann / f"{s}.json").read_text(encoding="utf-8") == txt

    # real run
    r = client.post(f"/api/project/{name}/derive-fragments", json={"margin": 0})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["pages_written"] == 3 and d["written"] == 5 and d["replaced"] == 0
    assert _rec_boxes(_page(pdir, "f_1")) == [([100, 100, 900, 358], False), ([100, 400, 900, 688], False)]
    assert _rec_boxes(_page(pdir, "f_2")) == [([100, 100, 900, 688], True)]
    assert _rec_boxes(_page(pdir, "f_3")) == [([100, 100, 900, 238], True), ([100, 400, 900, 688], False)]
    assert all(F.is_derived(s) for s in _page(pdir, "f_1")["shapes"] if s["label"] == REC)
    assert "shapes" in _page(pdir, "f_4") and _rec_boxes(_page(pdir, "f_4")) == []   # skip page untouched

    # re-run: nothing changes
    r = client.post(f"/api/project/{name}/derive-fragments", json={"margin": 0})
    d = r.json()
    assert d["pages_written"] == 0 and d["written"] == 0 and d["replaced"] == 0 and d["unchanged"] == 5

    # a human draws a box on f_2 and annotates the first derived one on f_1;
    # a re-run with a bigger margin re-derives the rest, leaves those alone
    p2 = _page(pdir, "f_2")
    p2["shapes"].append(_sh(REC, 100, 100, 900, 700, human_output={"human_corrected_text": "hand"}))
    (ann / "f_2.json").write_text(json.dumps(p2), encoding="utf-8")
    p1 = _page(pdir, "f_1")
    first = next(s for s in p1["shapes"] if s["label"] == REC)
    first["human_output"] = {"human_corrected_text": "annotated"}
    (ann / "f_1.json").write_text(json.dumps(p1), encoding="utf-8")
    r = client.post(f"/api/project/{name}/derive-fragments", json={"margin": 2})
    d = r.json()
    rows = {p["stem"]: p for p in d["pages"]}
    assert rows["f_2"]["protected"] == 1 and rows["f_2"]["suppressed"] == 1
    assert rows["f_1"]["protected"] == 1 and rows["f_1"]["suppressed"] == 1 \
        and rows["f_1"]["replaced"] == 1 and rows["f_1"]["written"] == 1
    p1 = _page(pdir, "f_1")
    assert _rec_boxes(p1) == [([100, 100, 900, 358], False), ([98, 400, 902, 690], False)]
    assert next(s for s in p1["shapes"] if s["label"] == REC)["human_output"]["human_corrected_text"] == "annotated"
    p2 = _page(pdir, "f_2")
    assert _rec_boxes(p2) == [([100, 100, 900, 700], False)]          # the derived one replaced by nothing
    # (the stale derived fragment on f_2 was replaceable and overlapped the hand box → gone)

    # status filter + pattern narrow the selection but the carry state still
    # flows through the unselected pages
    r = client.post(f"/api/project/{name}/derive-fragments",
                    json={"margin": 2, "statuses": ["predicted"], "dry_run": True})
    d = r.json()
    assert d["pages_selected"] == 1 and [p["stem"] for p in d["pages"]] == ["f_3"]
    assert d["pages"][0]["continuations"] == 1
    r = client.post(f"/api/project/{name}/derive-fragments",
                    json={"margin": 2, "pattern": "^f_1$", "dry_run": True})
    assert r.json()["pages_selected"] == 1

    # chained sweep numbers the derived fragments; the crop round trip cuts them
    r = client.post(f"/api/project/{name}/derive-fragments", json={"margin": 2, "sweep": True})
    d = r.json()
    assert d["sweep"]["records"] >= 2 and d["sweep"]["pages_written"] >= 1
    p3 = _page(pdir, "f_3")
    ids = [s.get("group_id") for s in p3["shapes"] if s["label"] == REC]
    assert all(isinstance(g, int) for g in ids)
    hand_f2 = [s["group_id"] for s in _page(pdir, "f_2")["shapes"] if s["label"] == REC][-1]
    assert ids[0] == hand_f2                      # f_3's continuation joins the record open on f_2
    r = client.post(f"/api/project/{name}/derive-crops",
                    json={"label": REC, "target": f"{name}_crops", "statuses": ["verified", "predicted"],
                          "dry_run": True})
    assert r.status_code == 200, r.text
    assert r.json()["boxes"] == 5 and r.json()["created"] == 5
