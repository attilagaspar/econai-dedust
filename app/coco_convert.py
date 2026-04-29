"""
LabelMe JSON → COCO JSON conversion for Detectron2 / layout-model-training.

Only shapes that have been annotated (shapes[] non-empty) contribute to the
COCO output.  Pages with no shapes are included in the images list but have
no annotations — cocosplit.py will put them in the unannotated pool.
"""

from __future__ import annotations

import json
from pathlib import Path


def labelme_to_coco(ann_dir: Path, labels: list[str]) -> dict:
    """
    Convert all LabelMe JSONs in ann_dir to a single COCO dict.
    labels: ordered list of category names — determines category IDs (1-based).
    """
    label_to_id = {lbl: i + 1 for i, lbl in enumerate(labels)}

    categories = [{"id": i + 1, "name": lbl, "supercategory": "layout"}
                  for i, lbl in enumerate(labels)]

    images, annotations = [], []
    ann_id = 1

    json_files = sorted(ann_dir.glob("*.json"),
                        key=lambda p: _page_sort_key(p.stem))

    for img_id, jf in enumerate(json_files, start=1):
        data = json.loads(jf.read_text(encoding="utf-8"))
        fname = data.get("imagePath", jf.stem + ".jpg")
        w = data.get("imageWidth", 0)
        h = data.get("imageHeight", 0)

        images.append({"id": img_id, "file_name": fname,
                       "width": w, "height": h})

        for shape in data.get("shapes", []):
            label = shape.get("label", "")
            if label not in label_to_id:
                continue  # skip unknown labels
            pts = shape.get("points", [])
            if len(pts) < 2:
                continue

            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x1, y1 = min(xs), min(ys)
            x2, y2 = max(xs), max(ys)
            bw, bh = x2 - x1, y2 - y1
            if bw <= 0 or bh <= 0:
                continue

            # COCO segmentation as rectangular polygon
            seg = [[x1, y1, x2, y1, x2, y2, x1, y2]]

            annotations.append({
                "id":          ann_id,
                "image_id":    img_id,
                "category_id": label_to_id[label],
                "bbox":        [x1, y1, bw, bh],
                "area":        bw * bh,
                "segmentation": seg,
                "iscrowd":     0,
            })
            ann_id += 1

    return {"images": images, "annotations": annotations,
            "categories": categories}


def prepare_training_data(project_name: str, ann_dir: Path,
                          labels: list[str], intermediate_dir: Path,
                          base_yaml_path: Path) -> dict:
    """
    1. Convert LabelMe JSONs → COCO annotations.json
    2. Copy + patch the base yaml config (NUM_CLASSES).
    3. Generate the training .sh script.
    Returns paths dict.
    """
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    # 1. COCO JSON
    coco = labelme_to_coco(ann_dir, labels)
    n_annotated = sum(1 for im in coco["images"]
                      if any(a["image_id"] == im["id"]
                             for a in coco["annotations"]))
    coco_path = intermediate_dir / "annotations.json"
    coco_path.write_text(json.dumps(coco, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    # 2. Detectron2 config yaml — patch NUM_CLASSES
    n_classes = len(labels)
    yaml_src = base_yaml_path.read_text(encoding="utf-8")
    # patch all NUM_CLASSES occurrences
    import re
    yaml_patched = re.sub(r"NUM_CLASSES:\s*\d+",
                          f"NUM_CLASSES: {n_classes}", yaml_src)
    cfg_dir = intermediate_dir / "configs" / project_name
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "fast_rcnn_R_50_FPN_3x.yaml"
    cfg_path.write_text(yaml_patched, encoding="utf-8")

    # 3. Training shell script
    remote_ws = "/workspace"
    sh = f"""#!/bin/bash
set -e
echo "=== EconAI: {project_name} training ==="
echo "Running cocosplit..."
cd {remote_ws}/layout-model-training
python3 utils/cocosplit.py \\
    --annotation-path {remote_ws}/{project_name}/annotations.json \\
    --train            {remote_ws}/{project_name}/train.json \\
    --test             {remote_ws}/{project_name}/test.json \\
    --split-ratio      0.8 \\
    --having-annotations

echo "=== Starting training ==="
cd {remote_ws}/layout-model-training/tools
python3 train_net.py \\
    --dataset_name          {project_name}-layout \\
    --json_annotation_train {remote_ws}/{project_name}/train.json \\
    --image_path_train      {remote_ws}/{project_name}/images \\
    --json_annotation_val   {remote_ws}/{project_name}/test.json \\
    --image_path_val        {remote_ws}/{project_name}/images \\
    --resume \\
    --config-file           {remote_ws}/layout-model-training/configs/{project_name}/fast_rcnn_R_50_FPN_3x.yaml \\
    OUTPUT_DIR  {remote_ws}/layout-model-training/outputs/{project_name}/fast_rcnn_R_50_FPN_3x/ \\
    SOLVER.IMS_PER_BATCH 2
echo "=== Training complete ==="
"""
    sh_path = intermediate_dir / "train.sh"
    sh_path.write_text(sh, encoding="utf-8")

    # 4. Inference shell script
    infer_sh = f"""#!/bin/bash
set -e
echo "=== EconAI: {project_name} inference ==="
cd {remote_ws}/layout-model-training/tools
python3 {remote_ws}/layout-model-training/tools/infer_layout.py \\
    --config  {remote_ws}/layout-model-training/configs/{project_name}/fast_rcnn_R_50_FPN_3x.yaml \\
    --weights {remote_ws}/layout-model-training/outputs/{project_name}/fast_rcnn_R_50_FPN_3x/model_final.pth \\
    --images  {remote_ws}/{project_name}/images \\
    --output  {remote_ws}/{project_name}/predictions \\
    --labels  {" ".join(labels)} \\
    --threshold 0.5
echo "=== Inference complete ==="
"""
    infer_sh_path = intermediate_dir / "infer.sh"
    infer_sh_path.write_text(infer_sh, encoding="utf-8")

    return {
        "coco_path":    str(coco_path),
        "config_path":  str(cfg_path),
        "train_sh":     str(sh_path),
        "infer_sh":     str(infer_sh_path),
        "n_images":     len(coco["images"]),
        "n_annotated":  n_annotated,
        "n_annotations": len(coco["annotations"]),
        "n_classes":    n_classes,
    }


def _page_sort_key(name: str) -> tuple:
    import re
    parts = re.split(r"(\d+)", name)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)
