"""Send × Scope for per-row LLM: payload (image/ocr/image+ocr) and
rows_source (existing/detect/auto) on the line-by-line endpoint and the
overnight submit."""
import json
from types import SimpleNamespace as NS

import pytest

import app.server as srv


class _FakeLlm:
    def __init__(self):
        self.seen = []
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.seen.append(kw["messages"])
                return NS(choices=[NS(message=NS(content="42"))],
                          usage=NS(prompt_tokens=1, completion_tokens=1))

        self.chat = NS(completions=_Completions())


@pytest.fixture()
def fake_llm(monkeypatch):
    f = _FakeLlm()
    monkeypatch.setattr(srv, "_make_llm_client", lambda model: f)
    return f


def _add_struct(client, page_folder, idx=1):
    rows = [{"y0": 100.0, "y1": 150.0, "ocr": "111", "llm": "", "human": ""},
            {"y0": 150.0, "y1": 200.0, "ocr": "222", "llm": "", "human": ""}]
    r = client.patch("/api/page/shape/rows",
                     params={"folder": str(page_folder), "stem": "p1", "idx": idx},
                     json={"rows": rows, "origin": "manual"})
    assert r.status_code == 200


def _sse_events(text):
    return [json.loads(c[6:]) for c in text.split("\n\n")
            if c.startswith("data: ")]


def test_rows_keep_with_ocr_payload(client, page_folder, fake_llm):
    """rows_source=existing uses the stored bands; payload=ocr sends each
    row's OCR text and NO image."""
    _add_struct(client, page_folder)
    r = client.post("/api/page/shape/llm/linebyline",
                    params={"folder": str(page_folder), "stem": "p1", "idx": 1,
                            "model": "gpt-4o-mini", "payload": "ocr",
                            "rows_source": "existing", "cached": "false"},
                    json={"prompt": "clean this"})
    assert r.status_code == 200
    ev = _sse_events(r.text)
    assert ev[0]["type"] == "lines_detected" and ev[0]["count"] == 2
    assert [e["text"] for e in ev if e["type"] == "row_result"] == ["42", "42"]
    # requests were text-only and carried the per-row OCR
    assert all(isinstance(m[0]["content"], str) for m in fake_llm.seen)
    assert "111" in fake_llm.seen[0][0]["content"]
    assert "222" in fake_llm.seen[1][0]["content"]
    # results written into the structure's llm column
    saved = json.loads((page_folder / "p1.json").read_text(encoding="utf-8"))
    assert [rw["llm"] for rw in saved["shapes"][1]["row_struct"]["rows"]] == ["42", "42"]


def test_rows_keep_image_plus_ocr(client, page_folder, fake_llm):
    _add_struct(client, page_folder)
    r = client.post("/api/page/shape/llm/linebyline",
                    params={"folder": str(page_folder), "stem": "p1", "idx": 1,
                            "model": "gpt-4o-mini", "payload": "image+ocr",
                            "rows_source": "existing", "cached": "false"},
                    json={"prompt": "clean"})
    assert r.status_code == 200
    m0 = fake_llm.seen[0][0]["content"]
    assert m0[0]["type"] == "text" and "111" in m0[0]["text"]
    assert m0[1]["type"] == "image_url"          # image attached too


def test_rows_keep_requires_structure(client, page_folder, fake_llm):
    r = client.post("/api/page/shape/llm/linebyline",
                    params={"folder": str(page_folder), "stem": "p1", "idx": 2,
                            "model": "gpt-4o-mini", "rows_source": "existing",
                            "cached": "false"},
                    json={"prompt": "x"})
    assert r.status_code == 400
    assert "no internal row structure" in r.json()["detail"].lower()


def test_overnight_rows_keep_payload(client, page_folder, monkeypatch):
    """Overnight submit: existing bands + per-row OCR in the request file;
    structureless cells silently skipped under rows_source=existing."""
    _add_struct(client, page_folder)
    uploaded = {}

    class _P:
        class files:
            @staticmethod
            def create(file, purpose):
                uploaded["jsonl"] = file[1].decode("utf-8")
                return NS(id="f")
        class batches:
            @staticmethod
            def create(**kw):
                return NS(id="b", status="validating")
    monkeypatch.setattr(srv, "_make_llm_client", lambda model: _P())

    r = client.post("/api/llm_batch/submit", params={"folder": str(page_folder)},
                    json={"targets": [{"stem": "p1", "idx": 1}, {"stem": "p1", "idx": 2}],
                          "model": "gpt-4o-mini", "mode": "linebyline",
                          "prompt": "clean", "payload": "ocr",
                          "rows_source": "existing"})
    assert r.status_code == 200, r.text
    evs = [json.loads(c[6:]) for c in r.text.split("\n\n") if c.startswith("data: ")]
    done = next(e for e in evs if e["type"] == "done")
    assert done["cells"] == 1                     # idx 2 has no structure → skipped
    lines = [json.loads(l) for l in uploaded["jsonl"].splitlines()]
    assert len(lines) == 2                       # one request per row
    assert lines[0]["custom_id"] == "p1|1|0"
    assert "111" in lines[0]["body"]["messages"][0]["content"]
