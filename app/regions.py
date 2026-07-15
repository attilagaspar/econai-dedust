"""
Phase H — region layer (hierarchical layout for Compass-class sources).

A "region" is an ordinary LabelMe shape whose label belongs to the project's
region vocabulary: page-scale objects (firm headers, text blocks, whole
tables, figures) as opposed to table cells. See
knowledge_base/09_hierarchical_layout_compass.md.

This module is the single source of truth for the region vocabulary and for
pure region computations (derivation from lattices now; the record-grouping
sweep will join it in a later phase).
"""
from __future__ import annotations

# Canonical vocabulary — projects may override with config.json "region_labels".
REGION_LABELS_DEFAULT = ["firm_header", "text_block", "table_region",
                         "figure", "page_header"]

# Labels never assigned to a record by the grouping sweep (running heads etc.);
# projects may override with config.json "group_ignore_labels".
GROUP_IGNORE_DEFAULT = ["page_header"]


def region_labels_for(cfg: dict) -> list[str]:
    labels = cfg.get("region_labels")
    return list(labels) if labels else list(REGION_LABELS_DEFAULT)


def derive_table_regions(shapes: list[dict], img_w: float, img_h: float,
                         margin: float = 8.0) -> list[dict]:
    """table_region shapes derived from existing lattice-cell annotations.

    Cells (shapes carrying super_row/super_column) are grouped by their
    `table` id; each group's bounding box + margin (clamped to the image)
    becomes one `table_region` shape. Pages without lattice cells yield [].
    This turns every cell-annotated page in existing projects into free
    training data for the region model's table class."""
    groups: dict = {}
    for sh in shapes:
        if sh.get("super_row") is None or sh.get("super_column") is None:
            continue
        pts = sh.get("points") or []
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        t = sh.get("table") or 0
        g = groups.setdefault(t, [float("inf"), float("inf"),
                                  float("-inf"), float("-inf")])
        g[0] = min(g[0], min(xs)); g[1] = min(g[1], min(ys))
        g[2] = max(g[2], max(xs)); g[3] = max(g[3], max(ys))

    out = []
    for t in sorted(groups):
        x1, y1, x2, y2 = groups[t]
        x1 = max(0.0, x1 - margin); y1 = max(0.0, y1 - margin)
        x2 = min(float(img_w), x2 + margin); y2 = min(float(img_h), y2 + margin)
        if x2 <= x1 or y2 <= y1:
            continue
        out.append({
            "label": "table_region",
            "points": [[x1, y1], [x2, y2]],
            "group_id": None,
            "shape_type": "rectangle",
            "flags": {},
            "derived_from": {"kind": "lattice_bbox", "table": t,
                             "margin": margin},
        })
    return out
