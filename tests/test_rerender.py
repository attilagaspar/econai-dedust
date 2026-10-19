"""Re-render pages (app/rerender.py, POST /api/project/{name}/rerender).

Pure: scale_shape / scale_doc (points, absolute row bands, raw box, mask,
derived_from.bbox; crop_source untouched), target normalisation. Project
render settings: config PATCH validation, imports honouring render_scale /
render_longest_px / native. Endpoint on a throwaway `_smoke_` project built
from a tiny generated PDF (a raster "scan" embedded at 3× the page size):
dry run writes nothing and reports counts + factor + refused pages; apply
re-renders at the new scale with every coordinate (shapes, row bands,
fragment markers, predictions, records registry) multiplied by the exact
factor, pdf_scale + provenance note written, a backup zip present; a re-run
at the same scale is a no-op; perspective-altered pages are refused; pages
without a PDF source are reported; stems unchanged.
"""
import io
import json
import shutil
import time
import uuid
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import rerender as R
from app.page_import import _import_pdf, normalize_render_settings
from app.pipeline import create_project, PROJECTS_ROOT


# ── helpers ──────────────────────────────────────────────────────────────────

def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _rm(path):
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.3)


def _scan_png(w=600, h=900) -> bytes:
    """A 'scanned page': white with a black bar near the top-left."""
    im = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(im).rectangle([60, 90, 300, 150], fill="black")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _make_pdf(path: Path, n_pages=2, pt_w=200, pt_h=300):
    """n pages of pt_w × pt_h points, each showing a 600×900 px raster →
    native scale 3.0; scale 2.0 renders 400×600."""
    import fitz
    doc = fitz.open()
    png = _scan_png()
    for _ in range(n_pages):
        page = doc.new_page(width=pt_w, height=pt_h)
        page.insert_image(page.rect, stream=png)
    doc.save(str(path))
    doc.close()


def _sh(label, x1, y1, x2, y2, **extra):
    d = {"label": label, "points": [[x1, y1], [x2, y2]], "group_id": None,
         "shape_type": "rectangle", "flags": {}}
    d.update(extra)
    return d


@pytest.fixture()
def proj():
    """_smoke_ project with a 2-page PDF imported at scale 2.0 (400×600 px
    pages), annotations with every coordinate kind, a prediction file, a
    records registry and one extra image page without a PDF source."""
    name = f"_smoke_rerender_{uuid.uuid4().hex[:6]}"
    create_project(name, "B", ["entry", "name", "cell"])
    pdir = PROJECTS_ROOT / name
    ann = pdir / "annotations"
    src = pdir / "sources"
    src.mkdir()
    pdf = src / "vol.pdf"
    _make_pdf(pdf)
    pages = _import_pdf(pdf, ann, render=normalize_render_settings(2.0))
    assert [p["stem"] for p in pages] == ["vol_1", "vol_2"]
    assert (pages[0]["width"], pages[0]["height"]) == (400, 600)
    # annotations on vol_1
    d = _read(ann / "vol_1.json")
    d["flags"]["status"] = "verified"
    d["shapes"] = [
        _sh("entry", 40, 60, 200, 100, group_id=7,
            row_struct={"version": 1, "origin": "manual",
                        "rows": [{"n": 1, "y0": 60.0, "y1": 80.0, "human": "a"},
                                 {"n": 2, "y0": 80.0, "y1": 100.0, "human": "b"}]},
            derived_from={"kind": "record_roles", "bbox": [40, 60, 200, 100],
                          "continuation": False, "start_label": "name",
                          "start_idx": 1, "column": 0},
            raw=[[40, 60], [200, 100]]),
        _sh("name", 40, 60, 120, 72, human_output={"human_corrected_text": "Firm"}),
        _sh("cell", 10, 200, 100, 250, super_row=1, super_column=1, table=0,
            authority={"id": "X1", "name": "Place"}, structured={"data": {"k": 1}},
            clip={"id": 3}),
    ]
    (ann / "vol_1.json").write_text(json.dumps(d), encoding="utf-8")
    # a prediction file for vol_2
    (pdir / "predictions").mkdir()
    (pdir / "predictions" / "vol_2.json").write_text(json.dumps({
        "version": "5.0.1", "flags": {}, "imagePath": "vol_2.jpg",
        "imageWidth": 400, "imageHeight": 600,
        "shapes": [_sh("entry", 20, 30, 220, 330, score=0.9)]}), encoding="utf-8")
    # records registry
    (pdir / "intermediate").mkdir(exist_ok=True)
    (pdir / "intermediate" / "records.json").write_text(json.dumps({
        "meta": {}, "records": {"7": {"fragments": [
            {"stem": "vol_1", "idx": 0, "bbox": [40, 60, 200, 100]}],
            "page_span": 1, "first_stem": "vol_1", "n_pages": 1}}}), encoding="utf-8")
    # an image page without a PDF source
    Image.new("RGB", (300, 300), "white").save(ann / "img_1.jpg")
    (ann / "img_1.json").write_text(json.dumps({
        "version": "5.0.1", "flags": {}, "shapes": [_sh("cell", 1, 1, 10, 10)],
        "imagePath": "img_1.jpg", "imageWidth": 300, "imageHeight": 300}), encoding="utf-8")
    try:
        yield name, pdir
    finally:
        _rm(pdir)


def _wait_job(client, name, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = client.get(f"/api/project/{name}/rerender/status").json()["job"]
        if job and job["done"]:
            return job
        time.sleep(0.1)
    raise AssertionError("re-render job did not finish")


# ── pure: scaling ────────────────────────────────────────────────────────────

def test_scale_shape_moves_every_page_frame_field_and_nothing_else():
    sh = _sh("entry", 10, 20, 30, 40,
             row_struct={"rows": [{"y0": 20, "y1": 30, "ocr": "x", "human": "y"}]},
             raw=[[10, 20], [30, 40]], mask=[[[1, 1], [2, 2], [3, 1]]],
             derived_from={"kind": "record_roles", "bbox": [10, 20, 30, 40], "column": 1},
             super_row=2, super_column=3, authority={"id": "A"}, clip={"id": 1},
             crop_origin={"project": "p", "stem": "s"})
    out = R.scale_shape(sh, 2.0, 3.0)
    assert out["points"] == [[20.0, 60.0], [60.0, 120.0]]
    assert out["row_struct"]["rows"][0]["y0"] == 60.0 and out["row_struct"]["rows"][0]["y1"] == 90.0
    assert out["row_struct"]["rows"][0]["ocr"] == "x"
    assert out["raw"] == [[20.0, 60.0], [60.0, 120.0]]
    assert out["mask"] == [[[2.0, 3.0], [4.0, 6.0], [6.0, 3.0]]]
    assert out["derived_from"]["bbox"] == [20.0, 60.0, 60.0, 120.0]
    assert out["derived_from"]["column"] == 1
    for k in ("super_row", "super_column", "authority", "clip", "crop_origin", "label"):
        assert out[k] == sh[k]
    assert sh["points"] == [[10, 20], [30, 40]]          # input untouched


def test_scale_doc_replaces_dims_and_leaves_crop_source_alone():
    doc = {"imageWidth": 100, "imageHeight": 200, "pdf_scale": 2.0,
           "flags": {"status": "verified", "crop_source": {"bbox": [1, 2, 3, 4], "crop_rect": [0, 0, 9, 9]}},
           "shapes": [_sh("a", 1, 2, 3, 4), {"label": "weird", "points": "nope"}]}
    out = R.scale_doc(doc, 2.0, 2.0, 200, 400)
    assert (out["imageWidth"], out["imageHeight"]) == (200, 400)
    assert out["flags"]["crop_source"] == doc["flags"]["crop_source"]
    assert out["shapes"][0]["points"] == [[2.0, 4.0], [6.0, 8.0]]
    assert out["shapes"][1]["points"] == "nope"


def test_target_settings_and_validation():
    assert R.target_settings(3.0) == {"mode": "scale", "scale": 3.0, "longest_px": None}
    assert R.target_settings(None, 2500)["mode"] == "longest"
    assert R.target_settings(mode="native")["mode"] == "native"
    assert normalize_render_settings() == {"mode": "scale", "scale": 2.0, "longest_px": None}
    with pytest.raises(ValueError):
        normalize_render_settings(50)
    with pytest.raises(ValueError):
        normalize_render_settings("huge")
    with pytest.raises(ValueError):
        normalize_render_settings(None, 10)


# ── import honours the project setting ───────────────────────────────────────

def test_import_scale_modes(tmp_path):
    pdf = tmp_path / "v.pdf"
    _make_pdf(pdf, n_pages=1)
    a = tmp_path / "a"; a.mkdir()
    p = _import_pdf(pdf, a, render=normalize_render_settings(3.0))[0]
    assert (p["width"], p["height"]) == (600, 900)
    assert _read(a / "v_1.json")["pdf_scale"] == 3.0
    b = tmp_path / "b"; b.mkdir()
    p = _import_pdf(pdf, b, render=normalize_render_settings(None, 1500))[0]
    assert p["height"] == 1500 and p["width"] == 1000
    c = tmp_path / "c"; c.mkdir()
    p = _import_pdf(pdf, c, render=normalize_render_settings("native"))[0]
    assert (p["width"], p["height"]) == (600, 900)       # the embedded 600×900 scan
    # from the project's config.json (annotations/ → parent)
    proj = tmp_path / "proj"; (proj / "annotations").mkdir(parents=True)
    (proj / "config.json").write_text(json.dumps({"render_scale": 4}), encoding="utf-8")
    p = _import_pdf(pdf, proj / "annotations")[0]
    assert (p["width"], p["height"]) == (800, 1200)
    # no config → default 2.0
    d = tmp_path / "d"; d.mkdir()
    assert _import_pdf(pdf, d)[0]["width"] == 400


def test_config_patch_render_scale(client, proj):
    name, pdir = proj
    r = client.patch(f"/api/project/{name}/config", json={"render_scale": "native"})
    assert r.status_code == 200 and r.json()["config"]["render_scale"] == "native"
    r = client.patch(f"/api/project/{name}/config", json={"render_longest_px": 3000})
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["render_longest_px"] == 3000 and "render_scale" not in cfg
    r = client.patch(f"/api/project/{name}/config", json={"render_scale": 2, "render_longest_px": ""})
    cfg = r.json()["config"]
    assert "render_scale" not in cfg and "render_longest_px" not in cfg   # default clears
    r = client.patch(f"/api/project/{name}/config", json={"render_scale": 99})
    assert r.status_code == 400


# ── endpoint ─────────────────────────────────────────────────────────────────

def test_dry_run_reports_and_writes_nothing(client, proj):
    name, pdir = proj
    ann = pdir / "annotations"
    before = {p.name: p.read_bytes() for p in ann.iterdir()}
    r = client.post(f"/api/project/{name}/rerender", json={"scale": 3.0, "dry_run": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["dry_run"] is True
    assert d["counts"] == {"to_render": 2, "up_to_date": 0, "no_source": 1, "missing_pdf": 0,
                           "no_image": 0, "altered": 0, "errors": 0}
    assert d["no_source"] == ["img_1"]
    assert d["factor"] == {"min": 1.5, "max": 1.5}
    pg = {p["stem"]: p for p in d["pages"]}
    assert (pg["vol_1"]["new_w"], pg["vol_1"]["new_h"]) == (600, 900)
    assert {p.name: p.read_bytes() for p in ann.iterdir()} == before
    assert not (pdir / "backups").exists()


def test_apply_scales_everything_backs_up_and_is_idempotent(client, proj):
    name, pdir = proj
    ann = pdir / "annotations"
    r = client.post(f"/api/project/{name}/rerender", json={"scale": 3.0, "dry_run": False})
    assert r.status_code == 200, r.text
    assert r.json()["job"] is not None
    job = _wait_job(client, name)
    assert job["phase"] == "done", job
    res = job["result"]
    assert sorted(res["rendered"]) == ["vol_1", "vol_2"] and res["failed"] == []
    # image + dims + scale + note
    assert Image.open(ann / "vol_1.jpg").size == (600, 900)
    d = _read(ann / "vol_1.json")
    assert (d["imageWidth"], d["imageHeight"]) == (600, 900)
    assert d["pdf_scale"] == 3.0
    assert d["rerender"]["from_scale"] == 2.0 and d["rerender"]["factor"] == [1.5, 1.5]
    assert d["flags"]["status"] == "verified"
    # shapes: points, row bands, marker, raw; content and ids untouched
    e, n, c = d["shapes"]
    assert e["points"] == [[60.0, 90.0], [300.0, 150.0]]
    assert [(r_["y0"], r_["y1"]) for r_ in e["row_struct"]["rows"]] == [(90.0, 120.0), (120.0, 150.0)]
    assert e["row_struct"]["rows"][0]["human"] == "a"
    assert e["derived_from"]["bbox"] == [60.0, 90.0, 300.0, 150.0]
    assert e["raw"] == [[60.0, 90.0], [300.0, 150.0]]
    assert e["group_id"] == 7
    assert n["human_output"]["human_corrected_text"] == "Firm"
    assert c["points"] == [[15.0, 300.0], [150.0, 375.0]]
    assert c["super_row"] == 1 and c["authority"]["id"] == "X1" and c["clip"] == {"id": 3}
    # the rendered image really is the sharper scan: the black bar now sits at
    # the scaled box (60..300 × 90..150) — the scaled annotation still covers it
    im = Image.open(ann / "vol_1.jpg").convert("L")
    assert im.getpixel((180, 120)) < 60 and im.getpixel((180, 200)) > 200
    # predictions + registry
    pd = _read(pdir / "predictions" / "vol_2.json")
    assert pd["shapes"][0]["points"] == [[30.0, 45.0], [330.0, 495.0]]
    assert (pd["imageWidth"], pd["imageHeight"]) == (600, 900)
    reg = _read(pdir / "intermediate" / "records.json")
    assert reg["records"]["7"]["fragments"][0]["bbox"] == [60.0, 90.0, 300.0, 150.0]
    # the image page is untouched
    assert _read(ann / "img_1.json")["shapes"][0]["points"] == [[1, 1], [10, 10]]
    # backup zip holds the old files
    assert res["backup"].startswith("backups/rerender_")
    with zipfile.ZipFile(pdir / res["backup"]) as zf:
        names = set(zf.namelist())
        assert {"annotations/vol_1.json", "annotations/vol_1.jpg", "annotations/vol_2.json",
                "predictions/vol_2.json", "intermediate/records.json"} <= names
        old = json.loads(zf.read("annotations/vol_1.json"))
        assert old["imageWidth"] == 400 and old["shapes"][0]["points"] == [[40, 60], [200, 100]]
    # same scale again → no-op
    r = client.post(f"/api/project/{name}/rerender", json={"scale": 3.0, "dry_run": True})
    assert r.json()["counts"]["to_render"] == 0 and r.json()["counts"]["up_to_date"] == 2
    r = client.post(f"/api/project/{name}/rerender", json={"scale": 3.0, "dry_run": False})
    assert r.json()["job"] is None
    # stems unchanged
    assert sorted(p.stem for p in ann.glob("*.json")) == ["img_1", "vol_1", "vol_2"]
    # and back down to 2.0 reproduces the original geometry exactly
    client.post(f"/api/project/{name}/rerender", json={"scale": 2.0, "dry_run": False, "backup": False})
    _wait_job(client, name)
    d = _read(ann / "vol_1.json")
    assert d["shapes"][0]["points"] == [[40.0, 60.0], [200.0, 100.0]] and d["imageWidth"] == 400


def test_project_setting_is_the_default_target_and_native_mode(client, proj):
    name, pdir = proj
    client.patch(f"/api/project/{name}/config", json={"render_scale": "native"})
    r = client.post(f"/api/project/{name}/rerender", json={"dry_run": True})
    d = r.json()
    assert d["target"].startswith("native") and d["scales"] == [3.0]
    assert d["counts"]["to_render"] == 2
    r = client.post(f"/api/project/{name}/rerender", json={"longest_px": 1200, "dry_run": True})
    assert r.json()["scales"] == [4.0] and r.json()["pages"][0]["new_h"] == 1200


def test_altered_and_missing_pdf_pages_are_refused(client, proj):
    name, pdir = proj
    ann = pdir / "annotations"
    # vol_1 "perspective-corrected": dims no longer match a render at pdf_scale
    d = _read(ann / "vol_1.json")
    d["imageWidth"], d["imageHeight"] = 380, 590
    (ann / "vol_1.json").write_text(json.dumps(d), encoding="utf-8")
    # vol_2 points at a PDF that is gone
    d2 = _read(ann / "vol_2.json")
    d2["pdf_source"] = "sources/gone.pdf"
    (ann / "vol_2.json").write_text(json.dumps(d2), encoding="utf-8")
    r = client.post(f"/api/project/{name}/rerender", json={"scale": 3.0, "dry_run": True})
    d = r.json()
    assert d["counts"]["to_render"] == 0
    assert d["altered"] == [{"stem": "vol_1", "expected": [400, 600], "stored": [380, 590]}]
    assert d["missing_pdf"] == [{"stem": "vol_2", "pdf": "gone.pdf"}]
    # a subset request only looks at the named stems
    r = client.post(f"/api/project/{name}/rerender", json={"scale": 3.0, "dry_run": True,
                                                            "stems": ["img_1"]})
    assert r.json()["counts"] == {"to_render": 0, "up_to_date": 0, "no_source": 1, "missing_pdf": 0,
                                  "no_image": 0, "altered": 0, "errors": 0}


def test_unknown_project_and_bad_target(client):
    assert client.post("/api/project/_smoke_nope_xyz/rerender", json={"scale": 3}).status_code == 404
