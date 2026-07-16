"""Phase H / P1: region vocabulary + table_region derivation from lattices."""
from app.regions import (REGION_LABELS_DEFAULT, region_labels_for,
                         derive_table_regions)


def _cell(x1, y1, x2, y2, table=0, row=1, col=1):
    return {"label": "cell", "points": [[x1, y1], [x2, y2]],
            "super_row": row, "super_column": col, "table": table}


def test_vocabulary_and_override():
    assert "table_region" in REGION_LABELS_DEFAULT
    assert region_labels_for({}) == REGION_LABELS_DEFAULT
    assert region_labels_for({"region_labels": ["a", "b"]}) == ["a", "b"]


def test_derive_single_table_bbox_with_margin():
    shapes = [_cell(100, 100, 200, 150), _cell(200, 100, 300, 150, col=2),
              _cell(100, 150, 200, 200, row=2)]
    out = derive_table_regions(shapes, 1000, 800, margin=10)
    assert len(out) == 1
    r = out[0]
    assert r["label"] == "table_region"
    (x1, y1), (x2, y2) = r["points"]
    assert (x1, y1, x2, y2) == (90, 90, 310, 210)
    assert r["derived_from"]["table"] == 0


def test_margin_clamped_to_image():
    out = derive_table_regions([_cell(2, 3, 995, 795)], 1000, 800, margin=10)
    (x1, y1), (x2, y2) = out[0]["points"]
    assert (x1, y1, x2, y2) == (0, 0, 1000, 800)


def test_two_tables_two_regions():
    shapes = [_cell(50, 50, 150, 100, table=0),
              _cell(50, 400, 150, 450, table=1)]
    out = derive_table_regions(shapes, 1000, 800)
    assert len(out) == 2
    assert [r["derived_from"]["table"] for r in out] == [0, 1]
    assert out[0]["points"][1][1] < out[1]["points"][0][1]   # vertically apart


def test_non_lattice_shapes_ignored():
    shapes = [{"label": "header", "points": [[0, 0], [500, 30]]},   # no super_*
              {"label": "cell", "points": [[10, 10]],               # degenerate
               "super_row": 1, "super_column": 1}]
    assert derive_table_regions(shapes, 1000, 800) == []
