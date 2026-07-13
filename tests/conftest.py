"""Shared fixtures: a TestClient over the real app and a synthetic
annotation folder with one 'lattice page' (title + 2x2 grid + footer)."""
import json
import os
import sys
from pathlib import Path

import pytest

# The suite assumes the remote guard is INERT. On machines where ECONAI_TOKEN
# is a persistent env var (any deployment box), inherited env would arm the
# guard and 401 every TestClient request — strip it here. The guard tests
# monkeypatch.setenv it explicitly, so they are unaffected.
os.environ.pop("ECONAI_TOKEN", None)

# Repo root importable regardless of where pytest is invoked from
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient          # noqa: E402
from app.server import app                          # noqa: E402


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


def _shape(label, x1, y1, x2, y2, text=None, super_row=None, super_col=None,
           **extra):
    sh = {
        "label": label,
        "points": [[x1, y1], [x2, y2]],
        "group_id": None,
        "shape_type": "rectangle",
        "flags": {},
    }
    if text is not None:
        sh["human_output"] = {"human_corrected_text": text}
    if super_row is not None:
        sh["super_row"] = super_row
        sh["super_column"] = super_col
        sh["table"] = 0
    sh.update(extra)
    return sh


@pytest.fixture()
def page_folder(tmp_path):
    """Folder with page 'p1': a free title, a 2x2 lattice, a free footer.

    Nested as <project>/annotations like real projects — several features
    (schemas, batch undo, overnight jobs) write to the folder's PARENT, and a
    flat tmp_path would share that parent across tests."""
    tmp_path = tmp_path / "proj" / "annotations"
    tmp_path.mkdir(parents=True)
    shapes = [
        _shape("header", 100, 10, 500, 40, text="TITLE ROW"),
        _shape("cell", 100, 100, 300, 200, text="A1", super_row=1, super_col=1),
        _shape("cell", 300, 100, 500, 200, text="B1", super_row=1, super_col=2),
        _shape("cell", 100, 200, 300, 300, text="A2", super_row=2, super_col=1),
        _shape("cell", 300, 200, 500, 300, text="B2", super_row=2, super_col=2),
        _shape("header", 100, 320, 500, 350, text="FOOTER ROW"),
    ]
    doc = {"version": "5.0.1", "flags": {}, "shapes": shapes,
           "imagePath": "p1.jpg", "imageHeight": 400, "imageWidth": 600}
    (tmp_path / "p1.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    from PIL import Image
    Image.new("RGB", (600, 400), "white").save(tmp_path / "p1.jpg")
    return tmp_path
