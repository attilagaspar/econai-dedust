"""Server-side background batch OCR: job lifecycle, per-page selection,
overwrite-off skipping, one write per page, status/stop endpoints."""
import json
import time

import pytest


@pytest.fixture()
def ocr_folder(tmp_path):
    from PIL import Image
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)
    for stem in ("b1", "b2"):
        shapes = [
            {"label": "cell", "points": [[10, 10], [60, 40]],
             "shape_type": "rectangle", "flags": {}},
            {"label": "cell", "points": [[10, 50], [60, 80]],
             "shape_type": "rectangle", "flags": {},
             "tesseract_output": {"ocr_text": "already done"}},
            {"label": "other", "points": [[10, 90], [60, 120]],
             "shape_type": "rectangle", "flags": {}},
        ]
        (proj / f"{stem}.json").write_text(json.dumps(
            {"shapes": shapes, "imagePath": f"{stem}.jpg",
             "imageWidth": 100, "imageHeight": 150}), encoding="utf-8")
        Image.new("RGB", (100, 150), "white").save(proj / f"{stem}.jpg")
    return proj


def _wait_done(client, folder, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get("/api/batch/ocr/status",
                       params={"folder": folder}).json()["job"]
        if j and j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def test_job_selects_skips_and_writes_per_page(client, ocr_folder, monkeypatch):
    import app.server as srv
    calls = []
    monkeypatch.setattr(srv, "_ocr_shape_whole",
                        lambda engine, crop, lang, langs:
                        (calls.append(1) or ("FAKE", 99.0, {"lang": lang})))
    r = client.post("/api/batch/ocr/start", params={"folder": str(ocr_folder)},
                    json={"stems": ["b1", "b2", "ghost"], "labels": ["cell"],
                          "engine": "tesseract", "scope": "whole",
                          "overwrite": False})
    assert r.status_code == 200
    j = _wait_done(client, str(ocr_folder))
    assert j["state"] == "done"
    # per page: 2 'cell' shapes, one already OCRed → 1 call each; 'other' and
    # the missing stem never touched
    assert j["cells_done"] == 2 and len(calls) == 2
    assert j["stems_done"] == 3 and j["cells_err"] == 0
    for stem in ("b1", "b2"):
        data = json.loads((ocr_folder / f"{stem}.json").read_text())
        assert data["shapes"][0]["tesseract_output"]["ocr_text"] == "FAKE"
        assert data["shapes"][1]["tesseract_output"]["ocr_text"] == "already done"
        assert "tesseract_output" not in data["shapes"][2]


def test_job_conflict_and_stop(client, ocr_folder, monkeypatch):
    import app.server as srv

    def slow_ocr(engine, crop, lang, langs):
        time.sleep(0.4)
        return "SLOW", 1.0, {}
    monkeypatch.setattr(srv, "_ocr_shape_whole", slow_ocr)
    r = client.post("/api/batch/ocr/start", params={"folder": str(ocr_folder)},
                    json={"stems": ["b1", "b2"], "labels": ["cell"],
                          "engine": "tesseract", "scope": "whole",
                          "overwrite": True})
    assert r.status_code == 200
    # second start while running → 409
    r2 = client.post("/api/batch/ocr/start", params={"folder": str(ocr_folder)},
                     json={"stems": ["b1"], "labels": ["cell"],
                           "engine": "tesseract", "scope": "whole"})
    assert r2.status_code == 409
    # stop it
    rs = client.post("/api/batch/ocr/stop", params={"folder": str(ocr_folder)})
    assert rs.json()["ok"] is True
    j = _wait_done(client, str(ocr_folder))
    assert j["state"] == "stopped"
    assert j["cells_done"] < 4          # halted before finishing everything


def test_start_validation(client, ocr_folder):
    r = client.post("/api/batch/ocr/start", params={"folder": str(ocr_folder)},
                    json={"stems": [], "labels": ["cell"]})
    assert r.status_code == 400
    r = client.post("/api/batch/ocr/start", params={"folder": str(ocr_folder)},
                    json={"stems": ["b1"], "labels": ["cell"], "engine": "banana"})
    assert r.status_code == 400
