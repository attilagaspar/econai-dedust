"""The Models view: GPU server profiles → the trained models on each host
(one SSH listing, parsed), the project each model belongs to, delete, and
the Azure VM behind a profile (state / start / stop through the compute
SDK, monkeypatched here)."""
import json
import shutil
import uuid

import pytest

from app import gpu_profiles, model_registry, ssh_ops
from app import server as srv
from app.pipeline import create_project, PROJECTS_ROOT


LISTING = """@@GPU
Tesla T4, 1203 MiB, 15360 MiB, 37 %
@@PROCS
4121, /opt/conda/bin/python, 1180 MiB
@@CONTAINERS
layout-train_ab12cd34ef|Up 12 minutes
layout-predict_ab12cd34ef|Exited (0) 3 days ago
@@DISK
52428800 61%
@@MODELS
@@MODEL compass_1892|1731400000|334567890|812345|1731400100
{"bbox/AP": 71.25, "bbox/AP50": 93.1, "bbox/APs": NaN, "iteration": 2999}
@@MODEL _no_such_project_q1|1720000000|334567000|400000|
@@PARTIAL gazette_1876|12000
@@END
"""


@pytest.fixture()
def profiles(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_profiles, "_PROFILES_PATH", tmp_path / "gpu_servers.json")
    gpu_profiles.save("boxA", {"host": "10.0.0.9", "user": "u", "key_path": "", "remote_path": "/home/u/ws"})
    gpu_profiles.save("boxB", {"host": "gpu.example.org", "user": "u", "key_path": "/k", "remote_path": "/srv/ws",
                               "azure_vm": {"resource_group": "rg1", "name": "gpu-vm", "subscription_id": "sub-1"}})
    yield


@pytest.fixture()
def proj(profiles):
    name = f"_pytest_mdl_{uuid.uuid4().hex[:6]}"
    create_project(name, "A", ["cell"])
    cfgp = PROJECTS_ROOT / name / "config.json"
    cfg = json.loads(cfgp.read_text(encoding="utf-8"))
    cfg["server_profile"] = "boxA"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    try:
        yield name
    finally:
        shutil.rmtree(PROJECTS_ROOT / name, ignore_errors=True)


def test_parse_listing_reads_every_section():
    d = model_registry.parse_listing(LISTING)
    assert d["complete"] is True
    assert d["gpu"] == {"name": "Tesla T4", "mem_used_mb": 1203, "mem_total_mb": 15360, "util_pct": 37}
    assert d["procs"] == [{"pid": "4121", "name": "/opt/conda/bin/python", "mem": "1180 MiB"}]
    assert [(c["name"], c["running"]) for c in d["containers"]] == [("layout-train_ab12cd34ef", True), ("layout-predict_ab12cd34ef", False)]
    assert d["disk"] == {"avail_gb": 50.0, "used_pct": 61}
    m = {x["name"]: x for x in d["models"]}
    assert set(m) == {"compass_1892", "_no_such_project_q1"}
    assert m["compass_1892"]["weights_bytes"] == 334567890 and m["compass_1892"]["folder_kb"] == 812345
    assert m["compass_1892"]["ap"] == {"AP": 71.25, "AP50": 93.1}      # NaN dropped
    assert m["compass_1892"]["trained"].startswith("20") and m["compass_1892"]["metrics_at"]
    assert m["_no_such_project_q1"]["ap"] is None and m["_no_such_project_q1"]["metrics_at"] is None
    assert d["partial"] == [{"name": "gazette_1876", "folder_kb": 12000}]
    # an interrupted listing is flagged, not mistaken for an empty server
    assert model_registry.parse_listing(LISTING.replace("@@END\n", ""))["complete"] is False


def test_listing_script_names_the_workspace_and_containers():
    s = model_registry.listing_script("/home/u/ws/", "layout-train_x", "layout-predict_x")
    assert 'R="/home/u/ws/layout-model-training"' in s
    assert "layout-train_x" in s and "layout-predict_x" in s and "@@END" in s


def test_servers_list_profiles_with_their_projects(client, proj):
    d = client.get("/api/models/servers").json()
    by = {s["name"]: s for s in d["servers"]}
    assert set(by) == {"boxA", "boxB"}
    assert by["boxA"]["projects"] == [proj] and by["boxA"]["azure_vm"] is None
    assert by["boxB"]["azure_vm"] == {"subscription_id": "sub-1", "resource_group": "rg1", "name": "gpu-vm", "ntfy_topic": None}
    assert by["boxA"]["train_container"] and by["boxA"]["host"] == "10.0.0.9"
    assert "passphrase" not in by["boxA"]


def test_models_listing_over_ssh_with_local_project_info(client, proj, monkeypatch):
    calls = []
    def fake_run(host, user, key_path, cmd, passphrase=None):
        calls.append((host, user, cmd, passphrase))
        return {"ok": True, "returncode": 0, "stdout": LISTING.replace("compass_1892", proj), "stderr": ""}
    monkeypatch.setattr(ssh_ops, "run_command", fake_run)
    d = client.get("/api/models/servers/boxA/models").json()
    assert calls and calls[0][0] == "10.0.0.9" and "/home/u/ws/layout-model-training" in calls[0][2]
    assert d["profile"] == "boxA" and d["host"] == "10.0.0.9" and d["complete"]
    m = {x["name"]: x for x in d["models"]}
    assert m[proj]["local"]["project"] is True and m[proj]["local"]["this_server"] is True
    assert m["_no_such_project_q1"]["local"] == {"project": False}        # a model whose project is not on this instance
    assert d["partial"][0]["name"] == "gazette_1876"
    # an unreachable host is a 502 with the reason, not an empty server
    monkeypatch.setattr(ssh_ops, "run_command", lambda *a, **k: {"ok": False, "error": "timed out"})
    r = client.get("/api/models/servers/boxA/models")
    assert r.status_code == 503 and "timed out" in r.json()["detail"]
    assert client.get("/api/models/servers/nope/models").status_code == 404


def test_delete_model_removes_the_output_folder(client, profiles, monkeypatch):
    seen = {}
    def fake_run(host, user, key_path, cmd, passphrase=None):
        seen["cmd"] = cmd
        return {"ok": True, "returncode": 0, "stdout": "removed\n", "stderr": ""}
    monkeypatch.setattr(ssh_ops, "run_command", fake_run)
    r = client.delete("/api/models/servers/boxA/models/compass_1892")
    assert r.status_code == 200 and r.json()["removed"] == "/home/u/ws/layout-model-training/outputs/compass_1892"
    assert "rm -rf '/home/u/ws/layout-model-training/outputs/compass_1892'" in seen["cmd"]
    assert client.delete("/api/models/servers/boxA/models/..").status_code in (400, 404)
    assert client.delete("/api/models/servers/boxA/models/a%2Fb").status_code in (400, 404)
    monkeypatch.setattr(ssh_ops, "run_command", lambda *a, **k: {"ok": True, "stdout": "missing\n"})
    assert client.delete("/api/models/servers/boxA/models/gone").status_code == 404


class _FakeVms:
    def __init__(self, state):
        self.state, self.calls = state, []
    def instance_view(self, rg, name):
        self.calls.append(("view", rg, name))
        class S: pass
        s1, s2 = S(), S(); s1.code = "ProvisioningState/succeeded"; s2.code = f"PowerState/{self.state}"
        class IV: statuses = [s1, s2]
        return IV()
    def begin_start(self, rg, name): self.calls.append(("start", rg, name))
    def begin_deallocate(self, rg, name): self.calls.append(("deallocate", rg, name))


def test_azure_vm_state_start_stop_and_settings(client, profiles, monkeypatch):
    vms = _FakeVms("deallocated")
    class C: virtual_machines = vms
    monkeypatch.setattr(srv, "_azure_compute_client", lambda sub: C())
    d = client.get("/api/models/servers/boxB/vm").json()
    assert d == {"ok": True, "state": "deallocated", "vm": "gpu-vm", "resource_group": "rg1"}
    assert client.post("/api/models/servers/boxB/vm/start").json()["state"] == "starting"
    assert client.post("/api/models/servers/boxB/vm/stop").json()["state"] == "deallocating"
    assert vms.calls == [("view", "rg1", "gpu-vm"), ("start", "rg1", "gpu-vm"), ("deallocate", "rg1", "gpu-vm")]
    # a profile without an Azure VM
    assert client.get("/api/models/servers/boxA/vm").status_code == 400
    # settings: set, then forget
    r = client.post("/api/models/servers/boxA/azure", json={"resource_group": "rg9", "name": "vm9"})
    assert r.status_code == 200 and gpu_profiles.get("boxA")["azure_vm"] == {"resource_group": "rg9", "name": "vm9"}
    assert client.post("/api/models/servers/boxA/azure", json={"name": "only-a-name"}).status_code == 400
    client.post("/api/models/servers/boxA/azure", json={})
    assert "azure_vm" not in gpu_profiles.get("boxA")
    assert gpu_profiles.get("boxA")["host"] == "10.0.0.9"            # the rest of the profile untouched
    # an authorization failure names the role to grant
    class Bad:
        class virtual_machines:
            @staticmethod
            def instance_view(rg, name): raise RuntimeError("(AuthorizationFailed) The client does not have authorization")
    monkeypatch.setattr(srv, "_azure_compute_client", lambda sub: Bad())
    r = client.get("/api/models/servers/boxB/vm")
    assert r.status_code == 503 and "Virtual Machine Contributor" in r.json()["detail"]


def test_power_state_helper():
    assert model_registry.power_state([{"code": "PowerState/running"}]) == "running"
    assert model_registry.power_state([]) == "unknown"


def test_servers_list_carries_each_projects_training_facts(client, proj):
    summ = PROJECTS_ROOT / proj / "intermediate" / "train_summary.json"
    summ.parent.mkdir(parents=True, exist_ok=True)
    summ.write_text(json.dumps({"n_train_pages": 42, "n_test_pages": 5, "model_labels": ["a", "b"],
                                "training_sources": ["other_1890"],
                                "sources": {proj: {"n_train_pages": 30}, "other_1890": {"n_train_pages": 12}}}), encoding="utf-8")
    d = client.get("/api/models/servers").json()
    s = {x["name"]: x for x in d["servers"]}["boxA"]
    assert s["project_info"][proj] == {"n_train_pages": 42, "n_test_pages": 5, "n_labels": 2,
                                       "training_sources": ["other_1890"],
                                       "sources": {proj: 30, "other_1890": 12}}
    # a project that never prepared has no facts, not an error
    summ.unlink()
    d = client.get("/api/models/servers").json()
    assert {x["name"]: x for x in d["servers"]}["boxA"]["project_info"][proj] == {}


def test_vm_history_logs_requests_and_observed_changes(client, profiles, monkeypatch):
    from app import vm_events
    from datetime import datetime
    vms = _FakeVms("running")
    class C: virtual_machines = vms
    monkeypatch.setattr(srv, "_azure_compute_client", lambda sub: C())
    assert client.get("/api/models/servers/boxB/vm/history").json()["events"] == []
    client.get("/api/models/servers/boxB/vm")          # observed: running (dated by the provisioning time when given)
    client.get("/api/models/servers/boxB/vm")          # same state again: not logged twice
    client.post("/api/models/servers/boxB/vm/stop")    # a request from the dashboard
    vms.state = "deallocated"
    client.get("/api/models/servers/boxB/vm")          # observed: deallocated, closes the running span
    ev = client.get("/api/models/servers/boxB/vm/history").json()["events"]
    assert [(e["event"], e["source"]) for e in ev] == [("deallocated", "observed"), ("stop requested", "dashboard"), ("running", "observed")]
    assert ev[0]["hours"] is not None and ev[0]["hours"] >= 0
    assert all(e["profile"] == "boxB" and len(e["at"]) == 19 for e in ev)
    # the log lives next to the profile store, one JSON line per event
    assert (gpu_profiles._PROFILES_PATH.parent / "gpu_vm_events.jsonl").read_text(encoding="utf-8").count("\n") == 3
    # a running span still open reports its length so far; Azure's own time stamps an observed change
    vm_events.observe("boxB", "running", at=datetime(2026, 11, 22, 8, 0, 0))
    r = vm_events.recent("boxB")[0]
    assert r["at"] == "2026-11-22T08:00:00" and r["hours_so_far"] > 0
    assert client.get("/api/models/servers/nope/vm/history").status_code == 404


def test_alive_probe_azure_state_and_ssh_knock(client, profiles, monkeypatch):
    vms = _FakeVms("running")
    class C: virtual_machines = vms
    monkeypatch.setattr(srv, "_azure_compute_client", lambda sub: C())
    knocked = []
    monkeypatch.setattr(srv, "_ssh_port_open", lambda host, timeout=2.0: knocked.append(host) or host == "10.0.0.9")
    d = client.get("/api/models/alive").json()
    by = {s["name"]: s for s in d["servers"]}
    assert by["boxB"] == {"name": "boxB", "alive": True, "state": "running", "how": "azure"}
    assert by["boxA"] == {"name": "boxA", "alive": True, "state": "running", "how": "ssh"} and knocked == ["10.0.0.9"]
    assert d["any_alive"] is True
    vms.state = "deallocated"
    monkeypatch.setattr(srv, "_ssh_port_open", lambda host, timeout=2.0: False)
    d = client.get("/api/models/alive").json()
    assert d["any_alive"] is False and {s["alive"] for s in d["servers"]} == {False}
    # an Azure error is reported on that server, not raised for all
    monkeypatch.setattr(srv, "_azure_compute_client", lambda sub: (_ for _ in ()).throw(RuntimeError("boom")))
    d = client.get("/api/models/alive").json()
    assert {s["name"]: s["alive"] for s in d["servers"]} == {"boxA": False, "boxB": None}
    assert "boom" in {s["name"]: s for s in d["servers"]}["boxB"]["error"]


def test_training_facts_fall_back_to_the_coco_export_and_name_a_borrowed_model(client, proj):
    inter = PROJECTS_ROOT / proj / "intermediate"
    inter.mkdir(parents=True, exist_ok=True)
    # a project prepared before the training summary existed: only the COCO export and the scripts
    inter.joinpath("annotations.json").write_text(json.dumps({"images": [{"id": i, "file_name": f"p{i}.jpg"} for i in range(3)],
                                                             "annotations": [], "categories": []}), encoding="utf-8")
    inter.joinpath("train.sh").write_text("python train.py --annotation-path /workspace/other_1876/annotations.json\n"
                                          "rm -f /workspace/layout-model-training/outputs/other_1876/fast_rcnn_R_50_FPN_3x/*.pth\n", encoding="utf-8")
    t = srv._project_training_facts(proj)
    assert t["n_train_pages"] == 3 and t["from"] == "coco" and len(t["prepared_at"]) == 10
    assert t["model_of"] == "other_1876"            # a clone that kept its source's scripts uses that model
    # scripts pointing at the project's own output folder: no model_of
    inter.joinpath("train.sh").write_text(f"rm -f /workspace/layout-model-training/outputs/{proj}/fast_rcnn_R_50_FPN_3x/*.pth\n", encoding="utf-8")
    assert "model_of" not in srv._project_training_facts(proj)
    d = client.get("/api/models/servers").json()
    assert {x["name"]: x for x in d["servers"]}["boxA"]["project_info"][proj]["n_train_pages"] == 3


def test_failed_start_is_named_with_azure_reason(client, profiles, monkeypatch):
    class S: pass
    failed = S(); failed.code = "ProvisioningState/failed/AllocationFailed"; failed.time = None
    failed.message = "Allocation failed. We do not have sufficient capacity for the requested VM size in this region."
    assert model_registry.power_state([failed]) == "failed"
    assert model_registry.provisioning_message([failed]).startswith("AllocationFailed: Allocation failed")
    assert model_registry.provisioning_message([{"code": "ProvisioningState/succeeded"}]) is None
    class IV: statuses = [failed]
    class Vms:
        @staticmethod
        def instance_view(rg, name): return IV()
    class C: virtual_machines = Vms
    monkeypatch.setattr(srv, "_azure_compute_client", lambda sub: C())
    d = client.get("/api/models/servers/boxB/vm").json()
    assert d["state"] == "failed" and "AllocationFailed" in d["message"] and "no machine of this size" in d["hint"]
    ev = client.get("/api/models/servers/boxB/vm/history").json()["events"]
    assert ev[0]["event"] == "failed" and ev[0]["note"].startswith("AllocationFailed")
    client.get("/api/models/servers/boxB/vm")                 # the same failure again is not logged twice
    assert len(client.get("/api/models/servers/boxB/vm/history").json()["events"]) == 1


class _KeepVms:
    """begin_start fails `fail_n` times with the given error text, then the VM runs."""
    def __init__(self, fail_n, err="(AllocationFailed) Allocation failed. We do not have sufficient capacity"):
        self.fail_n, self.err, self.state, self.starts = fail_n, err, "deallocated", 0
    def instance_view(self, rg, name):
        class S: pass
        s = S(); s.code = f"PowerState/{self.state}"; s.time = None
        class IV: statuses = [s]
        return IV()
    def begin_start(self, rg, name):
        self.starts += 1
        if self.starts <= self.fail_n:
            raise RuntimeError(self.err)
        self.state = "running"
        class P:
            @staticmethod
            def result(): return None
        return P()


def _status(**kw):
    d = {"active": True, "attempts": 0, "interval_min": 0, "max_hours": 1, "last_try": None, "last_error": None,
         "next_try": None, "result": None, "finished_at": None, "max_hours": 12}
    d.update(kw); return d


def test_keep_trying_loop_retries_capacity_errors_then_notifies(client, profiles, monkeypatch):
    import threading, time as _t
    pushed = []
    monkeypatch.setattr(srv, "_ntfy_push", lambda topic, title, body: pushed.append((topic, title, body)) or True)
    vms = _KeepVms(2)
    class C: virtual_machines = vms
    monkeypatch.setattr(srv, "_azure_compute_client", lambda sub: C())
    st = _status()
    srv._vm_keep_trying_loop("boxB", "sub-1", "rg1", "gpu-vm", 0, _t.time() + 3600, threading.Event(), st, "my-topic")
    assert st["result"] == "running" and st["attempts"] == 3 and st["active"] is False and vms.starts == 3
    assert pushed and pushed[0][0] == "my-topic" and "attempt 3" in pushed[0][2]
    ev = [e["event"] for e in client.get("/api/models/servers/boxB/vm/history").json()["events"]]
    assert ev[0].startswith("keep trying: running") and "running" in ev
    # a non-capacity error stops the loop at once
    vms = _KeepVms(5, err="(AuthorizationFailed) The client does not have authorization")
    C.virtual_machines = vms
    st = _status()
    srv._vm_keep_trying_loop("boxB", "sub-1", "rg1", "gpu-vm", 0, _t.time() + 3600, threading.Event(), st, "")
    assert st["result"] == "error" and st["attempts"] == 1 and "AuthorizationFailed" in st["last_error"]
    # the deadline
    vms = _KeepVms(5); C.virtual_machines = vms
    st = _status()
    srv._vm_keep_trying_loop("boxB", "sub-1", "rg1", "gpu-vm", 60, _t.time() + 1, threading.Event(), st, "")
    assert st["result"] == "gave_up" and st["attempts"] == 1
    # a VM that already runs needs no start
    vms = _KeepVms(0); vms.state = "running"; C.virtual_machines = vms
    st = _status()
    srv._vm_keep_trying_loop("boxB", "sub-1", "rg1", "gpu-vm", 0, _t.time() + 3600, threading.Event(), st, "")
    assert st["result"] == "running" and vms.starts == 0


def test_keep_trying_endpoints_start_report_and_stop(client, profiles, monkeypatch):
    vms = _KeepVms(10 ** 6)                       # never gets capacity
    class C: virtual_machines = vms
    monkeypatch.setattr(srv, "_azure_compute_client", lambda sub: C())
    monkeypatch.setattr(srv, "_ntfy_push", lambda *a: False)
    srv._vm_waiters.clear()
    assert client.get("/api/models/servers/boxB/vm/keep_trying").json()["keep_trying"] is None
    assert client.delete("/api/models/servers/boxB/vm/keep_trying").json()["keep_trying"] is None
    r = client.post("/api/models/servers/boxB/vm/keep_trying", json={"interval_min": 1, "max_hours": 1})
    assert r.status_code == 200 and r.json()["keep_trying"]["active"] is True
    assert client.post("/api/models/servers/boxB/vm/keep_trying").status_code == 409
    import time as _t
    for _ in range(50):                            # the first attempt has failed and the loop waits for the next
        kt = client.get("/api/models/servers/boxB/vm").json().get("keep_trying") or {}
        if kt.get("attempts", 0) >= 1 and kt.get("next_try"):
            break
        _t.sleep(0.05)
    assert kt["active"] and kt["attempts"] == 1 and "AllocationFailed" in kt["last_error"]
    d = client.delete("/api/models/servers/boxB/vm/keep_trying").json()["keep_trying"]
    assert d["active"] is False and d["result"] == "stopped"
    srv._vm_waiters["boxB"]["thread"].join(5)
    assert not srv._vm_waiters["boxB"]["thread"].is_alive()
    ev = [e["event"] for e in client.get("/api/models/servers/boxB/vm/history").json()["events"]]
    assert ev[0] == "keep trying: stopped" and ev[-1].startswith("keep trying started")
    # a profile without an Azure VM, and the ntfy topic kept in the settings
    assert client.post("/api/models/servers/boxA/vm/keep_trying").status_code == 400
    client.post("/api/models/servers/boxA/azure", json={"resource_group": "rg9", "name": "vm9", "ntfy_topic": "dedust-gpu-x"})
    assert gpu_profiles.get("boxA")["azure_vm"]["ntfy_topic"] == "dedust-gpu-x"
    assert {s["name"]: s for s in client.get("/api/models/servers").json()["servers"]}["boxA"]["azure_vm"]["ntfy_topic"] == "dedust-gpu-x"
    srv._vm_waiters.clear()


# ── storage on the server: the workspace rows of the listing, and bulk removal ──

WORKSPACE = """@@WORKSPACE
@@WS gend_1900|2400000|812|900|812|40|0|2401000|1200000|3
@@WS compass_1892|500000|200|0|0|0|30000|531000|800000|2
@@WS cfgonly|0|0|0|0|0|0|0|0|1
@@END
"""


def test_listing_reports_each_projects_footprint(client, proj, monkeypatch):
    out = model_registry.parse_listing(LISTING.replace("@@END\n", WORKSPACE))
    ws = {w["name"]: w for w in out["workspace"]}
    assert ws["gend_1900"]["images_kb"] == 2400000 and ws["gend_1900"]["images_n"] == 812
    assert ws["gend_1900"]["predictions_n"] == 812 and ws["gend_1900"]["model_kb"] == 1200000
    assert ws["gend_1900"]["other_kb"] == 2401000 - 2400000 - 900 - 40          # whatever else sits in the data folder
    assert ws["compass_1892"]["eval_kb"] == 30000 and ws["cfgonly"]["scripts_n"] == 1
    assert out["complete"] and model_registry.parse_listing(LISTING)["workspace"] == []   # an older listing: no rows
    script = model_registry.listing_script("/home/u/ws", "t", "p")
    assert "@@WORKSPACE" in script and '"$W"/*/' in script and 'layout-model-training" ] && continue' in script
    # the endpoint says which rows still have a project on this instance
    monkeypatch.setattr(ssh_ops, "run_command", lambda *a, **k: {"ok": True, "returncode": 0, "stderr": "",
                                                                 "stdout": LISTING.replace("@@END\n", WORKSPACE).replace("compass_1892", proj)})
    d = client.get("/api/models/servers/boxA/models").json()
    ws = {w["name"]: w for w in d["workspace"]}
    assert ws[proj]["local"] == {"project": True, "server_profile": "boxA"}
    assert ws["gend_1900"]["local"] == {"project": False}


def test_remove_paths_promote_a_whole_data_folder_and_refuse_bad_names():
    rp = model_registry.remove_paths
    exact, globs = rp("/ws", ["/ws"], "gend_1900", ["images", "predictions"])
    assert exact == ["/ws/gend_1900/images", "/ws/gend_1900/predictions"] and globs == []
    exact, globs = rp("/ws", ["/ws"], "gend_1900", ["images", "predictions", "parents", "eval"])
    assert exact == ["/ws/gend_1900"]                                       # all four parts → the whole data folder
    exact, globs = rp("/ws", ["/ws", "/pws"], "gend_1900", ["model"])
    assert "/ws/layout-model-training/outputs/gend_1900" in exact and "/pws/layout-model-training/configs/gend_1900" in exact
    assert "/ws/layout-model-training/scripts/gend_1900_infer.sh" in exact
    assert globs == ["/ws/layout-model-training/scripts/gend_1900_finetune_from_*.sh",
                     "/pws/layout-model-training/scripts/gend_1900_finetune_from_*.sh"]
    for bad in ("..", "a/b", "layout-model-training", "", "x y"):
        with pytest.raises(ValueError):
            rp("/ws", ["/ws"], bad, ["images"])
    with pytest.raises(ValueError):
        rp("/ws", ["/ws"], "ok", ["weights"])
    with pytest.raises(ValueError):
        rp("/ws", ["/ws"], "ok", [])
    s = model_registry.remove_script("/ws", ["/ws"], [{"project": "gend_1900", "kinds": ["images"]}])
    assert "rm -rf /ws/gend_1900/images" in s and "rmdir /ws/gend_1900" in s and s.rstrip().endswith('echo "@@DONE"')


def test_bulk_remove_endpoint_removes_everything_selected_in_one_call(client, profiles, monkeypatch, tmp_path):
    from app import auto_tags
    auto_tags.remember_models("boxA", ["gend_1900", "compass_1892"])
    seen = {}
    def fake_run(host, user, key_path, cmd, passphrase=None):
        seen["cmd"] = cmd
        return {"ok": True, "returncode": 0, "stderr": "", "stdout":
                "@@OK /home/u/ws/gend_1900\n@@OK /home/u/ws/layout-model-training/outputs/gend_1900\n"
                "@@MISSING /home/u/ws/layout-model-training/configs/gend_1900\n@@OK /home/u/ws/compass_1892/images\n@@DONE\n"}
    monkeypatch.setattr(ssh_ops, "run_command", fake_run)
    r = client.post("/api/models/servers/boxA/remove", json={"items": [
        {"project": "gend_1900", "kinds": ["images", "predictions", "parents", "eval", "model"]},
        {"project": "compass_1892", "kinds": ["images"]}]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and len(d["removed"]) == 3 and d["missing"] == ["/home/u/ws/layout-model-training/configs/gend_1900"]
    assert "rm -rf /home/u/ws/gend_1900 " in seen["cmd"] and "rm -rf /home/u/ws/compass_1892/images" in seen["cmd"]
    assert "/home/u/ws/compass_1892/predictions" not in seen["cmd"]
    assert auto_tags.seen_models("boxA") == {"compass_1892"}              # the model went; the other project's stays
    # bad input is refused before anything reaches the server
    seen.clear()
    assert client.post("/api/models/servers/boxA/remove", json={"items": []}).status_code == 400
    assert client.post("/api/models/servers/boxA/remove", json={"items": [{"project": "../etc", "kinds": ["images"]}]}).status_code == 400
    assert client.post("/api/models/servers/boxA/remove", json={"items": [{"project": "ok", "kinds": ["everything"]}]}).status_code == 400
    assert client.post("/api/models/servers/nope/remove", json={"items": [{"project": "ok", "kinds": ["images"]}]}).status_code == 404
    assert not seen
    monkeypatch.setattr(ssh_ops, "run_command", lambda *a, **k: {"ok": False, "error": "timed out"})
    assert client.post("/api/models/servers/boxA/remove", json={"items": [{"project": "ok", "kinds": ["images"]}]}).status_code == 503
