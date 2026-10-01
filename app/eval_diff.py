"""
Human-cost evaluation: predicted boxes vs verified boxes (P10.4).

Standard AP answers "is the model good?"; this answers "how much work is a
page?" — for each frozen-test page, match predicted shapes to ground-truth
shapes (same label, IoU-greedy) and count what a human would have to do:

  added   — ground-truth box with no matching prediction (model MISSED it;
            the human must draw it)
  deleted — prediction with no matching ground truth (model INVENTED it;
            the human must delete it)
  moved   — matched pair whose IoU is below the tight threshold (the human
            must drag edges)
  ok      — matched tightly enough to accept as-is

Counts aggregate per label and per page; corrections-per-page is the
headline number for the learning curve.
"""
from __future__ import annotations

import json
from pathlib import Path

IOU_MATCH = 0.5   # minimum overlap to count as "the same box"
IOU_TIGHT = 0.9   # below this, a matched box still needs manual adjustment


def _bbox(shape: dict):
    pts = shape.get("points") or []
    if len(pts) < 2:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def diff_page(gt_shapes: list[dict], pred_shapes: list[dict],
              iou_match: float = IOU_MATCH,
              iou_tight: float = IOU_TIGHT) -> dict:
    """Greedy best-IoU matching within each label. Returns counts per label."""
    by_label: dict = {}

    def bucket(label):
        return by_label.setdefault(
            label, {"added": 0, "deleted": 0, "moved": 0, "ok": 0})

    labels = {s.get("label", "") for s in gt_shapes} | \
             {s.get("label", "") for s in pred_shapes}
    for label in labels:
        gts = [b for s in gt_shapes if s.get("label", "") == label
               and (b := _bbox(s)) is not None]
        preds = [b for s in pred_shapes if s.get("label", "") == label
                 and (b := _bbox(s)) is not None]
        pairs = sorted(
            ((_iou(g, p), gi, pi)
             for gi, g in enumerate(gts) for pi, p in enumerate(preds)),
            key=lambda t: -t[0])
        used_g, used_p = set(), set()
        b = bucket(label)
        for iou, gi, pi in pairs:
            if iou < iou_match:
                break
            if gi in used_g or pi in used_p:
                continue
            used_g.add(gi)
            used_p.add(pi)
            if iou >= iou_tight:
                b["ok"] += 1
            else:
                b["moved"] += 1
        b["added"]   += len(gts) - len(used_g)
        b["deleted"] += len(preds) - len(used_p)
    return by_label


def evaluate_test_set(ann_dir: Path, pred_dir: Path, stems: list[str],
                      iou_match: float = IOU_MATCH,
                      iou_tight: float = IOU_TIGHT) -> dict:
    """Score predictions against annotations for the given stems.
    Pages without a prediction file count every GT shape as 'added'
    (the model produced nothing for them)."""
    per_label: dict = {}
    per_page = []
    n_missing_pred = 0

    for stem in stems:
        ann_f = ann_dir / f"{stem}.json"
        if not ann_f.exists():
            continue
        gt = json.loads(ann_f.read_text(encoding="utf-8")).get("shapes", [])
        pred_f = pred_dir / f"{stem}.json"
        if pred_f.exists():
            pred = json.loads(pred_f.read_text(encoding="utf-8")).get("shapes", [])
        else:
            pred = []
            n_missing_pred += 1
        page = diff_page(gt, pred, iou_match, iou_tight)
        tot = {"added": 0, "deleted": 0, "moved": 0, "ok": 0}
        for label, c in page.items():
            for k in tot:
                tot[k] += c[k]
                agg = per_label.setdefault(
                    label, {"added": 0, "deleted": 0, "moved": 0, "ok": 0})
                agg[k] += c[k] if k in c else 0
        corrections = tot["added"] + tot["deleted"] + tot["moved"]
        per_page.append({"stem": stem, **tot, "corrections": corrections})

    n_pages = len(per_page)
    totals = {"added": 0, "deleted": 0, "moved": 0, "ok": 0}
    for row in per_page:
        for k in totals:
            totals[k] += row[k]
    corrections = totals["added"] + totals["deleted"] + totals["moved"]
    return {
        "n_pages": n_pages,
        "n_missing_predictions": n_missing_pred,
        "totals": totals,
        "corrections_total": corrections,
        "corrections_per_page": round(corrections / n_pages, 2) if n_pages else None,
        "per_label": per_label,
        "per_page": per_page,
        "iou_match": iou_match,
        "iou_tight": iou_tight,
    }
