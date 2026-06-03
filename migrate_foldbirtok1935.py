"""
One-time migration: flatten foldbirtok1935 into the structure the app expects.

Before:
  projects/foldbirtok1935/<batch_dir>/images/page_N.jpg
  projects/foldbirtok1935/<batch_dir>/images/page_N.json

After:
  projects/foldbirtok1935/annotations/<batch_dir>_page_N.jpg
  projects/foldbirtok1935/annotations/<batch_dir>_page_N.json  (imagePath updated)
  projects/foldbirtok1935/config.json
  projects/foldbirtok1935/pipeline.json

Run from the repo root:
  python3 migrate_foldbirtok1935.py
"""

import json, shutil
from pathlib import Path

PROJECT   = Path("projects/foldbirtok1935")
ANN_DIR   = PROJECT / "annotations"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

ANN_DIR.mkdir(exist_ok=True)

copied_imgs  = 0
copied_jsons = 0
skipped      = 0

for batch_dir in sorted(PROJECT.iterdir()):
    if not batch_dir.is_dir() or batch_dir.name == "annotations":
        continue
    images_dir = batch_dir / "images"
    if not images_dir.exists():
        continue

    for src in sorted(images_dir.iterdir()):
        new_stem = f"{batch_dir.name}_{src.stem}"
        new_name = new_stem + src.suffix

        if src.suffix.lower() in IMAGE_EXT:
            dst = ANN_DIR / new_name
            if dst.exists():
                skipped += 1
            else:
                shutil.copy2(src, dst)
                copied_imgs += 1

        elif src.suffix.lower() == ".json":
            dst = ANN_DIR / (new_stem + ".json")
            if dst.exists():
                skipped += 1
            else:
                data = json.loads(src.read_text(encoding="utf-8"))
                # Update imagePath to the new flat filename
                old_img = Path(data.get("imagePath", ""))
                data["imagePath"] = new_stem + old_img.suffix
                dst.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                copied_jsons += 1

print(f"Done. {copied_imgs} images, {copied_jsons} JSONs copied. {skipped} skipped (already existed).")

# --- config.json ---
config_path = PROJECT / "config.json"
if not config_path.exists():
    config = {
        "name": "foldbirtok1935",
        "type": "A",
        "labels": ["numerical_cell", "table_header", "text_cell", "page_header"],
        "server": {"host": "", "user": "", "key_path": "", "remote_path": ""},
        "llm": {"model": "gpt-4o-mini", "prompt": ""}
    }
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Created config.json  (edit labels if needed)")
else:
    print("config.json already exists — not overwritten")

# --- pipeline.json ---
pipeline_path = PROJECT / "pipeline.json"
if not pipeline_path.exists():
    pipeline = {
        "stage": "correcting",
        "updated": "2026-01-01T00:00:00+00:00",
        "server_job_id": None,
        "pages_total": 0,
        "pages_corrected": 0,
        "notes": ""
    }
    pipeline_path.write_text(json.dumps(pipeline, indent=2), encoding="utf-8")
    print("Created pipeline.json")
else:
    print("pipeline.json already exists — not overwritten")

print(f"\nProject ready. Open the dashboard and select 'foldbirtok1935'.")
