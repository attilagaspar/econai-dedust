"""Named GPU-server profiles: per-instance definitions, project references by
name, stored-passphrase fallback, legacy inline block untouched."""
import json
import shutil
import uuid

import pytest

from app import gpu_profiles
from app.pipeline import create_project, PROJECTS_ROOT


@pytest.fixture()
def clean_profiles(monkeypatch, tmp_path):
    """Point the profiles file at a temp location so tests never touch the
    instance's real gpu_servers.json."""
    monkeypatch.setattr(gpu_profiles, "_PROFILES_PATH", tmp_path / "gpu_servers.json")
    yield


@pytest.fixture()
def proj(clean_profiles):
    name = f"_pytest_gpu_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", ["cell"])
    try:
        yield name
    finally:
        shutil.rmtree(PROJECTS_ROOT / name, ignore_errors=True)


def _set_cfg(name, **kv):
    p = PROJECTS_ROOT / name / "config.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.update(kv)
    p.write_text(json.dumps(cfg), encoding="utf-8")


def test_profile_crud_api(client, clean_profiles):
    r = client.post("/api/gpu-profiles", json={
        "name": "koren",
        "server": {"host": "gpu.example.org", "user": "g", "key_path": "/k",
                   "remote_path": "/home/g/ws", "passphrase": "sesame"}})
    assert r.status_code == 200
    listed = client.get("/api/gpu-profiles").json()["profiles"]
    assert listed["koren"]["host"] == "gpu.example.org"
    assert "passphrase" not in listed["koren"]          # never echoed
    assert listed["koren"]["has_passphrase"] is True
    # update without passphrase keeps the stored one
    client.post("/api/gpu-profiles", json={
        "name": "koren", "server": {"host": "gpu2.example.org", "user": "g",
                                    "key_path": "/k", "remote_path": "/home/g/ws"}})
    assert gpu_profiles.get("koren")["passphrase"] == "sesame"
    assert gpu_profiles.get("koren")["host"] == "gpu2.example.org"
    client.delete("/api/gpu-profiles/koren")
    assert gpu_profiles.get("koren") is None


def test_server_cfg_resolves_profile(client, proj):
    from app.server import _server_cfg
    gpu_profiles.save("box", {"host": "h", "user": "u", "key_path": "/k",
                              "remote_path": "/ws", "passphrase": "pw"})
    _set_cfg(proj, server_profile="box")
    srv = _server_cfg(proj)
    assert srv["host"] == "h" and srv["passphrase"] == "pw"


def test_server_cfg_missing_profile_clear_error(client, proj):
    from app.server import _server_cfg
    from fastapi import HTTPException
    _set_cfg(proj, server_profile="ghost")
    with pytest.raises(HTTPException) as e:
        _server_cfg(proj)
    assert "ghost" in e.value.detail and "not defined on this instance" in e.value.detail


def test_legacy_inline_block_still_works(client, proj):
    from app.server import _server_cfg
    _set_cfg(proj, server={"host": "old.example.org", "user": "u", "key_path": "/k",
                           "remote_path": "/ws"})
    srv = _server_cfg(proj)
    assert srv["host"] == "old.example.org"
    assert "passphrase" not in srv                       # nothing invented


def test_project_config_patch_profile(client, proj):
    r = client.patch(f"/api/project/{proj}/config", json={"server_profile": "box"})
    assert r.json()["config"]["server_profile"] == "box"
    r = client.patch(f"/api/project/{proj}/config", json={"server_profile": ""})
    assert "server_profile" not in r.json()["config"]
