"""§H8 cascade without cloned label sets: a page-scope detector that predicts
a SUBSET of the target project's labels (a one-label parent detector onto a
project whose labels are that parent plus its parts).

Covers, without a GPU: the label-subset rule (accepted / refused, source
order kept, fine-tune-from stays strict); the page-scope merge by label set
(scored shapes of the set replaced, human shapes and other labels untouched,
skip pages untouched, write only when changed, idempotent); the merge
endpoint's two modes; and the full cascade on throwaway _smoke_ projects:
parent model infer-from → merge parents onto populated pages → within-scope
merge of the parts on top.
"""
import json
import shutil
import time
import uuid

import pytest
from PIL import Image

from app import scope as S
from app.pipeline import create_project, PROJECTS_ROOT


# ── helpers ──────────────────────────────────────────────────────────────────

def _sh(label, x1, y1, x2, y2, **extra):
    d = {"label": label, "points": [[x1, y1], [x2, y2]], "group_id": None,
         "shape_type": "rectangle", "flags": {}}
    d.update(extra)
    return d


def _page(ann_dir, stem, shapes, status="verified", w=1000, h=800):
    doc = {"version": "5.0.1", "flags": {"status": status}, "shapes": shapes,
           "imagePath": f"{stem}.jpg", "imageWidth": w, "imageHeight": h}
    (ann_dir / f"{stem}.json").write_text(json.dumps(doc), encoding="utf-8")
    Image.new("RGB", (w, h), "white").save(ann_dir / f"{stem}.jpg")
    return doc


def _labels(ann_dir, stem):
    page = json.loads((ann_dir / f"{stem}.json").read_text(encoding="utf-8"))
    return [(s["label"], s.get("score")) for s in page["shapes"]]


def _settle(path):
    """projects/ lives in Dropbox: a just-written file can be briefly
    unopenable while the sync client grabs it."""
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


PARTS   = ["firm_area", "name", "address", "capital"]
PARENTS = ["firm_area"]


@pytest.fixture()
def cascade_projects():
    """`parents`: a one-label project (the parent detector). `target`: the
    richer project (parent + parts) with one populated, one empty and one
    skip page. Both throwaway, under the real projects/ root."""
    tag = uuid.uuid4().hex[:6]
    parents, target = f"_smoke_lsub_src_{tag}", f"_smoke_lsub_tgt_{tag}"
    create_project(parents, "A", PARENTS)
    create_project(target, "A", PARTS)
    ann = PROJECTS_ROOT / target / "annotations"
    ann.mkdir(parents=True, exist_ok=True)
    _page(ann, "p1", [
        _sh("name", 120, 110, 480, 140),                      # human part, no parent yet
        _sh("firm_area", 100, 450, 500, 700, score=0.4),      # an earlier predicted parent
        _sh("capital", 120, 460, 480, 490, score=0.5),        # earlier predicted part
    ])
    _page(ann, "p2", [])                                      # empty page
    _page(ann, "p3", [_sh("name", 0, 0, 100, 30)], status="skip")
    _settle(PROJECTS_ROOT / target / "config.json")
    _settle(PROJECTS_ROOT / parents / "config.json")
    try:
        yield parents, target, PROJECTS_ROOT / target
    finally:
        _rm(PROJECTS_ROOT / parents)
        _rm(PROJECTS_ROOT / target)


# ── label-subset rule (pure) ─────────────────────────────────────────────────

def test_missing_labels_subset_and_order():
    assert S.missing_labels(["firm_area"], PARTS) == []
    assert S.missing_labels(PARTS, PARTS) == []
    assert S.missing_labels(["b", "a"], ["a", "b", "c"]) == []
    assert S.missing_labels(["x", "firm_area", "y"], PARTS) == ["x", "y"]   # source order
    assert S.missing_labels([], PARTS) == []
    assert S.missing_labels(PARTS, ["firm_area"]) == ["name", "address", "capital"]


# ── page-scope merge by label set (pure) ─────────────────────────────────────

def test_merge_labels_rules():
    ann = [
        _sh("name", 120, 110, 480, 140),                       # human (kept)
        _sh("firm_area", 100, 100, 500, 400),                  # human parent (kept)
        _sh("firm_area", 100, 450, 500, 700, score=0.4),       # predicted, in set → replaced
        _sh("capital", 120, 460, 480, 490, score=0.5),         # predicted, other label → kept
    ]
    pred = [_sh("firm_area", 98, 448, 502, 702, score=0.9),
            _sh("firm_area", 100, 90, 500, 410, score=0.8),
            _sh("name", 0, 0, 10, 10, score=0.7),              # not in the set → never inserted
            {"label": "firm_area", "points": [[1, 1]]}]                # degenerate → dropped
    res = S.merge_labels(ann, pred, ["firm_area"])
    assert res["removed"] == 1 and res["inserted"] == 2 and res["changed"]
    got = [(s["label"], s.get("score")) for s in res["shapes"]]
    assert got == [("name", None), ("firm_area", None),              # new shapes take the removed one's place
                   ("firm_area", 0.9), ("firm_area", 0.8), ("capital", 0.5)]
    # idempotent: the same predictions again → identical list, no change reported
    again = S.merge_labels(res["shapes"], pred, ["firm_area"])
    assert again["removed"] == 2 and again["inserted"] == 2 and not again["changed"]
    assert again["shapes"] == res["shapes"]
    # a prediction without a score is still model output: score defaults to 0.0
    res2 = S.merge_labels([], [_sh("firm_area", 0, 0, 10, 10)], ["firm_area"])
    assert res2["shapes"][0]["score"] == 0.0 and res2["changed"]
    # removal only (model now predicts nothing) still counts as a change
    res3 = S.merge_labels([_sh("firm_area", 0, 0, 10, 10, score=0.3)], [], ["firm_area"])
    assert res3["removed"] == 1 and res3["inserted"] == 0 and res3["changed"] and res3["shapes"] == []
    # nothing in the set on either side → no change
    res4 = S.merge_labels([_sh("name", 0, 0, 10, 10, score=0.3)], [], ["firm_area"])
    assert not res4["changed"]


def test_merge_apply_labels_over_folders(tmp_path):
    ann = tmp_path / "annotations"; pred = tmp_path / "predictions"
    ann.mkdir(); pred.mkdir()
    writes = []

    def _w(path, obj):
        writes.append(path.name)
        path.write_text(json.dumps(obj), encoding="utf-8")

    _page(ann, "a", [_sh("name", 10, 10, 100, 40), _sh("firm_area", 0, 0, 500, 500, score=0.5)])
    _page(ann, "b", [_sh("name", 10, 10, 100, 40)])                    # nothing to replace, something to insert
    _page(ann, "c", [_sh("firm_area", 0, 0, 500, 500, score=0.5)], status="skip")   # clutter
    _page(ann, "d", [_sh("name", 10, 10, 100, 40, score=0.2)])         # other label only, nothing predicted
    for stem, shapes in {"a": [_sh("firm_area", 2, 2, 498, 498, score=0.8)],
                         "b": [_sh("firm_area", 1, 1, 400, 400, score=0.9)],
                         "c": [_sh("firm_area", 1, 1, 2, 2, score=0.9)],
                         "d": [], "zz_no_page": [_sh("firm_area", 1, 1, 2, 2)]}.items():
        (pred / f"{stem}.json").write_text(json.dumps({"shapes": shapes}))
    st = S.merge_apply_labels(ann, pred, ["firm_area"], _w)
    assert st == {"applied": 2, "skipped_unchanged": 1, "skipped_clutter": 1,
                  "skipped_no_page": 1, "inserted": 2, "removed": 1}
    assert writes == ["a.json", "b.json"]
    assert _labels(ann, "a") == [("name", None), ("firm_area", 0.8)]
    assert _labels(ann, "b") == [("name", None), ("firm_area", 0.9)]
    assert _labels(ann, "c") == [("firm_area", 0.5)]                   # skip page untouched
    assert _labels(ann, "d") == [("name", 0.2)]                        # other label untouched
    assert json.loads((ann / "a.json").read_text())["flags"]["status"] == "verified"
    # second pass: nothing changes, nothing is written
    writes.clear()
    st2 = S.merge_apply_labels(ann, pred, ["firm_area"], _w)
    assert st2["applied"] == 0 and st2["skipped_unchanged"] == 3 and writes == []


# ── endpoints ────────────────────────────────────────────────────────────────

def test_infer_from_accepts_subset_refuses_superset(client, cascade_projects):
    parents, target, _ = cascade_projects
    # one-label source onto the richer target: label gate passes, next gate
    # is the (unconfigured) GPU server
    r = client.post(f"/api/project/{target}/infer-from/{parents}", json={})
    assert r.status_code == 400, r.text
    assert "Server" in r.json()["detail"] and "Label" not in r.json()["detail"]
    # the reverse (rich source onto the one-label target) is refused and
    # names exactly the labels the target lacks, in source order
    r = client.post(f"/api/project/{parents}/infer-from/{target}", json={})
    assert r.status_code == 400
    d = r.json()["detail"]
    assert "Label mismatch" in d and "['name', 'address', 'capital']" in d
    # unknown source → 404, not a label error
    r = client.post(f"/api/project/{target}/infer-from/_no_such_project_", json={})
    assert r.status_code == 404


def test_finetune_from_stays_strict(client, cascade_projects):
    parents, target, _ = cascade_projects
    r = client.post(f"/api/project/{target}/finetune-from/{parents}", json={})
    assert r.status_code == 400 and "Label mismatch" in r.json()["detail"]
    assert "infer-from accepts a subset" in r.json()["detail"]


def test_infer_from_gen_reports_model_labels(cascade_projects):
    """The SSE generator names the labels the source model predicts (and the
    target labels it leaves alone) before it touches SSH."""
    from app.server import _push_infer_from_gen
    parents, target, _ = cascade_projects
    srv = {"host": "h", "user": "u", "key_path": "k", "remote_path": "/r"}
    gen = _push_infer_from_gen(target, parents, srv, None, scope="page")
    lines = []
    for line in gen:
        lines.append(line)
        if line.startswith("[infer-from] target labels the model does not predict"):
            break
    assert "[infer-from] model predicts labels: firm_area" in lines
    assert lines[-1].endswith("name, address, capital")
    gen.close()


def test_merge_endpoint_page_scope_needs_known_labels(client, cascade_projects):
    _, target, pdir = cascade_projects
    (pdir / "predictions").mkdir(exist_ok=True)
    r = client.post(f"/api/project/{target}/apply-predictions-merge", json={})
    assert r.status_code == 400 and "labels" in r.json()["detail"]
    r = client.post(f"/api/project/{target}/apply-predictions-merge", json={"labels": []})
    assert r.status_code == 400
    r = client.post(f"/api/project/{target}/apply-predictions-merge",
                    json={"labels": ["firm_area", "bogus"]})
    assert r.status_code == 400 and "['bogus']" in r.json()["detail"]


def test_cascade_parent_merge_then_within_scope_merge(client, cascade_projects):
    """Parent detector output merged onto populated pages, then the scoped
    parts model merged inside those parents — both via the endpoint, both
    idempotent, human shapes never touched, skip pages never touched."""
    _, target, pdir = cascade_projects
    ann = pdir / "annotations"
    pred = pdir / "predictions"; pred.mkdir()

    # ── step 1: the one-label parent model's page-scope predictions ──────────
    for stem, shapes in {"p1": [_sh("firm_area", 100, 100, 500, 400, score=0.93),
                                _sh("firm_area", 100, 450, 500, 700, score=0.88)],
                         "p2": [_sh("firm_area", 50, 50, 950, 750, score=0.97)],
                         "p3": [_sh("firm_area", 0, 0, 900, 700, score=0.9)]}.items():
        (pred / f"{stem}.json").write_text(json.dumps({"shapes": shapes}))
    r = client.post(f"/api/project/{target}/apply-predictions-merge",
                    json={"scope": "page", "labels": ["firm_area"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["labels"] == ["firm_area"]
    assert (d["applied"], d["inserted"], d["removed"], d["skipped_clutter"]) == (2, 3, 1, 1)
    assert _labels(ann, "p1") == [("name", None), ("firm_area", 0.93), ("firm_area", 0.88),
                                  ("capital", 0.5)]
    assert _labels(ann, "p2") == [("firm_area", 0.97)]
    assert _labels(ann, "p3") == [("name", None)]                     # skip page untouched
    # idempotent: same predictions → nothing written
    r = client.post(f"/api/project/{target}/apply-predictions-merge",
                    json={"scope": "page", "labels": ["firm_area"]})
    assert r.json()["applied"] == 0 and r.json()["skipped_unchanged"] == 2

    # ── step 2: the within:firm_area parts model's predictions ───────────────
    for f in pred.glob("*.json"):
        f.unlink()
    (pred / "p1.json").write_text(json.dumps({"shapes": [
        _sh("name", 122, 112, 478, 138, score=0.9),        # duplicates the human name (kept both: human untouched)
        _sh("address", 120, 150, 480, 180, score=0.85),
        _sh("capital", 120, 462, 480, 488, score=0.8)]}))  # replaces the earlier predicted capital
    (pred / "p2.json").write_text(json.dumps({"shapes": [_sh("name", 60, 60, 600, 90, score=0.7)]}))
    (pred / "p3.json").write_text(json.dumps({"shapes": [_sh("name", 1, 1, 50, 20, score=0.7)]}))
    r = client.post(f"/api/project/{target}/apply-predictions-merge",
                    json={"scope": "within:firm_area"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["scope"] == "within:firm_area"
    assert (d["applied"], d["inserted"], d["removed"], d["skipped_clutter"]) == (2, 4, 1, 1)
    assert _labels(ann, "p1") == [("name", None), ("firm_area", 0.93), ("firm_area", 0.88),
                                  ("name", 0.9), ("address", 0.85), ("capital", 0.8)]
    assert _labels(ann, "p2") == [("firm_area", 0.97), ("name", 0.7)]
    assert _labels(ann, "p3") == [("name", None)]

    # ── step 3: re-running the parent merge leaves the parts alone ───────────
    for f in pred.glob("*.json"):
        f.unlink()
    (pred / "p1.json").write_text(json.dumps({"shapes": [
        _sh("firm_area", 100, 100, 500, 400, score=0.93),
        _sh("firm_area", 100, 450, 500, 700, score=0.88)]}))
    r = client.post(f"/api/project/{target}/apply-predictions-merge",
                    json={"scope": "page", "labels": ["firm_area"]})
    assert r.json()["applied"] == 0
    assert _labels(ann, "p1") == [("name", None), ("firm_area", 0.93), ("firm_area", 0.88),
                                  ("name", 0.9), ("address", 0.85), ("capital", 0.8)]
