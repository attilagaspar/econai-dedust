"""Fine-tune-from endpoint — pre-SSH validation paths (label compatibility,
missing project). The actual GPU run is exercised manually; these guard the
cheap failure modes so a bad request never gets as far as pushing data."""
import shutil
import uuid

import pytest

from app.pipeline import create_project, PROJECTS_ROOT


@pytest.fixture()
def two_projects():
    """Two throwaway projects under the real projects/ root (cleaned up)."""
    a = f"_pytest_ft_a_{uuid.uuid4().hex[:6]}"
    b = f"_pytest_ft_b_{uuid.uuid4().hex[:6]}"
    create_project(a, "A", ["cell", "header"])
    create_project(b, "A", ["cell", "header"])
    try:
        yield a, b
    finally:
        shutil.rmtree(PROJECTS_ROOT / a, ignore_errors=True)
        shutil.rmtree(PROJECTS_ROOT / b, ignore_errors=True)


def test_label_mismatch_400(client, two_projects):
    a, b = two_projects
    import json
    cfg_path = PROJECTS_ROOT / b / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["labels"] = ["totally", "different"]
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    r = client.post(f"/api/project/{a}/finetune-from/{b}", json={})
    assert r.status_code == 400
    assert "Label mismatch" in r.json()["detail"]


def test_unknown_source_404(client, two_projects):
    a, _ = two_projects
    r = client.post(f"/api/project/{a}/finetune-from/_no_such_project_", json={})
    assert r.status_code == 404
    assert "No project" in r.json()["detail"]


def test_matching_labels_reaches_server_config_check(client, two_projects):
    # with matching labels the next gate is the (unconfigured) GPU server
    a, b = two_projects
    r = client.post(f"/api/project/{a}/finetune-from/{b}", json={})
    assert r.status_code == 400
    assert "Server" in r.json()["detail"]
