"""
Label palette editing — add / rename / delete a project's annotation labels
from within the app (dashboard Labels card + the editor's "new label…" entry).

Operations work from the ANNOTATIONS FOLDER (not the project name) so they
behave identically for cloud projects, local projects, and any folder that
has a project-style config.json next to it. Renames PROPAGATE: every page
JSON's shapes are rewritten, so a rename is a real refactor, not cosmetics.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

_LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")   # no spaces/commas — labels ride
                                               # in space/comma-separated inputs
_EDIT_LOCK = threading.Lock()


def valid_label(name: str) -> bool:
    return bool(name) and bool(_LABEL_RE.match(name))


def config_path_for(ann_dir: Path) -> Path | None:
    """The project config.json owning this annotations folder, if any."""
    p = ann_dir.parent / "config.json"
    return p if p.exists() else None


def _load_save(cfg_path: Path):
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    def save():
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    return cfg, save


def count_label_use(ann_dir: Path, label: str) -> tuple[int, int]:
    """(pages, shapes) still using `label`."""
    pages = shapes = 0
    for jf in ann_dir.glob("*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        n = sum(1 for s in data.get("shapes", []) if s.get("label") == label)
        if n:
            pages += 1
            shapes += n
    return pages, shapes


def add_label(ann_dir: Path, label: str) -> dict:
    with _EDIT_LOCK:
        cfg, save = _load_save(config_path_for(ann_dir))
        labels = cfg.setdefault("labels", [])
        if label not in labels:
            labels.append(label)
            save()
        return {"labels": labels, "region_labels": cfg.get("region_labels")}


def rename_label(ann_dir: Path, old: str, new: str) -> dict:
    """Rename in config AND rewrite every shape using it (atomic per page)."""
    from app.server import _write_json          # atomic write + fsync
    with _EDIT_LOCK:
        cfg, save = _load_save(config_path_for(ann_dir))
        labels = cfg.setdefault("labels", [])
        if new in labels and old in labels:
            labels.remove(old)                   # merge into existing label
        else:
            cfg["labels"] = [new if l == old else l for l in labels]
        if cfg.get("region_labels"):
            cfg["region_labels"] = [new if l == old else l
                                    for l in cfg["region_labels"]]
        pages_touched = shapes_touched = 0
        for jf in sorted(ann_dir.glob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            n = 0
            for s in data.get("shapes", []):
                if s.get("label") == old:
                    s["label"] = new
                    n += 1
            if n:
                _write_json(jf, data)
                pages_touched += 1
                shapes_touched += n
        save()
        return {"labels": cfg["labels"], "region_labels": cfg.get("region_labels"),
                "pages_touched": pages_touched, "shapes_touched": shapes_touched}


def remove_label(ann_dir: Path, label: str) -> dict:
    """Delete from config — refused while any shape still uses it."""
    with _EDIT_LOCK:
        pages, shapes = count_label_use(ann_dir, label)
        if shapes:
            raise ValueError(
                f"'{label}' is used by {shapes} shape(s) on {pages} page(s) — "
                f"rename it instead, or relabel those shapes first")
        cfg, save = _load_save(config_path_for(ann_dir))
        cfg["labels"] = [l for l in cfg.get("labels", []) if l != label]
        if cfg.get("region_labels"):
            cfg["region_labels"] = [l for l in cfg["region_labels"] if l != label]
        save()
        return {"labels": cfg["labels"], "region_labels": cfg.get("region_labels")}


def set_region_flag(ann_dir: Path, label: str, is_region: bool,
                    default_regions: list[str]) -> dict:
    """Toggle a label's membership in the project's region list (the labels the
    grouping sweep treats as page-scale objects). If the project has no
    explicit region_labels yet, initialize it from the canonical defaults that
    are actually present, so the first toggle doesn't wipe implied members."""
    with _EDIT_LOCK:
        cfg, save = _load_save(config_path_for(ann_dir))
        current = cfg.get("region_labels")
        if current is None:
            current = [l for l in cfg.get("labels", []) if l in default_regions]
        current = [l for l in current if l != label]
        if is_region:
            current.append(label)
        cfg["region_labels"] = current
        save()
        return {"labels": cfg.get("labels", []), "region_labels": current}
