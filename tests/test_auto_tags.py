"""Auto-tags in the project list: all-verified (every page verified or
skipped) and has-model (a trained model is known: the training ledger, the
last Models-view listing of the project's GPU server, or a borrowed model),
with the per-file status cache and the background first scan."""
import json
import os
import shutil
import uuid

import pytest

from app import auto_tags, gpu_profiles, ssh_ops, training_meta
from app import server as srv
from app.pipeline import create_project, list_projects, PROJECTS_ROOT


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_profiles, "_PROFILES_PATH", tmp_path / "gpu_servers.json")
    monkeypatch.setattr(auto_tags, "SYNC", True)
    gpu_profiles.save("boxA", {"host": "10.0.0.9", "user": "u", "key_path": "", "remote_path": "/home/u/ws"})
    made = []

    def mk(profile="boxA"):
        name = f"_pytest_atag_{uuid.uuid4().hex[:6]}"
        create_project(name, "A", ["cell"])
        cfgp = PROJECTS_ROOT / name / "config.json"
        cfg = json.loads(cfgp.read_text(encoding="utf-8"))
        cfg["server_profile"] = profile
        cfgp.write_text(json.dumps(cfg), encoding="utf-8")
        made.append(name)
        return name, PROJECTS_ROOT / name, cfg
    try:
        yield mk
    finally:
        for n in made:
            shutil.rmtree(PROJECTS_ROOT / n, ignore_errors=True)


def _page(pdir, stem, status, head=True):
    """An annotation JSON with the flags object in the head (the usual
    labelme order) or after a large imageData (the fallback path)."""
    d = pdir / "annotations"
    d.mkdir(exist_ok=True)
    flags = {"status": status} if status else {}
    if head:
        doc = {"version": "5.0", "flags": flags, "shapes": [], "imageData": "x" * 40000}
    else:
        doc = {"version": "5.0", "imageData": "x" * 40000, "shapes": [], "flags": flags}
    (d / f"{stem}.json").write_text(json.dumps(doc), encoding="utf-8")
    return d / f"{stem}.json"


def test_status_counts_read_the_head_and_fall_back(env):
    name, pdir, cfg = env()
    _page(pdir, "p1", "verified"); _page(pdir, "p2", "skip"); _page(pdir, "p3", None)
    _page(pdir, "p4", "verified", head=False)
    (pdir / "annotations" / auto_tags.RULES_FILENAME).write_text("{}", encoding="utf-8")   # not a page
    assert auto_tags.status_counts(pdir / "annotations") == {"verified": 2, "skip": 1, "predicted": 1}


def test_status_cache_notices_an_edited_page(env):
    name, pdir, cfg = env()
    p = _page(pdir, "p1", "verified"); _page(pdir, "p2", "verified")
    assert auto_tags.status_counts(pdir / "annotations") == {"verified": 2}
    _page(pdir, "p1", "problem")
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))       # a rewrite within the same second still counts
    assert auto_tags.status_counts(pdir / "annotations") == {"verified": 1, "problem": 1}
    p.unlink()
    assert auto_tags.status_counts(pdir / "annotations") == {"verified": 1}


def test_all_verified_needs_every_page_verified_or_skipped(env):
    name, pdir, cfg = env()
    assert auto_tags.auto_tags(name, pdir, cfg) == ([], False)        # no pages: no tag
    _page(pdir, "p1", "verified"); _page(pdir, "p2", "skip")
    tags, pending = auto_tags.auto_tags(name, pdir, cfg)
    assert not pending and [t["tag"] for t in tags] == ["all-verified"]
    assert "2" in tags[0]["title"] or "1" in tags[0]["title"]
    _page(pdir, "p3", "corrected")
    assert auto_tags.auto_tags(name, pdir, cfg)[0] == []
    name2, pdir2, cfg2 = env()
    _page(pdir2, "p1", "skip")                                          # only clutter: not "verified"
    assert auto_tags.auto_tags(name2, pdir2, cfg2)[0] == []


def test_has_model_from_the_training_ledger(env):
    name, pdir, cfg = env()
    assert auto_tags.model_facts(name, pdir, cfg) is None
    rid = training_meta.ledger_append(pdir, {"mode": "train"})
    assert auto_tags.model_facts(name, pdir, cfg) is None               # still running
    training_meta.ledger_update(pdir, rid, status="finished")
    mf = auto_tags.model_facts(name, pdir, cfg)
    assert mf and "ledger" in mf["how"]
    tags, _ = auto_tags.auto_tags(name, pdir, cfg)
    assert [t["tag"] for t in tags] == ["has-model"]


def test_has_model_from_the_last_server_listing_and_forgets_on_delete(env):
    name, pdir, cfg = env()
    auto_tags.remember_models("boxA", ["other_1890", name])
    assert auto_tags.seen_models("boxA") == {"other_1890", name}
    assert "boxA" in auto_tags.model_facts(name, pdir, cfg)["how"]
    assert auto_tags.model_facts(name, pdir, {"server_profile": "boxZ"}) is None     # another server's listing says nothing
    auto_tags.forget_model("boxA", name)
    assert auto_tags.model_facts(name, pdir, cfg) is None
    auto_tags.remember_models("boxA", [])                               # a fresh listing replaces the old one
    assert auto_tags.seen_models("boxA") == set()


def test_has_model_borrowed_from_another_project(env):
    src, sdir, scfg = env()
    name, pdir, cfg = env()
    inter = pdir / "intermediate"; inter.mkdir(exist_ok=True)
    (inter / "infer.sh").write_text(f"python infer.py --weights outputs/{src}/model_final.pth\n", encoding="utf-8")
    assert auto_tags.model_of(pdir, name) == src
    assert auto_tags.model_facts(name, pdir, cfg) is None               # the source has no model either
    rid = training_meta.ledger_append(sdir, {"mode": "finetune"}); training_meta.ledger_update(sdir, rid, status="finished")
    mf = auto_tags.model_facts(name, pdir, cfg)
    assert mf["model_of"] == src and src in mf["how"]
    tags, _ = auto_tags.auto_tags(name, pdir, cfg)
    assert tags[0]["tag"] == "has-model" and tags[0]["model_of"] == src


def test_list_projects_carries_auto_tags_and_scans_in_the_background(env, monkeypatch):
    name, pdir, cfg = env()
    _page(pdir, "p1", "verified")
    auto_tags.remember_models("boxA", [name])
    monkeypatch.setattr(auto_tags, "SYNC", False)
    first = next(p for p in list_projects() if p["name"] == name)
    if first["auto_tags_pending"]:                                      # the first scan runs in the background
        assert [t["tag"] for t in first["auto_tags"]] == ["has-model"]
        auto_tags.status_counts(pdir / "annotations", block=True)
    p = next(p for p in list_projects() if p["name"] == name)
    assert p["auto_tags_pending"] is False
    assert [t["tag"] for t in p["auto_tags"]] == ["all-verified", "has-model"]


def test_models_listing_feeds_the_has_model_tag(client, env, monkeypatch):
    name, pdir, cfg = env()
    listing = f"@@GPU\n\n@@PROCS\n@@CONTAINERS\n@@DISK\n1 1%\n@@MODELS\n@@MODEL {name}|1731400000|1|1|1731400100\n{{}}\n@@END\n"
    monkeypatch.setattr(ssh_ops, "run_command", lambda *a, **k: {"ok": True, "returncode": 0, "stdout": listing, "stderr": ""})
    assert client.get("/api/models/servers/boxA/models").status_code == 200
    assert auto_tags.seen_models("boxA") == {name}
    p = next(p for p in client.get("/api/projects").json()["projects"] if p["name"] == name)
    assert [t["tag"] for t in p["auto_tags"]] == ["has-model"]
    monkeypatch.setattr(ssh_ops, "run_command", lambda *a, **k: {"ok": True, "returncode": 0, "stdout": "removed\n", "stderr": ""})
    assert client.delete(f"/api/models/servers/boxA/models/{name}").status_code == 200
    assert auto_tags.seen_models("boxA") == set()
