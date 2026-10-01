"""
LabelMe JSON → COCO JSON conversion for Detectron2 / layout-model-training.

Two layers of hygiene around the raw conversion (P10.1 + P10.2):

Status filter — only pages whose `flags.status` is corrected or verified
enter the training data (override with status_filter=False). Uncorrected
predictions training as ground truth is how an active-learning loop poisons
itself. `skip` pages NEVER enter, in any mode: skip conflates "clutter" with
"deliberately unannotated", so a skipped table page must not train as a
negative example.

Verified-empty negatives — a VERIFIED page with zero shapes is a real
statement ("there is nothing here") and enters the training set as a
negative example (include_empty_verified=False to disable). A merely
predicted empty page is nothing and stays out. "An empty page is an
annotation, not an absence."

Frozen test set — when test_stems is given, the train/test split is done
HERE (train.json + test.json written next to annotations.json) and the
generated train.sh skips cocosplit entirely, so evaluation runs on exactly
the frozen pages every time. Without test_stems the legacy cocosplit
random-split path is kept.
"""

from __future__ import annotations

import json
from pathlib import Path

TRAIN_STATUSES = ("corrected", "verified")


def collect_pages(ann_dir: Path) -> list[dict]:
    """All annotation pages with the facts the filters need."""
    pages = []
    for jf in sorted(ann_dir.glob("*.json"), key=lambda p: _page_sort_key(p.stem)):
        data = json.loads(jf.read_text(encoding="utf-8"))
        pages.append({
            "stem":     jf.stem,
            "data":     data,
            "status":   (data.get("flags") or {}).get("status") or "predicted",
            "n_shapes": len(data.get("shapes") or []),
        })
    return pages


def pages_to_coco(pages: list[dict], labels: list[str]) -> dict:
    """Convert the given pages to a single COCO dict.
    labels: ordered list of category names — determines category IDs (1-based).
    """
    label_to_id = {lbl: i + 1 for i, lbl in enumerate(labels)}

    categories = [{"id": i + 1, "name": lbl, "supercategory": "layout"}
                  for i, lbl in enumerate(labels)]

    images, annotations = [], []
    ann_id = 1

    for img_id, page in enumerate(pages, start=1):
        data = page["data"]
        fname = data.get("imagePath", page["stem"] + ".jpg")
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


def labelme_to_coco(ann_dir: Path, labels: list[str]) -> dict:
    """Legacy entry point: every page in ann_dir, no filtering."""
    return pages_to_coco(collect_pages(ann_dir), labels)


def prepare_training_data(project_name: str, ann_dir: Path,
                          labels: list[str], intermediate_dir: Path,
                          base_yaml_path: Path,
                          max_iter: int = 2000, base_lr: float = 0.00125,
                          ims_per_batch: int = 2, num_workers: int = 2,
                          status_filter: bool = True,
                          include_empty_verified: bool = True,
                          test_stems: list[str] | None = None,
                          train_fraction: float = 1.0) -> dict:
    """
    1. Convert LabelMe JSONs → COCO annotations.json (status-filtered).
    2. When a frozen test set is given: write train.json/test.json here
       (deterministic split) instead of letting cocosplit re-roll it.
    3. Copy + patch the base yaml config (NUM_CLASSES).
    4. Generate the training .sh script (with hand-editable solver params).
    Returns paths dict + export counts.
    """
    # Guard against junk values from the UI
    max_iter      = max(1, int(max_iter or 2000))
    ims_per_batch = max(1, int(ims_per_batch or 2))
    try:
        num_workers = int(num_workers)
    except (TypeError, ValueError):
        num_workers = 2
    num_workers = max(0, num_workers)   # 0 = load in main process (safest for small /dev/shm)
    try:
        base_lr = float(base_lr)
    except (TypeError, ValueError):
        base_lr = 0.00125
    if base_lr <= 0:
        base_lr = 0.00125
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. page selection ────────────────────────────────────────────────────
    pages = collect_pages(ann_dir)
    test_set = set(test_stems or [])
    missing_test = sorted(test_set - {p["stem"] for p in pages})

    counts = {"verified": 0, "corrected": 0, "predicted": 0, "problem": 0,
              "skip": 0}
    train_pages, test_pages = [], []
    n_excluded_status = 0
    n_negatives = 0
    for p in pages:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
        if p["status"] == "skip":
            continue                           # never trains, never evaluates
        if p["stem"] in test_set:
            test_pages.append(p)               # frozen pages evaluate, period
            continue
        if status_filter and p["status"] not in TRAIN_STATUSES:
            n_excluded_status += 1
            continue
        if p["n_shapes"] == 0:
            # empty page: a verified empty is a negative example (opt-out);
            # any other empty is just "not annotated yet" and must stay out.
            if include_empty_verified and p["status"] == "verified":
                n_negatives += 1
                train_pages.append(p)
            elif not status_filter:
                train_pages.append(p)          # legacy: cocosplit pools it
            continue
        train_pages.append(p)

    # P10.5 learning-curve runs: train on a deterministic NESTED subset.
    # Ordering by each stem's hash is stable across runs and projects, so the
    # 50% subset is always contained in the 75% subset is contained in 100% —
    # score-vs-quantity curves compare like with like.
    try:
        train_fraction = float(train_fraction)
    except (TypeError, ValueError):
        train_fraction = 1.0
    train_fraction = min(1.0, max(0.0, train_fraction)) or 1.0
    n_before_fraction = len(train_pages)
    if train_fraction < 1.0 and train_pages:
        import hashlib
        ranked = sorted(train_pages,
                        key=lambda p: hashlib.sha1(p["stem"].encode()).hexdigest())
        keep = max(1, round(len(ranked) * train_fraction))
        kept_stems = {p["stem"] for p in ranked[:keep]}
        train_pages = [p for p in train_pages if p["stem"] in kept_stems]
        n_negatives = sum(1 for p in train_pages if p["n_shapes"] == 0)

    server_side_split = bool(test_set)

    # ── 2. COCO JSON(s) ──────────────────────────────────────────────────────
    coco = pages_to_coco(train_pages + test_pages, labels)
    n_annotated = sum(1 for im in coco["images"]
                      if any(a["image_id"] == im["id"]
                             for a in coco["annotations"]))
    coco_path = intermediate_dir / "annotations.json"
    coco_path.write_text(json.dumps(coco, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    if server_side_split:
        train_coco = pages_to_coco(train_pages, labels)
        test_coco  = pages_to_coco(test_pages, labels)
        (intermediate_dir / "train.json").write_text(
            json.dumps(train_coco, indent=2, ensure_ascii=False), encoding="utf-8")
        (intermediate_dir / "test.json").write_text(
            json.dumps(test_coco, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        # stale local splits from an earlier frozen set must not get pushed
        for f in ("train.json", "test.json"):
            try:
                (intermediate_dir / f).unlink()
            except FileNotFoundError:
                pass

    # ── 3. Detectron2 config yaml — patch NUM_CLASSES ────────────────────────
    n_classes = len(labels)
    yaml_src = base_yaml_path.read_text(encoding="utf-8")
    import re
    yaml_patched = re.sub(r"NUM_CLASSES:\s*\d+",
                          f"NUM_CLASSES: {n_classes}", yaml_src)
    cfg_dir = intermediate_dir / "configs" / project_name
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "fast_rcnn_R_50_FPN_3x.yaml"
    cfg_path.write_text(yaml_patched, encoding="utf-8")

    # ── 4. Training shell script ─────────────────────────────────────────────
    remote_ws = "/workspace"
    split_block = make_split_block(project_name, server_side_split)
    extra_opts = ""
    if n_negatives:
        # otherwise detectron2 silently drops the negative examples
        extra_opts = " \\\n    DATALOADER.FILTER_EMPTY_ANNOTATIONS False"
    sh = f"""#!/bin/bash
set -e
echo "=== EconAI: {project_name} training ==="
{split_block}
echo "=== Cleaning previous checkpoints ==="
rm -f {remote_ws}/layout-model-training/outputs/{project_name}/fast_rcnn_R_50_FPN_3x/*.pth
rm -f {remote_ws}/layout-model-training/outputs/{project_name}/fast_rcnn_R_50_FPN_3x/last_checkpoint

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
    SOLVER.IMS_PER_BATCH {ims_per_batch} \\
    SOLVER.BASE_LR {base_lr} \\
    SOLVER.MAX_ITER {max_iter} \\
    DATALOADER.NUM_WORKERS {num_workers}{extra_opts}
echo "=== Training complete ==="
"""
    sh_path = intermediate_dir / "train.sh"
    sh_path.write_text(sh, encoding="utf-8")

    # ── 5. Inference shell script ────────────────────────────────────────────
    infer_sh = f"""#!/bin/bash
set -e
echo "=== EconAI: {project_name} inference ==="
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

    result = {
        "coco_path":    str(coco_path),
        "config_path":  str(cfg_path),
        "train_sh":     str(sh_path),
        "infer_sh":     str(infer_sh_path),
        "n_images":     len(coco["images"]),
        "n_annotated":  n_annotated,
        "n_annotations": len(coco["annotations"]),
        "n_classes":    n_classes,
        "max_iter":     max_iter,
        "base_lr":      base_lr,
        "ims_per_batch": ims_per_batch,
        "num_workers":  num_workers,
        # export hygiene facts (ledger + UI)
        "status_filter":          status_filter,
        "include_empty_verified": include_empty_verified,
        "server_side_split":      server_side_split,
        "train_fraction":         train_fraction,
        "n_train_pool":           n_before_fraction,
        "n_train_pages":          len(train_pages),
        "n_test_pages":           len(test_pages),
        "n_negatives":            n_negatives,
        "n_excluded_status":      n_excluded_status,
        "n_skip":                 counts.get("skip", 0),
        "status_counts":          counts,
        "missing_test_stems":     missing_test,
    }
    (intermediate_dir / "train_summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def make_split_block(project_name: str, server_side_split: bool) -> str:
    """The train/test-split section of a training script: either 'splits were
    computed locally and uploaded' (frozen test set) or the legacy cocosplit
    re-roll. Shared by Train and fine-tune-from script generation."""
    remote_ws = "/workspace"
    if server_side_split:
        return (f'echo "Using uploaded train/test split '
                f'(frozen test set — no cocosplit)."')
    return f"""echo "Running cocosplit..."
cd {remote_ws}/layout-model-training
python3 utils/cocosplit.py \\
    --annotation-path {remote_ws}/{project_name}/annotations.json \\
    --train            {remote_ws}/{project_name}/train.json \\
    --test             {remote_ws}/{project_name}/test.json \\
    --split-ratio      0.8 \\
    --having-annotations"""


def _page_sort_key(name: str) -> tuple:
    import re
    parts = re.split(r"(\d+)", name)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)
