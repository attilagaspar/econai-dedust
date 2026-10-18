"""
infer_layout.py — Run Detectron2 layout inference on a folder of images.
Outputs one LabelMe JSON per image in the output folder.

This script runs INSIDE the Docker container on the GPU server. It is also
imported by the Dedust server (for the pure crop-geometry helpers below), so
cv2 / detectron2 are imported lazily — the module itself must load on a
laptop without a GPU.

Usage (whole page, the default):
  python3 infer_layout.py \
    --config  /workspace/layout-model-training/configs/<name>/fast_rcnn_R_50_FPN_3x.yaml \
    --weights /workspace/layout-model-training/outputs/<name>/fast_rcnn_R_50_FPN_3x/model_final.pth \
    --images  /workspace/<name>/images \
    --output  /workspace/<name>/predictions \
    --labels  label1 label2 label3 \
    --threshold 0.5

Nested detection scope (`within:<parent_label>`, see
knowledge_base/09_hierarchical_layout_compass.md §H8): add
    --parents /workspace/<name>/parents --parent-label firm_area --margin 12
The model then runs on a crop of every parent shape listed in
<parents>/<stem>.json and the boxes are mapped back to page coordinates;
still one LabelMe per page, holding only the predicted children.
"""

import argparse
import json
import os
import sys
from pathlib import Path


# ── pure crop geometry (shared with the Dedust server's training export) ─────

def box_of(points) -> tuple:
    """(x1, y1, x2, y2) of a LabelMe rectangle/polygon's points."""
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def crop_rect(parent_box, img_w, img_h, margin: float = 12.0) -> tuple:
    """Integer crop rectangle (x1, y1, x2, y2) for a parent box: the box
    grown by `margin` px on every side and clamped to the image. Used
    identically at training-export time and at inference time so the model
    sees the same framing in both."""
    x1, y1, x2, y2 = parent_box
    cx1 = max(0, int(x1 - margin))
    cy1 = max(0, int(y1 - margin))
    cx2 = min(int(img_w), int(round(x2 + margin)))
    cy2 = min(int(img_h), int(round(y2 + margin)))
    return cx1, cy1, cx2, cy2


def map_boxes_to_page(shapes, crop, parent_box=None, clip: bool = True) -> list:
    """Translate crop-coordinate shapes back to page coordinates by adding
    the crop's offset. With clip=True the result is clipped to parent_box
    (children of a parent lie within it by definition); degenerate boxes are
    dropped. Pure function, unit-tested without a GPU."""
    ox, oy = float(crop[0]), float(crop[1])
    out = []
    for sh in shapes:
        pts = sh.get("points") or []
        if len(pts) < 2:
            continue
        x1, y1, x2, y2 = box_of(pts)
        x1 += ox; x2 += ox; y1 += oy; y2 += oy
        if clip and parent_box is not None:
            px1, py1, px2, py2 = parent_box
            x1 = max(x1, px1); y1 = max(y1, py1)
            x2 = min(x2, px2); y2 = min(y2, py2)
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            continue
        new = dict(sh)
        new["points"] = [[x1, y1], [x2, y2]]
        out.append(new)
    return out


def parents_in(parents_doc: dict, parent_label: str) -> list:
    """Parent boxes (x1,y1,x2,y2) of the given label in a parents/<stem>.json
    (or any LabelMe doc), top-to-bottom then left-to-right — the same order
    the training export uses for crop indices."""
    boxes = []
    for sh in (parents_doc or {}).get("shapes") or []:
        if sh.get("label") != parent_label:
            continue
        pts = sh.get("points") or []
        if len(pts) < 2:
            continue
        b = box_of(pts)
        if b[2] - b[0] <= 0 or b[3] - b[1] <= 0:
            continue
        boxes.append(b)
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


# ── detectron2 plumbing (GPU container only) ─────────────────────────────────

def build_predictor(config_path: str, weights_path: str,
                    n_classes: int, threshold: float):
    from detectron2.config import get_cfg
    from detectron2.engine import DefaultPredictor
    cfg = get_cfg()
    cfg.merge_from_file(config_path)
    cfg.MODEL.WEIGHTS = weights_path
    cfg.MODEL.ROI_HEADS.NUM_CLASSES = n_classes
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = threshold
    cfg.MODEL.DEVICE = "cuda"
    return DefaultPredictor(cfg)


def build_diagnostic_predictor(config_path: str, weights_path: str, n_classes: int):
    """Same as build_predictor but with threshold=0.0 to expose raw scores."""
    return build_predictor(config_path, weights_path, n_classes, 0.0)


def instances_to_shapes(instances, label_map: dict) -> list:
    shapes = []
    boxes  = instances.pred_boxes.tensor.cpu().numpy() if instances.has("pred_boxes") else []
    classes = instances.pred_classes.cpu().numpy()     if instances.has("pred_classes") else []
    scores  = instances.scores.cpu().numpy()           if instances.has("scores") else []
    for box, cls, score in zip(boxes, classes, scores):
        x1, y1, x2, y2 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
        label = label_map.get(int(cls), f"class_{cls}")
        shapes.append({
            "label":      label,
            "points":     [[x1, y1], [x2, y2]],
            "group_id":   None,
            "shape_type": "rectangle",
            "flags":      {},
            "score":      round(float(score), 4),
        })
    return shapes


def labelme_doc(image_path: Path, shapes: list, img_w: int, img_h: int) -> dict:
    return {
        "version":     "5.0.0",
        "flags":       {},
        "shapes":      shapes,
        "imagePath":   image_path.name,
        "imageData":   None,
        "imageHeight": img_h,
        "imageWidth":  img_w,
    }


def boxes_to_labelme(image_path: Path, instances, label_map: dict,
                     img_w: int, img_h: int) -> dict:
    return labelme_doc(image_path, instances_to_shapes(instances, label_map),
                       img_w, img_h)


def _diagnose_zero(diag_predictor, img):
    raw = diag_predictor(img)["instances"]
    if len(raw) == 0:
        print("  [diag] threshold=0.0 also returned 0 instances — "
              "model produces no proposals at all. "
              "Likely NUM_CLASSES mismatch or corrupt weights.")
        return
    scores = raw.scores.cpu().numpy()
    print(f"  [diag] threshold=0.0 returned {len(raw)} instances")
    print(f"  [diag] score range: {scores.min():.4f} – {scores.max():.4f}  "
          f"mean={scores.mean():.4f}")
    print(f"  [diag] scores > 0.5: {(scores > 0.5).sum()}  "
          f"> 0.3: {(scores > 0.3).sum()}  "
          f"> 0.1: {(scores > 0.1).sum()}")


def main():
    import cv2

    parser = argparse.ArgumentParser()
    parser.add_argument("--config",    required=True)
    parser.add_argument("--weights",   required=True)
    parser.add_argument("--images",    required=True)
    parser.add_argument("--output",    required=True)
    parser.add_argument("--labels",    nargs="+", required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    # nested detection scope
    parser.add_argument("--parents", default=None,
                        help="folder of per-page JSONs holding the parent shapes; "
                             "enables crop inference within them")
    parser.add_argument("--parent-label", default=None)
    parser.add_argument("--margin", type=float, default=12.0)
    args = parser.parse_args()

    label_map = {i: lbl for i, lbl in enumerate(args.labels)}
    n_classes = len(args.labels)
    nested = bool(args.parents)
    if nested and not args.parent_label:
        sys.exit("--parents requires --parent-label")

    print(f"Loading model: {args.weights}")
    predictor = build_predictor(args.config, args.weights, n_classes, args.threshold)
    diag_predictor = None  # built lazily on first zero-result image

    img_dir = Path(args.images)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    parents_dir = Path(args.parents) if nested else None
    if nested:
        print(f"Nested scope: within '{args.parent_label}' "
              f"(parents from {parents_dir}, margin {args.margin}px)")

    exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
    images = sorted(p for p in img_dir.iterdir() if p.suffix.lower() in exts)
    print(f"Found {len(images)} images in {img_dir}")

    zero_count = 0
    for i, img_path in enumerate(images, 1):
        print(f"[{i}/{len(images)}] {img_path.name}", flush=True)
        img = cv2.imread(str(img_path))
        if img is None:
            print(f"  WARNING: could not read {img_path.name}, skipping")
            continue
        h, w = img.shape[:2]

        if not nested:
            instances = predictor(img)["instances"]
            shapes = instances_to_shapes(instances, label_map)
        else:
            pj = parents_dir / (img_path.stem + ".json")
            parents = []
            if pj.exists():
                try:
                    parents = parents_in(json.loads(pj.read_text(encoding="utf-8")),
                                         args.parent_label)
                except Exception as e:
                    print(f"  WARNING: unreadable parents file {pj.name}: {e}")
            shapes = []
            for k, pbox in enumerate(parents):
                cx1, cy1, cx2, cy2 = crop_rect(pbox, w, h, args.margin)
                if cx2 - cx1 <= 1 or cy2 - cy1 <= 1:
                    continue
                crop_img = img[cy1:cy2, cx1:cx2]
                inst = predictor(crop_img)["instances"]
                local = instances_to_shapes(inst, label_map)
                shapes.extend(map_boxes_to_page(local, (cx1, cy1, cx2, cy2), pbox))
            print(f"  {len(parents)} parent(s)")

        lm = labelme_doc(img_path, shapes, w, h)
        out_path = out_dir / (img_path.stem + ".json")
        out_path.write_text(json.dumps(lm, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        n_boxes = len(shapes)
        print(f"  → {n_boxes} boxes detected")

        # On the first image that returns zero boxes, run a threshold-free pass
        # to reveal the true score distribution and diagnose the problem.
        # (Nested mode: only when the page actually had parents to look into.)
        if n_boxes == 0 and zero_count == 0 and (not nested or parents):
            zero_count += 1
            print("  [diag] Running threshold=0.0 pass to inspect raw scores...")
            if diag_predictor is None:
                diag_predictor = build_diagnostic_predictor(
                    args.config, args.weights, n_classes)
            if nested:
                cx1, cy1, cx2, cy2 = crop_rect(parents[0], w, h, args.margin)
                _diagnose_zero(diag_predictor, img[cy1:cy2, cx1:cx2])
            else:
                _diagnose_zero(diag_predictor, img)
        elif n_boxes == 0:
            zero_count += 1

    print(f"\nDone. Predictions saved to {out_dir}")


if __name__ == "__main__":
    main()
