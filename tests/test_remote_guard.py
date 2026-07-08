"""Phase A remote hardening: the token guard middleware + folder caging.

The guard is INERT unless ECONAI_TOKEN is set — the whole existing local
workflow (and every other test in this suite) runs with it off.  TestClient
requests arrive with client.host == "testclient", which the guard counts as
REMOTE, so these tests exercise the real remote path by monkeypatching the
env var.  Fresh TestClient instances are used so login cookies never leak
into other tests' session-scoped client.
"""
import json
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient

from app.server import app, PROJECTS_ROOT

TOKEN = "test-secret-token"


@pytest.fixture()
def guarded(monkeypatch):
    monkeypatch.setenv("ECONAI_TOKEN", TOKEN)
    return TestClient(app)


# ── inert without the token ─────────────────────────────────────────────────

def test_guard_inert_without_token(monkeypatch, page_folder):
    monkeypatch.delenv("ECONAI_TOKEN", raising=False)
    c = TestClient(app)
    r = c.get("/api/projects")
    assert r.status_code == 200
    # arbitrary absolute folder paths keep working locally (legacy behavior)
    r2 = c.get("/api/pages", params={"folder": str(page_folder)})
    assert r2.status_code == 200


# ── remote requests need the token ───────────────────────────────────────────

def test_remote_api_without_token_401(guarded):
    r = guarded.get("/api/projects")
    assert r.status_code == 401

def test_remote_html_redirects_to_login(guarded):
    r = guarded.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "login" in r.headers["location"]

def test_bearer_header_accepted(guarded):
    r = guarded.get("/api/projects",
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200

def test_query_token_accepted(guarded):
    r = guarded.get("/api/projects", params={"token": TOKEN})
    assert r.status_code == 200

def test_wrong_token_rejected(guarded):
    r = guarded.get("/api/projects",
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# ── login flow: cookie ───────────────────────────────────────────────────────

def test_login_sets_cookie_and_authorizes(guarded):
    bad = guarded.post("/api/login", json={"token": "nope"})
    assert bad.status_code == 401
    ok = guarded.post("/api/login", json={"token": TOKEN})
    assert ok.status_code == 200
    assert guarded.cookies.get("econai_token")
    r = guarded.get("/api/projects")          # cookie now rides along
    assert r.status_code == 200

def test_exempt_paths_reachable_without_token(guarded):
    assert guarded.get("/static/login.html").status_code == 200
    assert guarded.get("/static/manifest.json").status_code == 200
    assert guarded.get("/static/sw.js").status_code == 200


# ── folder caging: authorized remote sessions stay under projects/ ───────────

def test_remote_folder_outside_projects_403(guarded, page_folder):
    r = guarded.get("/api/pages", params={"folder": str(page_folder)},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 403

def test_remote_folder_inside_projects_ok(guarded):
    tmp = PROJECTS_ROOT / f"_pytest_guard_{uuid.uuid4().hex[:8]}" / "annotations"
    tmp.mkdir(parents=True)
    try:
        (tmp / "p1.json").write_text(json.dumps(
            {"shapes": [], "imagePath": "p1.jpg",
             "imageWidth": 10, "imageHeight": 10}), encoding="utf-8")
        r = guarded.get("/api/pages", params={"folder": str(tmp)},
                        headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

def test_local_host_bypasses_guard_and_cage(monkeypatch, page_folder):
    # A request from 127.0.0.1 skips both the token wall and the folder cage.
    # TestClient reports host "testclient", so simulate localhost via the
    # client kwarg Starlette exposes for exactly this purpose.
    monkeypatch.setenv("ECONAI_TOKEN", TOKEN)
    c = TestClient(app, client=("127.0.0.1", 50000))
    r = c.get("/api/projects")                      # no token supplied
    assert r.status_code == 200
    r2 = c.get("/api/pages", params={"folder": str(page_folder)})
    assert r2.status_code == 200                    # outside projects/ — still OK locally
