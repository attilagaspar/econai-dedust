"""P10: frozen test set, status-filtered training export, training ledger,
and the corrections-per-page scorer."""
import json
import shutil
import uuid
from pathlib import Path

import pytest

from app import training_meta, eval_diff
from app.coco_convert import prepare_training_data
from app.pipeline import create_project, PROJECTS_ROOT

BASE_YAML = Path(__file__).resolve().parents[1] / "samples" / "ertesito2" / "fast_rcnn_R_50_FPN_3x.yaml"


def _page(ann_dir: Path, stem: str, status: str, shapes: list):
    data = {"imagePath": f"{stem}.jpg", "imageWidth": 1000, "imageHeight": 1400,
            "flags": {"status": status}, "shapes": shapes}
    (ann_dir / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")


def _box(label, x1=100, y1=100, x2=300, y2=200):
    return {"label": label, "points": [[x1, y1], [x2, y2]],
            "shape_type": "rectangle", "flags": {}}


@pytest.fixture()
def proj(tmp_path):
    pdir = tmp_path / "proj"
    ann = pdir / "annotations"
    ann.mkdir(parents=True)
    _page(ann, "p1", "verified",  [_box("cell")])
    _page(ann, "p2", "corrected", [_box("cell"), _box("header", 10, 10, 90, 40)])
    _page(ann, "p3", "predicted", [_box("cell")])          # uncorrected — excluded
    _page(ann, "p4", "verified",  [])                      # verified-empty negative
    _page(ann, "p5", "skip",      [_box("cell")])          # never trains
    _page(ann, "p6", "verified",  [_box("header", 10, 10, 90, 40)])
    return pdir


# ── frozen test set ──────────────────────────────────────────────────────────

def test_freeze_picks_verified_only(proj):
    ts = training_meta.freeze_test_set(proj, n=2)
    assert len(ts["stems"]) == 2
    for stem in ts["stems"]:
        data = json.loads((proj / "annotations" / f"{stem}.json").read_text())
        assert (data["flags"]["status"]) == "verified"


def test_freeze_refuses_overwrite_and_too_many(proj):
    training_meta.freeze_test_set(proj, n=1)
    with pytest.raises(ValueError):
        training_meta.freeze_test_set(proj, n=1)           # already frozen
    training_meta.unfreeze_test_set(proj)
    with pytest.raises(ValueError):
        training_meta.freeze_test_set(proj, n=99)          # not enough verified


def test_drift_detection(proj):
    training_meta.freeze_test_set(proj, stems=["p1"])
    st = training_meta.test_set_status(proj)
    assert st["changed"] == [] and st["missing"] == []
    # edit the frozen page → flagged as changed
    _page(proj / "annotations", "p1", "verified", [_box("cell", 1, 1, 50, 50)])
    st = training_meta.test_set_status(proj)
    assert st["changed"] == ["p1"]


# ── status-filtered export + server-side split ───────────────────────────────

def _prep(proj, **kw):
    return prepare_training_data(
        project_name="tproj", ann_dir=proj / "annotations",
        labels=["cell", "header"], intermediate_dir=proj / "intermediate",
        base_yaml_path=BASE_YAML, **kw)


def test_status_filter_and_negatives(proj):
    r = _prep(proj)
    # p1, p2, p6 train; p4 negative; p3 (predicted) + p5 (skip) excluded
    assert r["n_train_pages"] == 4
    assert r["n_negatives"] == 1
    assert r["n_excluded_status"] == 1
    assert r["n_skip"] == 1
    assert r["server_side_split"] is False
    # legacy path: no local split files, train.sh runs cocosplit
    assert not (proj / "intermediate" / "train.json").exists()
    assert "cocosplit" in (proj / "intermediate" / "train.sh").read_text(encoding="utf-8")


def test_filter_off_includes_predicted(proj):
    r = _prep(proj, status_filter=False)
    assert r["n_excluded_status"] == 0
    assert r["n_train_pages"] == 5          # everything except skip
    # skip NEVER enters, even unfiltered
    coco = json.loads((proj / "intermediate" / "annotations.json").read_text())
    assert all(im["file_name"] != "p5.jpg" for im in coco["images"])


def test_frozen_split_written_and_cocosplit_skipped(proj):
    training_meta.freeze_test_set(proj, stems=["p1"])
    r = _prep(proj, test_stems=["p1"])
    assert r["server_side_split"] is True
    assert r["n_test_pages"] == 1
    train = json.loads((proj / "intermediate" / "train.json").read_text())
    test  = json.loads((proj / "intermediate" / "test.json").read_text())
    assert [im["file_name"] for im in test["images"]] == ["p1.jpg"]
    assert all(im["file_name"] != "p1.jpg" for im in train["images"])
    sh = (proj / "intermediate" / "train.sh").read_text(encoding="utf-8")
    assert "cocosplit.py" not in sh
    assert "FILTER_EMPTY_ANNOTATIONS False" in sh          # p4 negative included
    # negative page present in train.json with no annotations
    p4 = next(im for im in train["images"] if im["file_name"] == "p4.jpg")
    assert not any(a["image_id"] == p4["id"] for a in train["annotations"])


def test_missing_frozen_stem_reported(proj):
    r = _prep(proj, test_stems=["p1", "ghost"])
    assert r["missing_test_stems"] == ["ghost"]


def test_train_fraction_nested(proj):
    full = _prep(proj)                                     # 4 training pages
    half = _prep(proj, train_fraction=0.5)
    assert half["n_train_pages"] == 2
    assert half["n_train_pool"] == full["n_train_pages"]
    # nested: the 50% stems are a subset of the 75% stems
    def stems(frac):
        _prep(proj, train_fraction=frac)
        coco = json.loads((proj / "intermediate" / "annotations.json").read_text())
        return {im["file_name"] for im in coco["images"]}
    assert stems(0.5) <= stems(0.75) <= stems(1.0)


# ── correction telemetry ─────────────────────────────────────────────────────

def test_record_correction_and_summary(tmp_path):
    pdir = tmp_path
    (pdir / "annotations").mkdir()
    (pdir / "predictions").mkdir()
    # model predicted one cell; the human kept it and added a header
    _page(pdir / "predictions", "p1", "predicted", [_box("cell")])
    _page(pdir / "annotations", "p1", "corrected",
          [_box("cell"), _box("header", 10, 10, 90, 40)])
    row = training_meta.record_correction(pdir, "p1", "predicted", "corrected")
    assert row and row["added"] == 1 and row["ok"] == 1 and row["corrections"] == 1
    # irrelevant transitions are not logged
    assert training_meta.record_correction(pdir, "p1", "corrected", "verified") is None
    assert training_meta.record_correction(pdir, "nope", "predicted", "verified") is None
    s = training_meta.corrections_summary(pdir)
    assert s["n"] == 1 and s["recent_avg_corrections_per_page"] == 1.0
    # re-correcting the same page replaces its row instead of double-counting
    training_meta.record_correction(pdir, "p1", "predicted", "verified")
    assert training_meta.corrections_summary(pdir)["n"] == 1


def test_status_change_endpoint_logs_correction(client, api_proj):
    pdir = PROJECTS_ROOT / api_proj
    (pdir / "predictions").mkdir(exist_ok=True)
    _page(pdir / "predictions", "p3", "predicted", [_box("cell")])
    _page(pdir / "annotations", "p3", "predicted",
          [_box("cell"), _box("header", 10, 10, 90, 40)])
    folder = str(pdir / "annotations")
    r = client.patch(f"/api/page/flags?folder={folder}&stem=p3",
                     json={"flags": {"status": "corrected"}})
    assert r.status_code == 200
    s = client.get(f"/api/project/{api_proj}/corrections-log").json()
    assert s["n"] == 1 and s["rows"][0]["stem"] == "p3"
    assert s["rows"][0]["added"] == 1


# ── ledger ───────────────────────────────────────────────────────────────────

def test_ledger_append_update(tmp_path):
    rid = training_meta.ledger_append(tmp_path, {"mode": "train", "max_iter": 2000})
    assert training_meta.ledger_update(tmp_path, rid, status="finished",
                                       ap={"AP": 71.2})
    rows = training_meta.load_ledger(tmp_path)
    assert rows[-1]["status"] == "finished"
    assert rows[-1]["ap"]["AP"] == 71.2


def test_parse_d2_metrics_takes_last_eval():
    text = "\n".join([
        json.dumps({"iteration": 500, "bbox/AP": 50.0, "bbox/AP50": 70.0}),
        json.dumps({"iteration": 999, "total_loss": 0.4}),
        json.dumps({"iteration": 2000, "bbox/AP": 66.6, "bbox/AP50": 88.8}),
    ])
    ap = training_meta.parse_d2_metrics(text)
    assert ap["AP"] == 66.6 and ap["AP50"] == 88.8 and ap["iteration"] == 2000
    assert training_meta.parse_d2_metrics("no eval here") is None


# ── corrections-per-page scorer ──────────────────────────────────────────────

def test_eval_diff_added_deleted_moved(tmp_path):
    ann = tmp_path / "annotations"; ann.mkdir()
    pred = tmp_path / "predictions"; pred.mkdir()
    # GT: two cells + a header. Pred: one matching cell, one shifted cell
    # (moved), a hallucinated header elsewhere; the real header missed.
    _page(ann, "p1", "verified", [
        _box("cell", 100, 100, 300, 200),
        _box("cell", 400, 100, 600, 200),
        _box("header", 10, 10, 200, 40),
    ])
    _page(pred, "p1", "predicted", [
        _box("cell", 100, 100, 300, 200),            # exact → ok
        _box("cell", 420, 105, 610, 205),            # overlaps ≥0.5 but loose → moved
        _box("header", 500, 500, 700, 540),          # invented → deleted
    ])
    r = eval_diff.evaluate_test_set(ann, pred, ["p1"])
    t = r["totals"]
    assert t["ok"] == 1 and t["moved"] == 1
    assert t["added"] == 1                            # missed header
    assert t["deleted"] == 1                          # invented header
    assert r["corrections_per_page"] == 3.0


def test_eval_diff_missing_prediction_counts_all_added(tmp_path):
    ann = tmp_path / "annotations"; ann.mkdir()
    pred = tmp_path / "predictions"; pred.mkdir()
    _page(ann, "p1", "verified", [_box("cell"), _box("header", 1, 1, 50, 20)])
    r = eval_diff.evaluate_test_set(ann, pred, ["p1"])
    assert r["n_missing_predictions"] == 1
    assert r["totals"]["added"] == 2


# ── endpoints ────────────────────────────────────────────────────────────────

@pytest.fixture()
def api_proj(client):
    name = f"_pytest_p10_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", ["cell", "header"])
    ann = PROJECTS_ROOT / name / "annotations"
    _page(ann, "p1", "verified", [_box("cell")])
    _page(ann, "p2", "verified", [_box("cell")])
    try:
        yield name
    finally:
        shutil.rmtree(PROJECTS_ROOT / name, ignore_errors=True)


def test_test_set_endpoints(client, api_proj):
    r = client.get(f"/api/project/{api_proj}/test-set")
    assert r.json()["exists"] is False
    r = client.post(f"/api/project/{api_proj}/test-set", json={"n": 1})
    assert r.status_code == 200 and r.json()["n"] == 1
    r = client.get(f"/api/project/{api_proj}/test-set")
    assert r.json()["exists"] is True
    # second freeze without force → 400
    r = client.post(f"/api/project/{api_proj}/test-set", json={"n": 1})
    assert r.status_code == 400
    r = client.delete(f"/api/project/{api_proj}/test-set")
    assert r.json()["removed"] is True


def test_training_log_endpoint(client, api_proj):
    r = client.get(f"/api/project/{api_proj}/training-log")
    assert r.json()["rows"] == []
    training_meta.ledger_append(PROJECTS_ROOT / api_proj,
                                {"mode": "train", "max_iter": 100})
    r = client.get(f"/api/project/{api_proj}/training-log")
    assert r.json()["rows"][0]["mode"] == "train"


def test_test_eval_endpoint_requires_frozen_set(client, api_proj):
    r = client.post(f"/api/project/{api_proj}/test-eval", json={})
    assert r.status_code == 400
    assert "frozen test set" in r.json()["detail"].lower()
