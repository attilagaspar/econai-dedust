"""
Pipeline state machine.

Each project has a config.json (static settings) and a pipeline.json (current state).
Stages are ordered lists; advancing moves to the next one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

PROJECTS_ROOT = Path(__file__).parent.parent / "projects"


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

STAGES_TYPE_A = [
    "raw",           # project created, waiting for images/JSONs
    "annotating",    # generating training data in LabelStudio
    "training",      # layout model training on GPU server
    "predicting",    # layout model inference on GPU server
    "correcting",    # reviewing/editing predicted boxes in the web editor
    "superstructure", # running layout_superstructure_detect.py
    "ocr",           # running add_ocr_to_layout_jsons.py
    "llm_cleaning",  # running add_llm_cleaning_to_layout_jsons.py
    "validating",    # human cell-by-cell validation in the web validator
    "exporting",     # running json_join_excel_export.py
    "done",
]

STAGES_TYPE_B = [
    "raw",
    "annotating",
    "training",
    "predicting",
    "correcting",
    "ocr",
    "llm_cleaning",
    "validating",
    "exporting",
    "done",
]

STAGE_DESCRIPTIONS = {
    "raw":            "Waiting for annotated images / raw data",
    "annotating":     "Generating training data in LabelStudio",
    "training":       "Layout model training on GPU server",
    "predicting":     "Layout model inference on GPU server",
    "correcting":     "Reviewing & correcting predicted boxes",
    "superstructure": "Detecting table superstructure (rows/columns)",
    "ocr":            "Running Tesseract OCR on cells",
    "llm_cleaning":   "Running LLM cleaning on cells",
    "validating":     "Human cell-by-cell validation",
    "exporting":      "Exporting to Excel / CSV",
    "done":           "Complete",
}


# ---------------------------------------------------------------------------
# Config schema (written once at project creation)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "name": "",
    "type": "A",          # "A" (tables) or "B" (structured text)
    "labels": [],         # layout label names used in this project
    "server": {
        "host": "",       # e.g. "gpu.myuni.edu"
        "user": "",
        "key_path": "",   # path to private SSH key
        "remote_path": "" # absolute path on server, e.g. "/home/user/econai"
    },
    "llm": {
        "model": "gpt-4o-mini",
        "prompt": ""      # custom prompt for LLM cleaning (optional)
    }
}

DEFAULT_PIPELINE = {
    "stage": "raw",
    "updated": "",
    "server_job_id": None,
    "pages_total": 0,
    "pages_corrected": 0,
    "notes": ""
}


# ---------------------------------------------------------------------------
# Project helpers
# ---------------------------------------------------------------------------

def project_dir(name: str) -> Path:
    return PROJECTS_ROOT / name


def config_path(name: str) -> Path:
    return project_dir(name) / "config.json"


def pipeline_path(name: str) -> Path:
    return project_dir(name) / "pipeline.json"


def load_config(name: str) -> dict:
    p = config_path(name)
    if not p.exists():
        raise FileNotFoundError(f"No project '{name}' found at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def load_pipeline(name: str) -> dict:
    p = pipeline_path(name)
    if not p.exists():
        raise FileNotFoundError(f"No pipeline.json for project '{name}'")
    return json.loads(p.read_text(encoding="utf-8"))


def save_pipeline(name: str, state: dict) -> None:
    state["updated"] = datetime.now(timezone.utc).isoformat()
    pipeline_path(name).write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def stages_for(project_type: str) -> list[str]:
    if project_type == "A":
        return STAGES_TYPE_A
    elif project_type == "B":
        return STAGES_TYPE_B
    raise ValueError(f"Unknown project type '{project_type}'. Use 'A' or 'B'.")


def current_stage(name: str) -> str:
    return load_pipeline(name)["stage"]


def advance_stage(name: str) -> str:
    """Move project to the next stage. Returns the new stage name."""
    cfg = load_config(name)
    state = load_pipeline(name)
    stages = stages_for(cfg["type"])
    current = state["stage"]
    if current == "done":
        raise ValueError("Project is already done.")
    idx = stages.index(current)
    next_stage = stages[idx + 1]
    state["stage"] = next_stage
    save_pipeline(name, state)
    return next_stage


def set_stage(name: str, stage: str) -> None:
    """Jump to a specific stage (e.g. to go back for retraining)."""
    cfg = load_config(name)
    stages = stages_for(cfg["type"])
    if stage not in stages:
        raise ValueError(f"'{stage}' is not a valid stage for type {cfg['type']}.")
    state = load_pipeline(name)
    state["stage"] = stage
    save_pipeline(name, state)


# ---------------------------------------------------------------------------
# Project creation
# ---------------------------------------------------------------------------

def clone_project(source_name: str, new_name: str) -> Path:
    """Deep-copy a project directory to a new name, updating config.json."""
    import shutil
    src = project_dir(source_name)
    if not src.exists():
        raise FileNotFoundError(f"Source project '{source_name}' not found")
    dst = project_dir(new_name)
    if dst.exists():
        raise FileExistsError(f"Project '{new_name}' already exists")
    shutil.copytree(src, dst)
    # Patch the name field in config.json
    cfg_path = dst / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["name"] = new_name
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return dst


def create_project(name: str, project_type: str, labels: list[str]) -> Path:
    """Create project directory structure, config.json, and pipeline.json."""
    if project_type not in ("A", "B"):
        raise ValueError("project_type must be 'A' or 'B'")

    pdir = project_dir(name)
    if pdir.exists():
        raise FileExistsError(f"Project '{name}' already exists at {pdir}")

    # Create subdirectories
    for subdir in ("annotations", "intermediate", "output"):
        (pdir / subdir).mkdir(parents=True)

    # Write config
    cfg = DEFAULT_CONFIG.copy()
    cfg["name"] = name
    cfg["type"] = project_type
    cfg["labels"] = labels
    config_path(name).write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Write initial pipeline state
    state = DEFAULT_PIPELINE.copy()
    state["updated"] = datetime.now(timezone.utc).isoformat()
    pipeline_path(name).write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return pdir


def list_projects() -> list[dict]:
    """Return summary dicts for all projects."""
    if not PROJECTS_ROOT.exists():
        return []
    results = []
    for pdir in sorted(PROJECTS_ROOT.iterdir()):
        if not pdir.is_dir():
            continue
        try:
            cfg = load_config(pdir.name)
            state = load_pipeline(pdir.name)
            # count annotation files
            ann_dir = pdir / "annotations"
            n_pages = len(list(ann_dir.glob("*.json"))) if ann_dir.exists() else 0
            results.append({
                "name": pdir.name,
                "type": cfg["type"],
                "stage": state["stage"],
                "description": STAGE_DESCRIPTIONS.get(state["stage"], ""),
                "pages": n_pages,
                "updated": state.get("updated", ""),
            })
        except Exception:
            pass
    return results
