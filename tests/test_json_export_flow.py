"""JSON export on newsflow pages: propagation follows flow_order, not raw
y-position — a column-2 title must never leak into column-1 records."""
import json

import pytest


@pytest.fixture()
def flow_folder(tmp_path):
    proj = tmp_path / "proj" / "annotations"
    proj.mkdir(parents=True)

    def shape(label, x, y, order=None, gid=None, text=None, record=None):
        sh = {"label": label, "points": [[x, y], [x + 100, y + 30]],
              "shape_type": "rectangle", "flags": {}}
        if order is not None:
            sh["flow_order"] = order
            sh["group_id"] = gid
        if text is not None:
            sh["human_output"] = {"human_corrected_text": text}
        if record is not None:
            sh["structured"] = {"llm": record, "data": record, "edited": False}
        return sh

    # Two columns. Column 2's title sits HIGHER on the page (y=10) than
    # column 1's text (y=60) — a y-sort would propagate "Szervita-tér" into
    # the column-1 record. Flow order: col1 title, col1 text, col2 title,
    # col2 text.
    shapes = [
        shape("title", 10, 20, order=0, gid=1, text="Rézmál-dülő"),
        shape("cikkszoveg", 10, 60, order=1, gid=1,
              record={"buildings": [{"hazszam": "1"}]}),
        shape("title", 200, 10, order=2, gid=2, text="Szervita-tér"),
        shape("cikkszoveg", 200, 50, order=3, gid=2,
              record={"buildings": [{"hazszam": "4"}]}),
    ]
    (proj / "x1.json").write_text(json.dumps(
        {"shapes": shapes, "imagePath": "x1.jpg",
         "imageWidth": 400, "imageHeight": 200}), encoding="utf-8")
    return proj


def test_propagation_follows_flow_order(client, flow_folder):
    r = client.post("/api/export/json", params={"folder": str(flow_folder)},
                    json={"stems": ["x1"],
                          "label_modes": {"cikkszoveg": "export",
                                          "title": "propagate"},
                          "mode": "single"})
    assert r.status_code == 200
    recs = json.loads(r.text)
    assert len(recs) == 2
    # col-1 record carries col-1's street; col-2 record carries col-2's
    assert recs[0]["title"] == "Rézmál-dülő"
    assert recs[0]["buildings"][0]["hazszam"] == "1"
    assert recs[1]["title"] == "Szervita-tér"
    assert recs[1]["buildings"][0]["hazszam"] == "4"
