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
    assert by["boxB"]["azure_vm"] == {"subscription_id": "sub-1", "resource_group": "rg1", "name": "gpu-vm"}
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
