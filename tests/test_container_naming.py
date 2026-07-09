"""Per-workspace container naming — the durable fix for two users sharing one
GPU host clobbering each other's Docker container mount."""
from app.server import (_ns_suffix, _predict_container, _train_container,
                        _predict_workspace_root)


def test_suffix_stable_and_docker_safe():
    s = _ns_suffix("/home/gaspar/econai/koren")
    assert s == _ns_suffix("/home/gaspar/econai/koren")          # deterministic
    assert s == _ns_suffix("/home/gaspar/econai/koren/")         # trailing slash ignored
    import re
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", "x_" + s) # valid docker name tail


def test_different_workspaces_get_different_containers():
    gaspar = {"remote_path": "/home/gaspar/econai/koren"}
    matyas = {"remote_path": "/home/matyas/econai"}
    assert _predict_container(gaspar) != _predict_container(matyas)
    assert _train_container(gaspar) != _train_container(matyas)
    # names embed the base + a readable slug of the workspace tail
    assert _predict_container(gaspar).startswith("detectron_predicting_container_")
    assert "koren" in _predict_container(gaspar)
    assert "econai" in _predict_container(matyas)


def test_predict_prefers_predict_remote_path():
    srv = {"remote_path": "/home/gaspar/data",
           "predict_remote_path": "/home/gaspar/econai/koren"}
    assert _predict_workspace_root(srv) == "/home/gaspar/econai/koren"
    # predict container keys off predict_remote_path; train off remote_path
    assert _predict_container(srv) != _train_container(srv)


def test_no_srv_returns_legacy_base_name():
    # inert default: without a workspace we keep the historic single-user name
    assert _predict_container(None) == "detectron_predicting_container"
    assert _train_container(None) == "detectron_training_container"
    assert _ns_suffix("") == ""
