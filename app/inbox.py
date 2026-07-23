"""
Blob inbox — self-service bulk import (the dashboard's 📥 Inbox button).

An instance is inbox-enabled when DEDUST_INBOX_URL points at a blob container
(e.g. https://dedustbackup.blob.core.windows.net/inbox). Auth uses
DefaultAzureCredential: on the Azure VM that resolves to the machine's managed
identity (no stored keys); instances without the env var simply have no inbox
(the button doesn't render). Uploaders organize the container as one folder
per volume; the dashboard imports folder → project.

Jobs run in a background thread and are polled by the UI — deliberately NOT
an SSE stream, because a long PDF render between progress lines would trip
Cloudflare's idle timeout on the tunnel.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

PDF_IMAGE_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}

_FOLDER_RE = re.compile(r"^[A-Za-z0-9._ -]+$")   # no slashes — no traversal

_JOBS: dict = {}          # project name -> job status dict
_JOBS_LOCK = threading.Lock()


def inbox_url() -> str:
    return os.environ.get("DEDUST_INBOX_URL", "").rstrip("/")


def configured() -> bool:
    return bool(inbox_url())


def _container_client():
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import ContainerClient
    return ContainerClient.from_container_url(
        inbox_url(), credential=DefaultAzureCredential())


def valid_folder(name: str) -> bool:
    return bool(name) and bool(_FOLDER_RE.match(name))


def list_folders() -> list[dict]:
    """Top-level folders in the inbox container with importable-file counts.
    Root-level blobs (no folder) are ignored — uploaders use one folder per
    volume by convention."""
    client = _container_client()
    folders: dict = {}
    for blob in client.list_blobs():
        if "/" not in blob.name:
            continue
        top, rest = blob.name.split("/", 1)
        if not rest or Path(rest).suffix.lower() not in PDF_IMAGE_EXTS:
            continue
        f = folders.setdefault(top, {"name": top, "files": 0, "bytes": 0})
        f["files"] += 1
        f["bytes"] += blob.size or 0
    return sorted(folders.values(), key=lambda f: f["name"].lower())


def jobs_snapshot() -> dict:
    with _JOBS_LOCK:
        return {k: dict(v) for k, v in _JOBS.items()}


def active_job(project: str = None, folder: str = None):
    with _JOBS_LOCK:
        for p, j in _JOBS.items():
            if j.get("done"):
                continue
            if (project and p == project) or (folder and j.get("folder") == folder):
                return p, j
    return None


def start_ingest(folder: str, project: str, ann_dir: Path, sources_dir: Path) -> dict:
    """Spawn the download+render job. Caller has validated folder/project and
    checked active_job(). Idempotent per file: a PDF whose first page already
    exists in annotations/ is skipped (safe re-runs after interruption)."""
    job = {"folder": folder, "project": project, "phase": "listing",
           "file_i": 0, "file_n": 0, "current": "", "pages": 0,
           "skipped": 0, "errors": [], "done": False,
           "started": time.time()}
    with _JOBS_LOCK:
        _JOBS[project] = job

    def run():
        from app.page_import import _import_pdf, _save_image_file, _sanitize
        try:
            client = _container_client()
            names = [b.name for b in client.list_blobs(name_starts_with=f"{folder}/")
                     if Path(b.name).suffix.lower() in PDF_IMAGE_EXTS]
            names.sort()
            job["file_n"] = len(names)
            ann_dir.mkdir(parents=True, exist_ok=True)
            sources_dir.mkdir(parents=True, exist_ok=True)
            for i, blob_name in enumerate(names, 1):
                fname = _sanitize(Path(blob_name).name)
                job.update(phase="downloading", file_i=i, current=fname)
                dest = sources_dir / fname
                ext = dest.suffix.lower()
                stem = _sanitize(dest.stem)
                # skip if this file's pages are already in the project
                probe = ann_dir / (f"{stem}_1.json" if ext == ".pdf" else f"{stem}.json")
                if probe.exists():
                    job["skipped"] += 1
                    continue
                try:
                    if not dest.exists() or dest.stat().st_size == 0:
                        with open(dest, "wb") as fh:
                            client.download_blob(blob_name).readinto(fh)
                    job["phase"] = "rendering"
                    if ext == ".pdf":
                        pages = _import_pdf(dest, ann_dir)
                        job["pages"] += len(pages)
                    else:
                        _save_image_file(dest, ann_dir, stem)
                        job["pages"] += 1
                except Exception as e:                      # per-file failure
                    job["errors"].append(f"{fname}: {e}")
            job["phase"] = "done"
        except Exception as e:                              # listing/auth failure
            job["errors"].append(str(e))
            job["phase"] = "failed"
        finally:
            job["done"] = True
            job["finished"] = time.time()

    threading.Thread(target=run, daemon=True,
                     name=f"inbox-ingest-{project}").start()
    return job
