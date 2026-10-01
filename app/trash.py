"""
Trash management (P9.6): one view over both soft-delete locations —
whole projects in projects/_trash/<name>_<timestamp>/ and per-project pages
in <project>/_trash_pages/ — with Restore and Delete-forever.

Restore REFUSES to clobber live work: a trashed project only restores when
no live project has its name; a trashed page only restores when the live
annotation folder has no file with its stem. This kills the last reason to
SSH into the server for file management (root-owned container files made
plain `rm` painful).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

_TS_SUFFIX = re.compile(r"_(\d{8}_\d{6})$")


def _folder_stats(d: Path) -> tuple[int, int, float]:
    """(files, bytes, latest mtime) over a tree."""
    files = size = 0
    mtime = 0.0
    for p in d.rglob("*"):
        if p.is_file():
            files += 1
            st = p.stat()
            size += st.st_size
            mtime = max(mtime, st.st_mtime)
    if not files:
        mtime = d.stat().st_mtime
    return files, size, mtime


def list_trash(projects_root: Path) -> dict:
    """Everything currently in the trash, both kinds."""
    out = {"projects": [], "pages": []}

    troot = projects_root / "_trash"
    if troot.exists():
        for d in sorted(troot.iterdir()):
            if not d.is_dir():
                continue
            m = _TS_SUFFIX.search(d.name)
            files, size, mtime = _folder_stats(d)
            out["projects"].append({
                "entry":    d.name,
                "name":     d.name[:m.start()] if m else d.name,
                "deleted":  m.group(1) if m else None,
                "files":    files, "bytes": size, "mtime": mtime,
            })

    for pdir in sorted(projects_root.iterdir()):
        if not pdir.is_dir() or pdir.name == "_trash":
            continue
        tp = pdir / "_trash_pages"
        if not tp.exists():
            continue
        stems: dict = {}
        for f in tp.rglob("*"):
            if not f.is_file():
                continue
            s = stems.setdefault(f.stem, {"files": 0, "bytes": 0, "mtime": 0.0})
            st = f.stat()
            s["files"] += 1
            s["bytes"] += st.st_size
            s["mtime"] = max(s["mtime"], st.st_mtime)
        for stem, s in sorted(stems.items()):
            out["pages"].append({"project": pdir.name, "stem": stem, **s})
    return out


def restore_project(projects_root: Path, entry: str) -> str:
    """Move projects/_trash/<entry> back to projects/<original name>.
    Refuses when a live project with that name exists — never clobber."""
    src = projects_root / "_trash" / entry
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f"No trashed project entry '{entry}'")
    m = _TS_SUFFIX.search(entry)
    name = entry[:m.start()] if m else entry
    dst = projects_root / name
    if dst.exists():
        raise FileExistsError(
            f"A live project named '{name}' exists — rename or delete it "
            f"first; restore never overwrites live work.")
    src.rename(dst)
    return name


def restore_pages(projects_root: Path, project: str,
                  stems: list[str]) -> dict:
    """Move page files back from <project>/_trash_pages into annotations/
    (predictions go back to predictions/). Per-stem refusal when a live page
    with the same stem exists."""
    pdir = projects_root / project
    tp = pdir / "_trash_pages"
    ann = pdir / "annotations"
    if not tp.exists():
        raise FileNotFoundError(f"Project '{project}' has no page trash")
    ann.mkdir(exist_ok=True)
    restored, refused, missing = [], [], []
    for stem in stems:
        files = [p for p in tp.iterdir() if p.is_file() and p.stem == stem]
        pred  = tp / "predictions" / f"{stem}.json"
        if not files and not pred.exists():
            missing.append(stem)
            continue
        live = [p for p in ann.iterdir() if p.is_file() and p.stem == stem]
        if live:
            refused.append(stem)
            continue
        for p in files:
            shutil.move(str(p), str(ann / p.name))
        if pred.exists():
            pred_dir = pdir / "predictions"
            pred_dir.mkdir(exist_ok=True)
            dst = pred_dir / pred.name
            if not dst.exists():               # live prediction wins
                shutil.move(str(pred), str(dst))
            else:
                pred.unlink()
        restored.append(stem)
    return {"restored": restored, "refused": refused, "missing": missing}


def purge_project(projects_root: Path, entry: str) -> int:
    """Delete a trashed project forever; returns bytes freed."""
    src = projects_root / "_trash" / entry
    if not src.exists():
        raise FileNotFoundError(f"No trashed project entry '{entry}'")
    _, size, _ = _folder_stats(src)
    shutil.rmtree(src)
    return size


def purge_pages(projects_root: Path, project: str,
                stems: list[str] | None = None) -> dict:
    """Delete trashed pages forever (stems=None → the whole page trash).
    Returns counts + bytes freed."""
    tp = projects_root / project / "_trash_pages"
    if not tp.exists():
        raise FileNotFoundError(f"Project '{project}' has no page trash")
    freed = purged = 0
    if stems is None:
        _, freed, _ = _folder_stats(tp)
        purged = len({p.stem for p in tp.rglob("*") if p.is_file()})
        shutil.rmtree(tp)
    else:
        for stem in stems:
            targets = [p for p in tp.iterdir() if p.is_file() and p.stem == stem]
            pred = tp / "predictions" / f"{stem}.json"
            if pred.exists():
                targets.append(pred)
            if not targets:
                continue
            for p in targets:
                freed += p.stat().st_size
                p.unlink()
            purged += 1
    return {"purged": purged, "bytes": freed}
