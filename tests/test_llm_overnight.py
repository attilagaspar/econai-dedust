"""Overnight LLM batch lane (Batch API): submit → status → apply, with the
provider mocked — verifies the JSONL request building, job manifest, and that
apply writes results into the pages like the live path.
"""
import json
from types import SimpleNamespace as NS

import pytest

import app.server as srv


class _FakeProvider:
    """Mimics the OpenAI SDK surface the lane uses."""
    def __init__(self):
        self.uploaded = None
        self.status = "in_progress"
        self.output_lines = []
        self.cancelled = False
        outer = self

        class _Files:
            def create(self, file, purpose):
                outer.uploaded = file[1].decode("utf-8")
                assert purpose == "batch"
                return NS(id="file-in")

            def content(self, fid):
                assert fid == "file-out"
                return NS(text="\n".join(outer.output_lines))

        class _Batches:
            def create(self, input_file_id, endpoint, completion_window):
                assert input_file_id == "file-in"
                return NS(id="batch-remote-1", status="validating")

            def retrieve(self, rid):
                return NS(id=rid, status=outer.status,
                          output_file_id="file-out" if outer.status == "completed" else None,
                          request_counts=NS(completed=2, failed=0, total=2))

            def cancel(self, rid):
                outer.cancelled = True
                return NS(status="cancelling")

        self.files = _Files()
        self.batches = _Batches()


@pytest.fixture()
def provider(monkeypatch):
    p = _FakeProvider()
    monkeypatch.setattr(srv, "_make_llm_client", lambda model: p)
    return p


def _answer(cid, text):
    return json.dumps({"custom_id": cid, "error": None,
                       "response": {"status_code": 200,
                                    "body": {"choices": [{"message": {"content": text}}]}}})


def _submit(client, folder, body):
    """POST the (now SSE-streaming) submit endpoint; return parsed events."""
    r = client.post("/api/llm_batch/submit", params={"folder": str(folder)}, json=body)
    assert r.status_code == 200, r.text
    evs = [json.loads(c[6:]) for c in r.text.split("\n\n") if c.startswith("data: ")]
    return evs


def test_overnight_roundtrip(client, page_folder, provider):
    # submit two whole-cell requests (ocr mode needs no image work)
    evs = _submit(client, page_folder,
                  {"targets": [{"stem": "p1", "idx": 1}, {"stem": "p1", "idx": 2}],
                   "model": "gpt-4o-mini", "mode": "ocr", "prompt": "read"})
    done = next(e for e in evs if e["type"] == "done")
    assert done["requests"] == 2 and len(done["jobs"]) == 1
    assert done["jobs"][0]["id"] == "job-1"

    # the uploaded JSONL mirrors the live call's parameters
    lines = [json.loads(l) for l in provider.uploaded.splitlines()]
    assert lines[0]["custom_id"] == "p1|1|-1"
    assert lines[0]["body"]["model"] == "gpt-4o-mini"
    assert lines[0]["body"]["max_tokens"] == 1024
    assert lines[0]["url"] == "/v1/chat/completions"

    # job listing refreshes status from the provider
    r = client.get("/api/llm_batch/jobs", params={"folder": str(page_folder)})
    j = r.json()["jobs"][0]
    assert j["status"] == "in_progress" and j["counts"]["total"] == 2
    assert "meta" not in j          # internals not leaked to the client

    # apply refuses while unfinished
    r = client.post("/api/llm_batch/apply",
                    params={"folder": str(page_folder), "job": "job-1"})
    assert r.status_code == 400

    # complete it and apply
    provider.status = "completed"
    provider.output_lines = [_answer("p1|1|-1", "B1-cleaned"),
                             _answer("p1|2|-1", "A2-cleaned")]
    r = client.post("/api/llm_batch/apply",
                    params={"folder": str(page_folder), "job": "job-1"})
    assert r.status_code == 200, r.text
    assert r.json()["applied_cells"] == 2

    saved = json.loads((page_folder / "p1.json").read_text(encoding="utf-8"))
    assert saved["shapes"][1]["openai_output"]["response"] == "B1-cleaned"
    assert saved["shapes"][2]["openai_output"]["response"] == "A2-cleaned"
    assert saved["shapes"][1]["openai_output"]["batch_job"] == "job-1"

    # manifest marks it applied
    r = client.get("/api/llm_batch/jobs", params={"folder": str(page_folder)})
    assert r.json()["jobs"][0]["status"] == "applied"


def test_overnight_cancel(client, page_folder, provider):
    _submit(client, page_folder, {"targets": [{"stem": "p1", "idx": 1}],
            "model": "gpt-4o-mini", "mode": "ocr", "prompt": "read"})
    r = client.post("/api/llm_batch/cancel",
                    params={"folder": str(page_folder), "job": "job-1"})
    assert r.status_code == 200 and provider.cancelled
    assert r.json()["status"] == "cancelling"


def test_overnight_remove_and_clear(client, page_folder, provider):
    # two jobs; remove one, then clear finished
    _submit(client, page_folder, {"targets": [{"stem": "p1", "idx": 1}],
            "model": "gpt-4o-mini", "mode": "ocr", "prompt": "x"})
    _submit(client, page_folder, {"targets": [{"stem": "p1", "idx": 2}],
            "model": "gpt-4o-mini", "mode": "ocr", "prompt": "y"})
    # remove one by id
    r = client.post("/api/llm_batch/remove",
                    params={"folder": str(page_folder), "job": "job-1"})
    assert r.status_code == 200 and r.json()["removed"] == 1
    assert len(client.get("/api/llm_batch/jobs",
                          params={"folder": str(page_folder)}).json()["jobs"]) == 1
    # mark the remaining job failed, then clear finished
    provider.status = "failed"
    client.get("/api/llm_batch/jobs", params={"folder": str(page_folder)})  # refresh status
    r = client.post("/api/llm_batch/remove",
                    params={"folder": str(page_folder), "finished": "1"})
    assert r.status_code == 200 and r.json()["removed"] == 1
    assert client.get("/api/llm_batch/jobs",
                      params={"folder": str(page_folder)}).json()["jobs"] == []


def test_overnight_rejects_local_models(client, page_folder, provider):
    r = client.post("/api/llm_batch/submit", params={"folder": str(page_folder)},
                    json={"targets": [{"stem": "p1", "idx": 1}],
                          "model": "tk:vllm/whatever", "mode": "ocr", "prompt": "x"})
    assert r.status_code == 400
    assert "batch service" in r.json()["detail"]


def test_overnight_reasoning_model_body(client, page_folder, provider):
    _submit(client, page_folder, {"targets": [{"stem": "p1", "idx": 1}],
            "model": "azure:gpt-5-mini", "mode": "ocr", "prompt": "x"})
    body = json.loads(provider.uploaded.splitlines()[0])["body"]
    assert body["model"] == "gpt-5-mini"            # bare name in the request
    assert body["max_completion_tokens"] >= 2000    # reasoning-model params
    assert body["reasoning_effort"] == "low"
    assert "temperature" not in body


def test_submit_skips_broken_geometry_cells(client, page_folder, provider):
    # Give shape 1 a row_struct whose bands lie BELOW the cell (stale rows
    # after a resize) — this used to raise inside PIL and abort the whole
    # submit. It must now be skipped and counted, and the good cell (shape 2,
    # rows matching its bbox 300,100..500,200) must still go through.
    doc = json.loads((page_folder / "p1.json").read_text(encoding="utf-8"))
    doc["shapes"][1]["row_struct"] = {"version": 1, "origin": "t", "rows": [
        {"n": 1, "y0": 950.0, "y1": 990.0, "ocr": "", "llm": "", "human": ""}]}
    doc["shapes"][2]["row_struct"] = {"version": 1, "origin": "t", "rows": [
        {"n": 1, "y0": 110.0, "y1": 150.0, "ocr": "", "llm": "", "human": ""},
        {"n": 2, "y0": 150.0, "y1": 190.0, "ocr": "", "llm": "", "human": ""}]}
    (page_folder / "p1.json").write_text(json.dumps(doc), encoding="utf-8")

    evs = _submit(client, page_folder,
                  {"targets": [{"stem": "p1", "idx": 1}, {"stem": "p1", "idx": 2}],
                   "model": "gpt-4o-mini", "mode": "linebyline", "prompt": "read",
                   "payload": "image", "rows_source": "existing"})
    done = next(e for e in evs if e["type"] == "done")
    assert done["skipped_bad_geometry"] == 1
    assert done["requests"] == 2                       # shape 2's two rows
    lines = [json.loads(l) for l in provider.uploaded.splitlines()]
    assert all(l["custom_id"].startswith("p1|2|") for l in lines)
