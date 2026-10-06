"""Reasoning-model completion budgets: JSON (structured-output) requests get
headroom for hidden reasoning tokens; plain requests stay cheap."""
from app.server import _llm_batch_line, _is_reasoning_model


def test_json_batch_line_gets_reasoning_headroom():
    model = "azure-us:gpt-5.4-mini-batch"
    assert _is_reasoning_model(model.split(":", 1)[1]) or _is_reasoning_model(model), \
        "test premise: the batch model is a reasoning model"
    rf = {"type": "json_schema", "json_schema": {"name": "r", "schema": {}}}
    line = _llm_batch_line("s|0|-1", model, [], max_out=1200,
                           temperature=0, response_format=rf)
    assert line["body"]["max_completion_tokens"] >= 8000


def test_plain_batch_line_keeps_small_budget():
    line = _llm_batch_line("s|0|-1", "azure-us:gpt-5.4-mini-batch", [],
                           max_out=1200, temperature=0, response_format=None)
    assert line["body"]["max_completion_tokens"] == 2000
