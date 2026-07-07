"""LLM response cache + merge-safe shape writes (P5: parallel batch LLM).

The cache makes re-runs free; merge writes make same-page parallel LLM runs
safe (whole-document writes would clobber sibling results).
"""
import json
import threading

import pytest

import app.server as srv


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "_LLM_CACHE_PATH", tmp_path / "cache.sqlite")


class _FakeClient:
    """Counts real completions; returns a canned answer."""
    def __init__(self):
        self.calls = 0
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                from types import SimpleNamespace as NS
                return NS(choices=[NS(message=NS(content="42"))],
                          usage=NS(prompt_tokens=10, completion_tokens=2))

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _msgs(text):
    return [{"role": "user", "content": text}]


def test_cache_roundtrip_and_hit():
    c = _FakeClient()
    r1 = srv._llm_complete(c, "gpt-4o-mini", _msgs("q"), 64, use_cache=True)
    assert r1.choices[0].message.content == "42" and c.calls == 1
    # identical request → served from cache, no API call
    r2 = srv._llm_complete(c, "gpt-4o-mini", _msgs("q"), 64, use_cache=True)
    assert r2.choices[0].message.content == "42" and c.calls == 1
    assert getattr(r2, "cached", False) is True
    # different prompt / model / temperature → fresh call
    srv._llm_complete(c, "gpt-4o-mini", _msgs("other"), 64, use_cache=True)
    srv._llm_complete(c, "gpt-4o", _msgs("q"), 64, use_cache=True)
    srv._llm_complete(c, "gpt-4o-mini", _msgs("q"), 64, temperature=0.2, use_cache=True)
    assert c.calls == 4


def test_cache_off_by_default():
    c = _FakeClient()
    srv._llm_complete(c, "gpt-4o-mini", _msgs("q"), 64)
    srv._llm_complete(c, "gpt-4o-mini", _msgs("q"), 64)
    assert c.calls == 2          # no caching unless asked for


def test_azure_prefix_shares_cache_with_bare_model():
    """Key uses the bare model name, so azure:gpt-5-mini == gpt-5-mini."""
    k1 = srv._llm_cache_key("azure:gpt-5-mini", _msgs("q"), 64, 0, None)
    k2 = srv._llm_cache_key("gpt-5-mini", _msgs("q"), 64, 0, None)
    assert k1 == k2


def test_azure_us_prefix_routing(monkeypatch):
    """azure-us: strips to the deployment name and demands its own env vars."""
    assert srv._bare_model("azure-us:gpt-5.4-mini-batch") == "gpt-5.4-mini-batch"
    assert srv._llm_batch_supported("azure-us:gpt-5.4-mini-batch")
    # stub the openai SDK (not installed in the test env)
    import sys, types

    class _FakeOpenAI:
        def __init__(self, api_key=None, base_url=None):
            self.api_key, self.base_url = api_key, base_url
    mod = types.ModuleType("openai")
    mod.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    # missing _US env vars → clear error naming the right variables
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT_US", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY_US", raising=False)
    import pytest as _pt
    from fastapi import HTTPException
    with _pt.raises(HTTPException) as ei:
        srv._make_llm_client("azure-us:gpt-5.4-mini-batch")
    assert "AZURE_OPENAI_ENDPOINT_US" in ei.value.detail
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT_US", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY_US", "k")
    c = srv._make_llm_client("azure-us:gpt-5.4-mini-batch")
    assert "x.openai.azure.com" in str(c.base_url)
    assert "/openai/v1" in str(c.base_url)
    # a mispasted portal "Target URI" (full path + api-version) is normalized
    # to the bare resource host, so files/batches resolve (no 404)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT_US",
        "https://x.openai.azure.com/openai/deployments/gpt-5.4-mini-batch/"
        "chat/completions?api-version=2024-10-01-preview")
    c = srv._make_llm_client("azure-us:gpt-5.4-mini-batch")
    assert str(c.base_url).rstrip("/") == "https://x.openai.azure.com/openai/v1"


def test_merge_shape_fields_concurrent(tmp_path):
    """Two 'parallel' LLM results on the same page must both survive."""
    jf = tmp_path / "p.json"
    jf.write_text(json.dumps({"shapes": [{"label": "a"}, {"label": "b"}]}),
                  encoding="utf-8")
    # simulate what parallel requests do: each read its own copy earlier,
    # then merge-writes only its own shape's fields
    ok1 = srv._merge_shape_fields(jf, 0, {"openai_output": {"response": "one"}})
    ok2 = srv._merge_shape_fields(jf, 1, {"openai_output": {"response": "two"}})
    assert ok1 and ok2
    data = json.loads(jf.read_text(encoding="utf-8"))
    assert data["shapes"][0]["openai_output"]["response"] == "one"
    assert data["shapes"][1]["openai_output"]["response"] == "two"


def test_merge_shape_fields_threaded(tmp_path):
    jf = tmp_path / "p.json"
    jf.write_text(json.dumps({"shapes": [{} for _ in range(20)]}), encoding="utf-8")
    threads = [threading.Thread(target=srv._merge_shape_fields,
                                args=(jf, i, {"openai_output": {"response": str(i)}}))
               for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    data = json.loads(jf.read_text(encoding="utf-8"))
    got = [s.get("openai_output", {}).get("response") for s in data["shapes"]]
    assert got == [str(i) for i in range(20)]   # nothing clobbered


def test_merge_shape_fields_bad_idx(tmp_path):
    jf = tmp_path / "p.json"
    jf.write_text(json.dumps({"shapes": [{}]}), encoding="utf-8")
    assert srv._merge_shape_fields(jf, 5, {"x": 1}) is False
