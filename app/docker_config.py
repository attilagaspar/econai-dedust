"""
Docker container configuration — persisted to docker_config.json next to this file.

Defaults match the existing containers so the current workflow is unchanged.
"""
from __future__ import annotations
import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "docker_config.json"

_DEFAULTS: dict = {
    "predict_container": "dedust_predict",
    "train_container":   "dedust_train",
    "image_name":        "dedust-layout",
}


def load() -> dict:
    if _CONFIG_PATH.exists():
        try:
            return {**_DEFAULTS, **json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(_DEFAULTS)


def save(cfg: dict) -> dict:
    merged = {**load(), **cfg}
    _CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged
