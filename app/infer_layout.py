"""
infer_layout.py — Run Detectron2 layout inference on a folder of images.
Outputs one LabelMe JSON per image in the output folder.

This script runs INSIDE the Docker container on the GPU server.

Usage:
  python3 infer_layout.py \
    --config  /workspace/layout-model-training/configs/<name>/fast_rcnn_R_50_FPN_3x.yaml \
    --weights /workspace/layout-model-training/outputs/<name>/fast_rcnn_R_50_FPN_3x/model_final.pth \
    --images  /workspace/<name>/images \
    --output  /workspace/<name>/predictions \
    --labels  label1 label2 label3 \
    --threshold 0.5
"""

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
from detectron2.config import get_cfg
from detectron2.engine import DefaultPredictor


def build_predictor(config_path: str, weights_path: str,
                    n_classes: int, threshold: float):
    cfg = get_cfg()
    cfg.merge_from_file(config_path)
    cfg.MODEL.WEIGHTS = weights_path
    cfg.MODEL.ROI_HEADS.NUM_CLASSES = n_classes
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = threshold
    cfg.MODEL.DEVICE = "cuda"
    return DefaultPredictor(cfg)


def build_diagnostic_predictor(config_path: str, weights_path: str, n_classes: int):
    """Same as build_predictor but with threshold=0.0 to expose raw scores."""
    cfg = get_cfg()
    cfg.merge_from_file(config_path)
    cfg.MODEL.WEIGHTS = weights_path
    cfg.MODEL.ROI_HEADS.NUM_CLASSES = n_classes
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = 0.0
    cfg.MODEL.DEVICE = "cuda"
    return DefaultPredictor(cfg)


def boxes_to_labelme(image_path: Path, instances, label_map: dict,
                     img_w: int, img_h: int) -> dict:
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

    return {
        "version":     "5.0.0",
        "flags":       {},
        "shapes":      shapes,
        "imagePath":   image_path.name,
        "imageData":   None,
        "imageHeight": img_h,
        "imageWidth":  img_w,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",    required=True)
    parser.add_argument("--weights",   required=True)
    parser.add_argument("--images",    required=True)
    parser.add_argument("--output",    required=True)
    parser.add_argument("--labels",    nargs="+", required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    label_map = {i: lbl for i, lbl in enumerate(args.labels)}
    n_classes = len(args.labels)

    print(f"Loading model: {args.weights}")
    predictor = build_predictor(args.config, args.weights, n_classes, args.threshold)
    diag_predictor = None  # built lazily on first zero-result image

    img_dir = Path(args.images)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

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
        outputs = predictor(img)
        instances = outputs["instances"]
        lm = boxes_to_labelme(img_path, instances, label_map, w, h)
        out_path = out_dir / (img_path.stem + ".json")
        out_path.write_text(json.dumps(lm, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        n_boxes = len(lm['shapes'])
        print(f"  → {n_boxes} boxes detected")

        # On the first image that returns zero boxes, run a threshold-free pass
        # to reveal the true score distribution and diagnose the problem.
        if n_boxes == 0 and zero_count == 0:
            zero_count += 1
            print("  [diag] Running threshold=0.0 pass to inspect raw scores...")
            if diag_predictor is None:
                diag_predictor = build_diagnostic_predictor(
                    args.config, args.weights, n_classes)
            raw = diag_predictor(img)["instances"]
            if len(raw) == 0:
                print("  [diag] threshold=0.0 also returned 0 instances — "
                      "model produces no proposals at all. "
                      "Likely NUM_CLASSES mismatch or corrupt weights.")
            else:
                scores = raw.scores.cpu().numpy()
                print(f"  [diag] threshold=0.0 returned {len(raw)} instances")
                print(f"  [diag] score range: {scores.min():.4f} – {scores.max():.4f}  "
                      f"mean={scores.mean():.4f}")
                print(f"  [diag] scores > 0.5: {(scores > 0.5).sum()}  "
                      f"> 0.3: {(scores > 0.3).sum()}  "
                      f"> 0.1: {(scores > 0.1).sum()}")
        elif n_boxes == 0:
            zero_count += 1

    print(f"\nDone. Predictions saved to {out_dir}")


if __name__ == "__main__":
    main()
