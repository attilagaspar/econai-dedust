"""Perspective correction: the 4 points (any order) become the image corners;
shapes are remapped through the same homography on save."""
import json

import pytest

cv2 = pytest.importorskip("cv2")


@pytest.fixture()
def persp_folder(tmp_path):
    from PIL import Image
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)
    img = Image.new("RGB", (200, 100), "black")
    for dx in range(-2, 3):                        # 5x5 marker inside the quad
        for dy in range(-2, 3):                    # (1px would smear away in
            img.putpixel((25 + dx, 15 + dy), (255, 0, 0))   # JPEG + resample)
    img.save(proj / "q1.jpg", quality=100, subsampling=0)
    (proj / "q1.json").write_text(json.dumps({
        "shapes": [{"label": "cell", "points": [[20, 10], [70, 35]],
                    "shape_type": "rectangle", "flags": {}}],
        "imagePath": "q1.jpg", "imageWidth": 200, "imageHeight": 100,
    }), encoding="utf-8")
    return proj


# an axis-aligned rectangle as the quad → the warp must be an exact crop
QUAD_SHUFFLED = [[120, 10], [20, 60], [20, 10], [120, 60]]   # any order allowed


def test_preview_size_is_exactly_the_quad(client, persp_folder):
    r = client.post("/api/page/perspective", json={
        "folder": str(persp_folder), "stem": "q1",
        "points": QUAD_SHUFFLED, "save": False})
    assert r.status_code == 200
    d = r.json()
    assert (d["width"], d["height"]) == (100, 50)   # quad size, no canvas expansion
    assert d["preview"]


def test_save_crops_image_and_remaps_shapes(client, persp_folder):
    from PIL import Image
    r = client.post("/api/page/perspective", json={
        "folder": str(persp_folder), "stem": "q1",
        "points": QUAD_SHUFFLED, "save": True})
    assert r.status_code == 200
    d = r.json()
    assert (d["width"], d["height"]) == (100, 50)
    assert d["shapes_transformed"] == 1

    img = Image.open(persp_folder / "q1.jpg")
    assert img.size == (100, 50)
    # the marker at (25,15) must now sit at ~(5,5)
    px = img.getpixel((5, 5))
    assert px[0] > 150 and px[1] < 100 and px[2] < 100

    data = json.loads((persp_folder / "q1.json").read_text())
    assert (data["imageWidth"], data["imageHeight"]) == (100, 50)
    (x1, y1), (x2, y2) = data["shapes"][0]["points"]
    # shape (20,10)-(70,35) maps to (0,0)-(50,25) under the crop
    assert abs(x1) < 1 and abs(y1) < 1 and abs(x2 - 50) < 1 and abs(y2 - 25) < 1


def test_margin_expands_the_kept_area(client, persp_folder):
    import math
    r = client.post("/api/page/perspective", json={
        "folder": str(persp_folder), "stem": "q1",
        "points": QUAD_SHUFFLED, "save": False, "margin": 10})
    assert r.status_code == 200
    d = r.json()
    # radial push by m on a w×h rectangle scales it by (1 + m/half-diagonal)
    f = 1 + 10 / math.hypot(50, 25)
    assert abs(d["width"] - round(100 * f)) <= 1
    assert abs(d["height"] - round(50 * f)) <= 1


def test_degenerate_points_rejected(client, persp_folder):
    r = client.post("/api/page/perspective", json={
        "folder": str(persp_folder), "stem": "q1",
        "points": [[10, 10], [10, 10], [50, 50], [50, 50]], "save": False})
    assert r.status_code == 400
