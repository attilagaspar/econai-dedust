"""Blob-inbox bulk import: folder listing/grouping, validation, job lifecycle.
The blob container is faked — no Azure involved."""
import json
import shutil
import time
import types
import uuid

import pytest

from app import inbox
from app.pipeline import create_project, PROJECTS_ROOT


class FakeBlob:
    def __init__(self, name, size=100):
        self.name, self.size = name, size


class FakeDownload:
    def __init__(self, data):
        self._data = data

    def readinto(self, fh):
        fh.write(self._data)


class FakeContainer:
    """Mimics the two ContainerClient methods the inbox uses."""
    def __init__(self, blobs):
        self._blobs = blobs          # name -> bytes

    def list_blobs(self, name_starts_with=""):
        return [FakeBlob(n, len(d)) for n, d in sorted(self._blobs.items())
                if n.startswith(name_starts_with)]

    def download_blob(self, name):
        return FakeDownload(self._blobs[name])


def _tiny_pdf() -> bytes:
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (200, 300), "white").save(buf, "PDF")
    return buf.getvalue()


@pytest.fixture()
def fake_inbox(monkeypatch):
    blobs = {
        "KE_1876/a.pdf": _tiny_pdf(),
        "KE_1876/b.pdf": _tiny_pdf(),
        "KE_1876/notes.txt": b"ignore me",
        "mihok_1874/c.pdf": _tiny_pdf(),
        "loose_root_file.pdf": _tiny_pdf(),
    }
    monkeypatch.setenv("DEDUST_INBOX_URL", "https://fake.blob.core.windows.net/inbox")
    monkeypatch.setattr(inbox, "_container_client", lambda: FakeContainer(blobs))
    inbox._JOBS.clear()
    yield blobs


def test_unconfigured_without_env(monkeypatch):
    monkeypatch.delenv("DEDUST_INBOX_URL", raising=False)
    assert not inbox.configured()


def test_list_folders_groups_and_filters(fake_inbox):
    folders = inbox.list_folders()
    names = {f["name"]: f for f in folders}
    assert set(names) == {"KE_1876", "mihok_1874"}     # root file + .txt ignored
    assert names["KE_1876"]["files"] == 2
    assert names["KE_1876"]["bytes"] > 0


def test_folder_name_validation():
    assert inbox.valid_folder("KE_1876")
    assert inbox.valid_folder("Mihok_1879")
    assert not inbox.valid_folder("../projects")
    assert not inbox.valid_folder("a/b")
    assert not inbox.valid_folder("")


def test_ingest_job_renders_and_is_idempotent(fake_inbox, tmp_path):
    ann, src = tmp_path / "annotations", tmp_path / "sources"
    job = inbox.start_ingest("KE_1876", "projX", ann, src)
    for _ in range(100):
        if job["done"]:
            break
        time.sleep(0.1)
    assert job["done"] and job["phase"] == "done", job
    assert job["pages"] == 2 and not job["errors"]
    assert (ann / "a_1.jpg").exists() and (ann / "b_1.json").exists()
    assert (src / "a.pdf").exists()                     # PDF kept for text layer
    # re-run: everything already present -> skipped, no duplicates
    inbox._JOBS.clear()
    job2 = inbox.start_ingest("KE_1876", "projX", ann, src)
    for _ in range(100):
        if job2["done"]:
            break
        time.sleep(0.1)
    assert job2["skipped"] == 2 and job2["pages"] == 0


def test_active_job_blocks_duplicates(fake_inbox, tmp_path):
    inbox._JOBS["busy_project"] = {"folder": "KE_1876", "done": False}
    assert inbox.active_job(project="busy_project")
    assert inbox.active_job(folder="KE_1876")
    assert inbox.active_job(project="other", folder="mihok_1874") is None
    inbox._JOBS.clear()


def test_ingest_endpoint_validation(client, monkeypatch):
    monkeypatch.delenv("DEDUST_INBOX_URL", raising=False)
    r = client.post("/api/inbox/ingest", json={"folder": "x", "project": "y"})
    assert r.status_code == 400 and "No inbox" in r.json()["detail"]
    monkeypatch.setenv("DEDUST_INBOX_URL", "https://fake/inbox")
    r = client.post("/api/inbox/ingest", json={"folder": "../evil", "project": "y"})
    assert r.status_code == 400 and "Invalid folder" in r.json()["detail"]
    r = client.post("/api/inbox/ingest", json={"folder": "ok", "project": "_no_such_proj"})
    assert r.status_code == 404


def test_label_presets_endpoint(client):
    d = client.get("/api/label-presets").json()["presets"]
    assert "Compass regions" in d
    assert "firm_header" in d["Compass regions"]
