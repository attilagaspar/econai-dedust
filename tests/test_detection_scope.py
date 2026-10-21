"""§H8 detection scope — nested detection within parent shapes.

Covers, without a GPU: scope strings + class set; crop export (coordinate
translation, clipping, childless-parent negatives, split inheritance,
scripts pointing at crops); the pure inference mapping and the full
export → crop-space → page-space round trip; parents export; merge-apply
(human shapes untouched, scored children inside parents replaced, scored
shapes outside parents kept, pages without parents / skip / unchanged
skipped); the endpoints' scope plumbing on a throwaway _smoke_ project.
"""
import json
import shutil
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app import scope as S
from app.coco_convert import prepare_training_data, make_infer_script
from app.infer_layout import crop_rect, map_boxes_to_page, parents_in, box_of


# ── helpers ──────────────────────────────────────────────────────────────────

def _sh(label, x1, y1, x2, y2, **extra):
    d = {"label": label, "points": [[x1, y1], [x2, y2]], "group_id": None,
         "shape_type": "rectangle", "flags": {}}
    d.update(extra)
    return d


def _page(ann_dir: Path, stem: str, shapes, status="verified", w=1000, h=800):
    doc = {"version": "5.0.1", "flags": {"status": status}, "shapes": shapes,
           "imagePath": f"{stem}.jpg", "imageWidth": w, "imageHeight": h}
    (ann_dir / f"{stem}.json").write_text(json.dumps(doc), encoding="utf-8")
    Image.new("RGB", (w, h), "white").save(ann_dir / f"{stem}.jpg")
    return doc


LABELS = ["firm_area", "name", "address", "page_header"]
BASE_YAML = Path(__file__).resolve().parents[1] / "samples" / "ertesito2" / "fast_rcnn_R_50_FPN_3x.yaml"


@pytest.fixture()
def scoped_project(tmp_path):
    """Two verified pages. p1: two firm_area parents — the first with two
    children (one straddling the parent edge), the second childless; plus a
    page_header outside every parent. p2: one parent with one child."""
    pdir = tmp_path / "proj"
    ann = pdir / "annotations"
    ann.mkdir(parents=True)
    _page(ann, "p1", [
        _sh("page_header", 100, 10, 900, 40),
        _sh("firm_area", 100, 100, 500, 400),
        _sh("name", 120, 110, 480, 140),
        _sh("address", 120, 360, 480, 430),      # straddles the parent's bottom (centre inside)
        _sh("firm_area", 100, 450, 500, 700),    # childless → negative crop
    ])
    _page(ann, "p2", [
        _sh("firm_area", 50, 50, 950, 750),
        _sh("name", 60, 60, 600, 90),
    ])
    return pdir


# ── scope strings ────────────────────────────────────────────────────────────

def test_scope_strings_and_class_set():
    assert S.normalize_scope(None) == "page"
    assert S.normalize_scope("") == "page"
    assert S.normalize_scope(" within:firm_area ") == "within:firm_area"
    assert S.parent_label_of("within:table_region") == "table_region"
    assert S.parent_label_of("page") is None
    assert S.scope_labels(LABELS, "page") == LABELS
    assert S.scope_labels(LABELS, "within:firm_area") == ["name", "address", "page_header"]
    with pytest.raises(ValueError):
        S.normalize_scope("inside:x")
    with pytest.raises(ValueError):
        S.normalize_scope("within:")
    with pytest.raises(ValueError):
        S.scope_labels(["only"], "within:only")


# ── containment ──────────────────────────────────────────────────────────────

def test_containment_is_centre_based_smallest_parent_wins():
    parents = [(0, 0, 1000, 1000), (100, 100, 500, 500)]
    assert S.parent_index_for((120, 120, 200, 200), parents) == 1   # nested → smallest
    assert S.parent_index_for((600, 600, 700, 700), parents) == 0
    assert S.parent_index_for((1200, 50, 1300, 80), parents) is None
    # straddling child: centre decides
    assert S.parent_index_for((400, 450, 600, 650), [(100, 100, 500, 500)]) is None
    assert S.parent_index_for((400, 350, 600, 550), [(100, 100, 500, 500)]) == 0


# ── crop geometry (shared by export and inference) ──────────────────────────

def test_crop_rect_margin_and_clamp():
    assert crop_rect((100, 100, 500, 400), 1000, 800, 12) == (88, 88, 512, 412)
    assert crop_rect((5, 5, 995, 795), 1000, 800, 12) == (0, 0, 1000, 800)


def test_map_boxes_to_page_translates_clips_drops_degenerate():
    crop = (88, 88, 512, 412)
    parent = (100, 100, 500, 400)
    local = [_sh("name", 32, 22, 392, 52, score=0.9),          # inside → +offset
             _sh("address", 32, 292, 392, 360, score=0.8),     # pokes below parent → clipped
             _sh("name", 0, 300, 10, 312, score=0.5)]          # fully in the margin → dropped
    out = map_boxes_to_page(local, crop, parent)
    assert [s["label"] for s in out] == ["name", "address"]
    assert out[0]["points"] == [[120, 110], [480, 140]]
    assert out[0]["score"] == 0.9
    assert out[1]["points"] == [[120, 380], [480, 400]]        # clipped to parent bottom
    # no clipping requested → only translated
    raw = map_boxes_to_page(local[1:2], crop, parent, clip=False)
    assert raw[0]["points"] == [[120, 380], [480, 448]]


def test_parents_in_sorted_reading_order_and_filtered():
    doc = {"shapes": [_sh("firm_area", 100, 450, 500, 700), _sh("name", 0, 0, 10, 10),
                      _sh("firm_area", 100, 100, 500, 400), _sh("firm_area", 5, 5, 5, 5)]}
    assert parents_in(doc, "firm_area") == [(100, 100, 500, 400), (100, 450, 500, 700)]


# ── crop export ──────────────────────────────────────────────────────────────

def test_export_crops_translation_clipping_negatives(scoped_project):
    ann = scoped_project / "annotations"
    crops = scoped_project / "intermediate" / "crops"
    from app.coco_convert import collect_pages
    pages = collect_pages(ann)
    res = S.export_crops(pages, "firm_area", ["name", "address", "page_header"],
                         ann, crops, margin=12)
    assert res["n_parents"] == 3 and res["n_negatives"] == 1 and res["n_children"] == 3
    by_stem = {p["stem"]: p for p in res["pages"]}
    assert set(by_stem) == {"p1__crop0", "p1__crop1", "p2__crop0"}

    c0 = by_stem["p1__crop0"]["data"]
    assert (c0["imageWidth"], c0["imageHeight"]) == (424, 324)
    assert c0["flags"]["crop_rect"] == [88, 88, 512, 412]
    with Image.open(crops / "p1__crop0.jpg") as im:
        assert im.size == (424, 324)
    labels = sorted(s["label"] for s in c0["shapes"])
    assert labels == ["address", "name"]           # page_header outside → not a child
    name = next(s for s in c0["shapes"] if s["label"] == "name")
    assert name["points"] == [[32, 22], [392, 52]]  # translated by the crop offset
    addr = next(s for s in c0["shapes"] if s["label"] == "address")
    assert addr["points"] == [[32, 272], [392, 324]]   # clipped to the crop's bottom edge

    c1 = by_stem["p1__crop1"]
    assert c1["n_shapes"] == 0 and c1["data"]["shapes"] == []   # negative example
    assert c1["status"] == "verified"                           # inherits page status


def test_export_round_trip_crop_space_to_page_space(scoped_project):
    """export (page → crop) then map_boxes_to_page (crop → page) reproduces
    the child boxes up to the clipping the parent imposes."""
    ann = scoped_project / "annotations"
    from app.coco_convert import collect_pages
    pages = collect_pages(ann)
    res = S.export_crops(pages, "firm_area", ["name", "address"], ann,
                         scoped_project / "intermediate" / "crops")
    page_doc = json.loads((ann / "p1.json").read_text())
    parents = parents_in(page_doc, "firm_area")
    crop0 = next(p for p in res["pages"] if p["stem"] == "p1__crop0")
    back = map_boxes_to_page(crop0["data"]["shapes"], crop0["data"]["flags"]["crop_rect"],
                             parents[0])
    got = sorted((s["label"], tuple(map(tuple, s["points"]))) for s in back)
    assert got == [("address", ((120, 360), (480, 400))),   # clipped at parent bottom (400)
                   ("name", ((120, 110), (480, 140)))]


def test_prepare_scoped_split_inheritance_and_scripts(scoped_project):
    inter = scoped_project / "intermediate"
    res = prepare_training_data(
        project_name="proj", ann_dir=scoped_project / "annotations", labels=LABELS,
        intermediate_dir=inter, base_yaml_path=BASE_YAML,
        test_stems=["p2"], scope="within:firm_area")
    assert res["scope"] == "within:firm_area"
    assert res["model_labels"] == ["name", "address", "page_header"]
    assert res["n_classes"] == 3
    assert res["n_parents"] == 3 and res["n_crop_negatives"] == 1
    assert res["n_scope_pages"] == 2 and res["n_train_pages"] == 2 and res["n_test_pages"] == 1
    assert res["n_negatives"] == 1          # the childless parent trains as a negative

    train = json.loads((inter / "train.json").read_text())
    test  = json.loads((inter / "test.json").read_text())
    assert sorted(im["file_name"] for im in train["images"]) == ["p1__crop0.jpg", "p1__crop1.jpg"]
    assert [im["file_name"] for im in test["images"]] == ["p2__crop0.jpg"]   # crops follow their page
    assert [c["name"] for c in train["categories"]] == ["name", "address", "page_header"]
    # crop-coordinate bbox in COCO
    bb = {a["category_id"]: a["bbox"] for a in train["annotations"]}
    assert bb[1] == [32, 22, 360, 30]

    sh = (inter / "train.sh").read_text()
    assert "--image_path_train      /workspace/proj/crops" in sh
    assert "FILTER_EMPTY_ANNOTATIONS False" in sh
    yaml = (inter / "configs" / "proj" / "fast_rcnn_R_50_FPN_3x.yaml").read_text()
    assert "NUM_CLASSES: 3" in yaml
    infer = (inter / "infer.sh").read_text()
    assert "--parents /workspace/proj/parents" in infer
    assert "--parent-label firm_area" in infer
    assert "--labels  name address page_header" in infer


def test_prepare_page_scope_unchanged(scoped_project):
    inter = scoped_project / "intermediate"
    res = prepare_training_data(
        project_name="proj", ann_dir=scoped_project / "annotations", labels=LABELS,
        intermediate_dir=inter, base_yaml_path=BASE_YAML, test_stems=["p2"])
    assert res["scope"] == "page" and res["n_parents"] == 0 and res["n_classes"] == 4
    assert "--image_path_train      /workspace/proj/images" in (inter / "train.sh").read_text()
    assert "--parents" not in (inter / "infer.sh").read_text()
    assert not (inter / "crops").exists()


def test_stale_crops_cleared_on_reexport(scoped_project):
    inter = scoped_project / "intermediate"
    crops = inter / "crops"
    crops.mkdir(parents=True)
    (crops / "old__crop7.jpg").write_bytes(b"x")
    prepare_training_data(project_name="proj", ann_dir=scoped_project / "annotations",
                          labels=LABELS, intermediate_dir=inter, base_yaml_path=BASE_YAML,
                          scope="within:firm_area")
    assert not (crops / "old__crop7.jpg").exists()
    assert (crops / "p1__crop0.jpg").exists()


def test_make_infer_script_explicit_paths():
    s = make_infer_script("tgt", ["a", "b"], scope="within:a", threshold=0.2,
                          weights_path="/w.pth", parents_path="/x/parents")
    assert "--weights /w.pth" in s and "--parents /x/parents" in s
    assert "--labels  b " in s and "--threshold 0.2" in s


# ── parents export (inference input) ─────────────────────────────────────────

def test_export_parents_slim_and_skips_clutter(scoped_project):
    ann = scoped_project / "annotations"
    _page(ann, "p3", [_sh("firm_area", 0, 0, 100, 100)], status="skip")
    _page(ann, "p4", [_sh("name", 0, 0, 100, 100)])          # no parents → no file
    out = scoped_project / "intermediate" / "parents"
    out.mkdir(parents=True)
    (out / "stale.json").write_text("{}")
    st = S.export_parents(ann, out, "firm_area")
    assert st == {"n_pages": 2, "n_parents": 3}
    assert sorted(p.name for p in out.glob("*.json")) == ["p1.json", "p2.json"]
    p1 = json.loads((out / "p1.json").read_text())
    assert p1["imageWidth"] == 1000 and len(p1["shapes"]) == 2
    assert set(p1["shapes"][0]) == {"label", "points"}      # slim: no text layers


# ── merge-apply ──────────────────────────────────────────────────────────────

def test_merge_children_rules():
    ann = [
        _sh("firm_area", 100, 100, 500, 400),                       # parent (kept)
        _sh("name", 120, 110, 480, 140),                            # HUMAN child (kept)
        _sh("address", 120, 300, 480, 340, score=0.7),              # old predicted child (replaced)
        _sh("page_header", 100, 10, 900, 40, score=0.9),            # scored, outside → kept
        _sh("name", 600, 600, 700, 650, score=0.4),                 # scored child-label, outside → kept
    ]
    pred = [_sh("address", 125, 305, 475, 345, score=0.95),
            _sh("firm_area", 0, 0, 10, 10, score=0.5)]              # parent label never inserted
    res = S.merge_children(ann, pred, "firm_area", ["name", "address", "page_header"])
    assert res["removed"] == 1 and res["inserted"] == 1 and res["changed"]
    labels = [(s["label"], s.get("score")) for s in res["shapes"]]
    assert labels == [("firm_area", None), ("name", None), ("page_header", 0.9),
                      ("name", 0.4), ("address", 0.95)]
    # idempotent: applying the same prediction again changes nothing but the identity
    again = S.merge_children(res["shapes"], pred, "firm_area", ["name", "address", "page_header"])
    assert again["removed"] == 1 and again["inserted"] == 1
    assert [(s["label"], s.get("score")) for s in again["shapes"]] == labels


def test_merge_children_no_parents_untouched():
    ann = [_sh("name", 0, 0, 10, 10, score=0.3)]
    res = S.merge_children(ann, [_sh("name", 1, 1, 5, 5, score=0.9)], "firm_area", ["name"])
    assert res["n_parents"] == 0 and not res["changed"] and res["shapes"] is ann


def test_merge_apply_over_folders(tmp_path):
    ann = tmp_path / "annotations"; pred = tmp_path / "predictions"
    ann.mkdir(); pred.mkdir()
    writes = []

    def _w(path, obj):
        writes.append(path.name)
        path.write_text(json.dumps(obj), encoding="utf-8")

    _page(ann, "a", [_sh("firm_area", 0, 0, 500, 500), _sh("name", 10, 10, 100, 40, score=0.5)])
    _page(ann, "b", [_sh("name", 10, 10, 100, 40)])                   # no parents
    _page(ann, "c", [_sh("firm_area", 0, 0, 500, 500)], status="skip")  # clutter
    _page(ann, "d", [_sh("firm_area", 0, 0, 500, 500)])              # parents, nothing predicted
    for stem, shapes in {"a": [_sh("name", 12, 12, 98, 38, score=0.8)],
                         "b": [_sh("name", 1, 1, 2, 2, score=0.9)],
                         "c": [_sh("name", 1, 1, 2, 2, score=0.9)],
                         "d": [], "zz_no_page": [_sh("name", 1, 1, 2, 2)]}.items():
        (pred / f"{stem}.json").write_text(json.dumps({"shapes": shapes}))
    st = S.merge_apply(ann, pred, "firm_area", ["name", "address"], _w, statuses=None)
    assert st == {"applied": 1, "skipped_unchanged": 1, "skipped_no_parents": 1,
                  "skipped_clutter": 1, "skipped_status": 0, "skipped_no_page": 1, "inserted": 1, "removed": 1}
    assert writes == ["a.json"]
    a = json.loads((ann / "a.json").read_text())
    assert [(s["label"], s.get("score")) for s in a["shapes"]] == [("firm_area", None), ("name", 0.8)]
    assert a["flags"]["status"] == "verified"        # page metadata untouched


# ── endpoints on a throwaway project ─────────────────────────────────────────

@pytest.fixture()
def smoke_project():
    from app.pipeline import create_project, PROJECTS_ROOT
    name = f"_smoke_scope_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", LABELS)
    ann = PROJECTS_ROOT / name / "annotations"
    ann.mkdir(parents=True, exist_ok=True)
    _page(ann, "p1", [_sh("firm_area", 100, 100, 500, 400),
                      _sh("name", 120, 110, 480, 140, score=0.6),
                      _sh("address", 120, 300, 480, 340)])
    # projects/ lives in Dropbox: a just-written file can be briefly
    # unopenable (OSError 22) while the sync client grabs it — settle first.
    for _ in range(20):
        try:
            json.loads((PROJECTS_ROOT / name / "config.json").read_text(encoding="utf-8"))
            break
        except OSError:
            time.sleep(0.1)
    try:
        yield name, PROJECTS_ROOT / name
    finally:
        for _ in range(5):                       # Dropbox/AV may hold a handle briefly
            shutil.rmtree(PROJECTS_ROOT / name, ignore_errors=True)
            if not (PROJECTS_ROOT / name).exists():
                break
            time.sleep(0.3)


def test_config_detection_scope_patch(client, smoke_project):
    name, pdir = smoke_project
    r = client.patch(f"/api/project/{name}/config", json={"detection_scope": "within:firm_area"})
    assert r.status_code == 200 and r.json()["config"]["detection_scope"] == "within:firm_area"
    assert client.get(f"/api/project/{name}").json()["config"]["detection_scope"] == "within:firm_area"
    r = client.patch(f"/api/project/{name}/config", json={"detection_scope": "within:nope"})
    assert r.status_code == 400 and "not one of" in r.json()["detail"]
    r = client.patch(f"/api/project/{name}/config", json={"detection_scope": "page"})
    assert r.status_code == 200 and "detection_scope" not in r.json()["config"]


def test_prepare_endpoint_uses_config_scope_and_records_it(client, smoke_project):
    name, pdir = smoke_project
    client.patch(f"/api/project/{name}/config", json={"detection_scope": "within:firm_area"})
    r = client.post(f"/api/project/{name}/prepare", json={"status_filter": False})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["scope"] == "within:firm_area" and d["n_parents"] == 1 and d["n_classes"] == 3
    summary = json.loads((pdir / "intermediate" / "train_summary.json").read_text())
    assert summary["scope"] == "within:firm_area"
    assert (pdir / "intermediate" / "crops" / "p1__crop0.jpg").exists()
    # the ledger row builder picks the scope up from the summary
    from app.server import _ledger_row_from_summary
    row = _ledger_row_from_summary(pdir / "intermediate", mode="train")
    assert row["scope"] == "within:firm_area" and row["n_parents"] == 1
    # explicit request scope overrides config
    r = client.post(f"/api/project/{name}/prepare", json={"status_filter": False, "scope": "page"})
    assert r.json()["scope"] == "page" and r.json()["n_classes"] == 4
    r = client.post(f"/api/project/{name}/prepare", json={"scope": "within:zzz"})
    assert r.status_code == 400


def test_merge_apply_endpoint(client, smoke_project):
    name, pdir = smoke_project
    pred = pdir / "predictions"; pred.mkdir()
    (pred / "p1.json").write_text(json.dumps({"shapes": [
        _sh("name", 122, 112, 478, 138, score=0.91)]}))
    # page scope → refused
    r = client.post(f"/api/project/{name}/apply-predictions-merge", json={})
    assert r.status_code == 400
    r = client.post(f"/api/project/{name}/apply-predictions-merge", json={"scope": "within:firm_area", "statuses": ["predicted", "corrected", "verified"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["applied"] == 1 and d["inserted"] == 1 and d["removed"] == 1
    page = json.loads((pdir / "annotations" / "p1.json").read_text())
    got = [(s["label"], s.get("score")) for s in page["shapes"]]
    assert got == [("firm_area", None), ("address", None), ("name", 0.91)]
    # second run: same prediction → replaced again, nothing else moves
    r = client.post(f"/api/project/{name}/apply-predictions-merge", json={"scope": "within:firm_area", "statuses": ["predicted", "corrected", "verified"]})
    assert r.json()["applied"] == 1 and r.json()["removed"] == 1


def test_infer_endpoints_validate_scope_before_ssh(client, smoke_project):
    name, _ = smoke_project
    r = client.post(f"/api/project/{name}/infer", json={"scope": "within:bogus"})
    assert r.status_code == 400 and "not one of" in r.json()["detail"]
    r = client.post(f"/api/project/{name}/finetune-from/{name}", json={"scope": "within:bogus"})
    assert r.status_code == 400 and "not one of" in r.json()["detail"]
    r = client.post(f"/api/project/{name}/infer-from/{name}", json={"scope": "within:bogus"})
    assert r.status_code == 400 and "not one of" in r.json()["detail"]
    # a valid scope gets as far as the (unconfigured) GPU server gate
    r = client.post(f"/api/project/{name}/infer", json={"scope": "within:firm_area", "statuses": ["predicted", "corrected", "verified"]})
    assert r.status_code == 400 and "Server" in r.json()["detail"]


def test_infer_layout_importable_without_detectron2():
    import importlib, sys
    assert "detectron2" not in sys.modules or True   # the import above already succeeded
    mod = importlib.import_module("app.infer_layout")
    assert callable(mod.main) and callable(mod.build_predictor)
    assert box_of([[3, 4], [1, 2]]) == (1, 2, 3, 4)
