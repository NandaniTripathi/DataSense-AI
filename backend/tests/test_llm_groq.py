"""Groq integration + AI Analyst tests.

NO real Groq calls happen here: the OpenAI-compatible client is replaced by a scripted fake, so
these tests cover our request/response translation, error mapping and the whole agent loop
(real dataset upload, real SQL validation and execution) deterministically and without a key.
Real-API verification is the separate manual script:  python -m scripts.check_groq
"""
import io
import json
from types import SimpleNamespace as NS

import httpx
import openai
import pandas as pd
import pytest

from app.core.config import get_settings
from app.services import llm as llm_mod
from app.services import query_engine
from app.services.llm import GroqLLM, LLMError, LLMNotConfigured, LLMResponse, ToolChoiceError, get_llm
from app.services.prompts import TOOLS
from app.services.samples import generate_sales_demo

TOOL_MSG = "Tool choice is required, but model did not call a tool"
USER_Q = [{"role": "user", "content": "q"}]


# --------------------------------------------------------------------------- helpers
def _resp(content=None, tool_calls=None, finish="stop", prompt=10, completion=5):
    msg = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(message=msg, finish_reason=finish)],
              usage=NS(prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=None))


def _call(cid, name, args):
    raw = args if isinstance(args, str) else json.dumps(args)
    return NS(id=cid, type="function", function=NS(name=name, arguments=raw))


def _sql(sql, cid="c1"):
    return _resp(finish="tool_calls", tool_calls=[_call(cid, "run_sql", {"sql": sql, "purpose": "test"})])


def _final(summary="Done.", qid=1, cid="cf", **extra):
    args = {"summary": summary, "follow_up_questions": ["A?", "B?", "C?"], **extra}
    if qid is not None:
        args["display_query_id"] = qid
    return _resp(finish="tool_calls", tool_calls=[_call(cid, "final_answer", args)])


COUNT_SQL = "SELECT COUNT(*) AS row_count FROM data"


class FakeClient:
    """Mimics openai.OpenAI(): .chat.completions.create(**kwargs), scripted in order."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests: list[dict] = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _http_error(cls, status, message="boom", body=None):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return cls(message, response=httpx.Response(status, request=req), body=body)


def _tool_failure(failed_generation=None):
    body = {"code": "tool_use_failed", "failed_generation": failed_generation} if failed_generation else None
    return _http_error(openai.BadRequestError, 400, TOOL_MSG, body=body)


def _profile(dataset_id):
    from app.db.session import get_db
    from app.routers.datasets import get_dataset_or_404

    db = next(get_db())
    try:
        return get_dataset_or_404(db, dataset_id).profile
    finally:
        db.close()


def _run(steps, dataset_id, question="q", history=None, model="m"):
    fake = FakeClient(*steps)
    out = query_engine.answer_question(GroqLLM("k", model, client=fake), dataset_id,
                                       _profile(dataset_id), history or [], question)
    return out, fake


def _assert_rows(actual, expected):
    assert len(actual) == len(expected)
    for a_row, e_row in zip(actual, expected):
        assert len(a_row) == len(e_row)
        for a, e in zip(a_row, e_row):
            assert (a == pytest.approx(e)) if isinstance(e, float) else (a == e)


def _json_answer(summary="There are 4,000 rows.", **extra):
    return json.dumps({"summary": summary, "chart": None, "follow_up_questions": ["A?", "B?", "C?"], **extra})


# --------------------------------------------------------------------------- translation (pure)
def test_tools_are_converted_to_openai_function_format():
    out = llm_mod.tools_to_openai(TOOLS)
    assert [t["function"]["name"] for t in out] == ["run_sql", "final_answer"]
    assert all(t["type"] == "function" for t in out)
    assert out[0]["function"]["parameters"]["required"] == ["sql", "purpose"]
    assert "summary" in out[1]["function"]["parameters"]["required"]


def test_messages_round_trip_tool_calls_and_results():
    msgs = [
        {"role": "user", "content": "total revenue?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "call_1", "name": "run_sql", "input": {"sql": "SELECT 1", "purpose": "p"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "rows: 1"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_2", "name": "run_sql", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_2", "is_error": True, "content": "bad sql"}]},
    ]
    out = llm_mod.messages_to_openai("SYS", msgs)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "total revenue?"}
    a = out[2]
    assert a["role"] == "assistant" and a["content"] == "Let me check."
    assert json.loads(a["tool_calls"][0]["function"]["arguments"]) == {"sql": "SELECT 1", "purpose": "p"}
    assert out[3] == {"role": "tool", "tool_call_id": "call_1", "name": "run_sql", "content": "rows: 1"}
    assert out[4]["content"] is None and out[4]["tool_calls"][0]["id"] == "call_2"
    assert out[5]["role"] == "tool" and out[5]["content"].startswith("ERROR")


def test_empty_assistant_turn_is_not_sent():
    out = llm_mod.messages_to_openai("S", [
        {"role": "user", "content": "q"}, {"role": "assistant", "content": []},
        {"role": "user", "content": "again"}])
    assert [m["role"] for m in out] == ["system", "user", "user"]


def test_response_parsing_text_tool_calls_usage_and_malformed_arguments():
    r = llm_mod.response_from_openai(_resp(
        content="thinking", finish="tool_calls", prompt=100, completion=20,
        tool_calls=[_call("c1", "run_sql", {"sql": "SELECT 1", "purpose": "x"})]))
    assert isinstance(r, LLMResponse)
    assert r.blocks[0] == {"type": "text", "text": "thinking"}
    assert r.blocks[1] == {"type": "tool_use", "id": "c1", "name": "run_sql",
                           "input": {"sql": "SELECT 1", "purpose": "x"}}
    assert r.stop_reason == "tool_use"
    assert r.usage["input_tokens"] == 100 and r.usage["output_tokens"] == 20
    assert set(r.usage) == {"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"}
    for bad in ("{not json", "", "[1, 2]", None):
        r = llm_mod.response_from_openai(_resp(finish="tool_calls", tool_calls=[_call("c2", "final_answer", bad or "")]))
        assert r.blocks[0]["input"] == {}


# --------------------------------------------------------------------------- client: requests
def test_successful_text_response():
    r = GroqLLM("k", "m", client=FakeClient(_resp(content="Hello"))).create("s", USER_Q)
    assert r.blocks == [{"type": "text", "text": "Hello"}] and r.stop_reason == "end_turn"


def test_successful_tool_call_request_and_response():
    fake = FakeClient(_sql("SELECT 1"))
    r = GroqLLM("k", "m", client=fake).create("SYS", USER_Q, tools=TOOLS, max_tokens=123)
    req = fake.requests[0]
    assert req["model"] == "m" and req["max_tokens"] == 123 and req["tool_choice"] == "auto"
    assert req["messages"][0] == {"role": "system", "content": "SYS"}
    assert [t["function"]["name"] for t in req["tools"]] == ["run_sql", "final_answer"]
    assert r.blocks[0]["type"] == "tool_use" and r.blocks[0]["input"]["sql"] == "SELECT 1"


def test_force_tool_requests_required_and_json_mode_sends_no_tools():
    fake = FakeClient(_resp(content="x"), _resp(content="{}"))
    g = GroqLLM("k", "m", client=fake)
    g.create("s", USER_Q, tools=TOOLS, force_tool=True)
    g.create("s", USER_Q, tools=TOOLS, json_mode=True)
    forced, js = fake.requests
    assert forced["tool_choice"] == "required"
    assert js["response_format"] == {"type": "json_object"} and "tools" not in js and "tool_choice" not in js


def test_gpt_oss_gets_short_reasoning_and_other_models_do_not():
    a, b = FakeClient(_resp(content="x")), FakeClient(_resp(content="x"))
    GroqLLM("k", "openai/gpt-oss-120b", client=a).create("s", USER_Q)
    GroqLLM("k", "qwen/some-model", client=b).create("s", USER_Q)
    assert a.requests[0]["extra_body"] == {"reasoning_effort": "low"}
    assert "extra_body" not in b.requests[0]


# --------------------------------------------------------------------------- client: errors
@pytest.mark.parametrize("exc_factory, status, needle", [
    (lambda: _http_error(openai.AuthenticationError, 401), 502, "GROQ_API_KEY"),
    (lambda: _http_error(openai.PermissionDeniedError, 403), 502, "GROQ_MODEL"),
    (lambda: _http_error(openai.NotFoundError, 404), 502, "GROQ_MODEL"),
    (lambda: _http_error(openai.RateLimitError, 429), 429, "rate limiting"),
    (lambda: _http_error(openai.BadRequestError, 400, "model does not support tools"), 502, "tool calling"),
    (lambda: _http_error(openai.InternalServerError, 500), 502, "500"),
    (lambda: openai.APIConnectionError(request=httpx.Request("POST", "https://x")), 502, "Could not reach"),
    (lambda: openai.APITimeoutError(request=httpx.Request("POST", "https://x")), 502, "timed out"),
])
def test_error_mapping(exc_factory, status, needle):
    g = GroqLLM("gsk_secret", "m", client=FakeClient(exc_factory()))
    with pytest.raises(LLMError) as ei:
        g.create("s", USER_Q)
    assert ei.value.status == status and needle in str(ei.value)
    assert "gsk_secret" not in str(ei.value)


def test_unrelated_bad_request_is_not_retried():
    fake = FakeClient(_http_error(openai.BadRequestError, 400, "model does not support tools"))
    with pytest.raises(LLMError):
        GroqLLM("k", "m", client=fake).create("s", USER_Q, tools=TOOLS, force_tool=True)
    assert len(fake.requests) == 1


def test_empty_response_choices_is_an_error():
    with pytest.raises(LLMError):
        GroqLLM("k", "m", client=FakeClient(NS(choices=[], usage=None))).create("s", USER_Q)


# --------------------------------------------------------------------------- client: tool-call failures (the reported bug)
def test_required_tool_choice_with_prose_in_failed_generation_is_salvaged_without_retry():
    fake = FakeClient(_tool_failure("Hello! Ask me about your data."))
    r = GroqLLM("k", "openai/gpt-oss-120b", client=fake).create("s", USER_Q, tools=TOOLS, force_tool=True)
    assert len(fake.requests) == 1
    assert r.blocks == [{"type": "text", "text": "Hello! Ask me about your data."}]


@pytest.mark.parametrize("fg", [None, "", '{"name": "run_sql"', "<|call|>run_sql", "[1]"])
def test_required_tool_failure_without_usable_prose_retries_once_with_auto(fg):
    fake = FakeClient(_tool_failure(fg), _resp(content="ok"))
    r = GroqLLM("k", "m", client=fake).create("s", USER_Q, tools=TOOLS, force_tool=True)
    assert [q["tool_choice"] for q in fake.requests] == ["required", "auto"]
    assert r.blocks[0]["text"] == "ok"


def test_malformed_tool_call_under_auto_is_retried_once():
    err = _http_error(openai.BadRequestError, 400, "tool_use_failed: Failed to call a function")
    fake = FakeClient(err, _sql("SELECT 1"))
    r = GroqLLM("k", "m", client=fake).create("s", USER_Q, tools=TOOLS)
    assert [q["tool_choice"] for q in fake.requests] == ["auto", "auto"]
    assert r.blocks[0]["type"] == "tool_use"


def test_persistent_tool_failure_raises_a_user_presentable_error():
    fake = FakeClient(_tool_failure(), _tool_failure())
    with pytest.raises(ToolChoiceError) as ei:
        GroqLLM("k", "m", client=fake).create("s", USER_Q, tools=TOOLS)
    assert len(fake.requests) == 2 and ei.value.status == 502 and "invalid tool call" in str(ei.value)


def test_failure_details_and_empty_messages_are_logged(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="datasense.llm"):
        GroqLLM("k", "m", client=FakeClient(_tool_failure("<|garbage|>"), _resp(content=None, finish="length"))).create(
            "s", USER_Q, tools=TOOLS)
    assert "garbage" in caplog.text
    assert "empty message" in caplog.text and "finish_reason=length" in caplog.text


# --------------------------------------------------------------------------- configuration
def test_get_llm_requires_key_and_builds_groq_client(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_provider", "groq")
    monkeypatch.setattr(s, "groq_api_key", "")
    with pytest.raises(LLMNotConfigured) as ei:
        get_llm()
    assert ei.value.status == 503 and "GROQ_API_KEY" in str(ei.value)

    monkeypatch.setattr(s, "groq_api_key", "gsk_test_dummy")
    monkeypatch.setattr(s, "groq_model", "openai/gpt-oss-120b")
    client = get_llm()
    assert isinstance(client, GroqLLM) and client.model == "openai/gpt-oss-120b"
    llm_mod._client_cache.clear()


def test_mock_provider_still_works(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_provider", "mock")
    assert get_llm().model == "mock-llm"


# --------------------------------------------------------------------------- agent loop on the REAL dataset
def test_tool_call_then_sql_then_final_answer_uses_real_data(client, dataset_id):
    out, fake = _run([_sql(COUNT_SQL), _final("There are 4,000 rows.")], dataset_id, "How many rows are there?")
    assert out["summary"] == "There are 4,000 rows."
    assert out["queries"][0]["ok"] is True and out["table"]["rows"] == [[4000]]
    assert out["follow_ups"] == ["A?", "B?", "C?"]
    tool_msg = fake.requests[1]["messages"][-1]  # the real result went back to the model
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1" and tool_msg["name"] == "run_sql"
    assert "4000" in tool_msg["content"]


def test_columns_question_is_answered_from_schema_without_a_query(client, dataset_id):
    out, fake = _run([_final("The dataset has 10 columns.", qid=None)], dataset_id, "How many columns?")
    assert out["summary"] == "The dataset has 10 columns." and out["table"] is None and len(fake.requests) == 1


_DF = generate_sales_demo()


@pytest.mark.parametrize("question, sql, expected", [
    ("Average revenue?", "SELECT AVG(revenue) AS avg_revenue FROM data", [[_DF["revenue"].mean()]]),
    ("Which category has the highest sales?",
     "SELECT category, SUM(revenue) AS total FROM data GROUP BY category ORDER BY total DESC LIMIT 1",
     [[_DF.groupby("category")["revenue"].sum().idxmax(), _DF.groupby("category")["revenue"].sum().max()]]),
    ("Missing values?", "SELECT COUNT(*) - COUNT(customer_segment) AS missing FROM data",
     [[int(_DF["customer_segment"].isna().sum())]]),
    ("Compare two categories",
     "SELECT category, COUNT(*) AS n FROM data WHERE category IN ('Electronics','Furniture') GROUP BY category ORDER BY category",
     [["Electronics", int((_DF.category == "Electronics").sum())], ["Furniture", int((_DF.category == "Furniture").sum())]]),
])
def test_analyst_questions_match_pandas_on_the_actual_data(client, dataset_id, question, sql, expected):
    out, _ = _run([_sql(sql), _final("answer")], dataset_id, question)
    assert out["queries"][0]["ok"] is True
    _assert_rows(out["table"]["rows"], expected)


def test_top_five_and_monthly_trend(client, dataset_id):
    out, _ = _run([_sql("SELECT order_id, revenue FROM data ORDER BY revenue DESC, order_id LIMIT 5"),
                   _final("top 5")], dataset_id, "Top 5 by revenue")
    assert len(out["table"]["rows"]) == 5 and out["table"]["rows"][0][1] == pytest.approx(_DF["revenue"].max())
    out, _ = _run([_sql("SELECT strftime('%Y-%m', order_date) AS month, SUM(revenue) AS rev FROM data "
                        "GROUP BY month ORDER BY month"), _final("trend")], dataset_id, "Monthly trend")
    months = _DF["order_date"].astype(str).str[:7].nunique()
    assert len(out["table"]["rows"]) == months and out["chart"] is not None


def test_plain_text_instead_of_tool_call_is_nudged_to_use_the_data(client, dataset_id):
    out, fake = _run([_resp(content="Sure, let me look."), _sql(COUNT_SQL), _final("4,000 rows.")], dataset_id)
    nudge = fake.requests[1]["messages"][-1]
    assert nudge["role"] == "user" and "run_sql" in nudge["content"]
    assert out["table"]["rows"] == [[4000]] and out["summary"] == "4,000 rows."


def test_conversational_question_gets_a_text_reply_without_a_query(client, dataset_id):
    out, fake = _run([_resp(content="Hello! I can analyse your data."), _resp(content="Hi! Ask me about your data.")],
                     dataset_id, "hello")
    assert out["summary"] == "Hi! Ask me about your data." and out["table"] is None and out["queries"] == []
    assert len(fake.requests) == 2


def test_prose_with_numbers_and_no_query_is_never_shown_as_an_answer(client, dataset_id):
    out, _ = _run([_resp(content="There are 5,000 rows."), _resp(content="There are 5,000 rows.")], dataset_id)
    assert "5,000" not in out["summary"] and "couldn't produce a working query" in out["summary"]
    assert out["table"] is None


@pytest.mark.parametrize("bad_call", [
    _call("c1", "run_sql", "{not valid json"),                       # malformed arguments
    _call("c1", "run_sql", {"sql": "DROP TABLE data", "purpose": "x"}),    # unsafe write
    _call("c1", "run_sql", {"sql": "SELECT * FROM other_table", "purpose": "x"}),
    _call("c1", "run_sql", {"sql": "SELECT nope FROM data", "purpose": "x"}),  # unknown column
    _call("c1", "run_sql", {"sql": "SELECT 1; SELECT 2", "purpose": "x"}),
])
def test_bad_sql_and_malformed_tool_arguments_are_rejected_then_repaired(client, dataset_id, bad_call):
    steps = [_resp(finish="tool_calls", tool_calls=[bad_call]), _sql(COUNT_SQL, cid="c2"), _final("ok", qid=2)]
    out, fake = _run(steps, dataset_id)
    assert out["queries"][0]["ok"] is False and out["queries"][0]["error"]
    assert out["queries"][1]["ok"] is True and out["table"]["rows"] == [[4000]]  # table still intact
    assert fake.requests[1]["messages"][-1]["content"].startswith("ERROR")


def test_final_answer_with_empty_summary_is_rejected_and_retried(client, dataset_id):
    out, fake = _run([_sql(COUNT_SQL), _final("", qid=1), _final("Fixed.", qid=1, cid="cf2")], dataset_id)
    assert out["summary"] == "Fixed." and len(fake.requests) == 3
    assert "non-empty" in fake.requests[2]["messages"][-1]["content"]


def test_prose_after_a_query_is_accepted_and_shows_the_result(client, dataset_id):
    out, _ = _run([_sql(COUNT_SQL), _resp(content="There are 4,000 rows.")], dataset_id)
    assert out["summary"] == "There are 4,000 rows." and out["table"]["rows"] == [[4000]]


# --- empty model response: finalised from the REAL result, never "I could not produce an answer"
@pytest.mark.parametrize("raw", [
    _json_answer(),
    "```json\n" + _json_answer() + "\n```",
    "Here you go: " + _json_answer(),
])
def test_empty_message_after_query_is_finalised_in_json_mode(client, dataset_id, raw):
    out, fake = _run([_sql(COUNT_SQL), _resp(content=None), _resp(content=raw)], dataset_id)
    assert out["summary"] == "There are 4,000 rows." and out["follow_ups"] == ["A?", "B?", "C?"]
    assert out["table"]["rows"] == [[4000]]
    last = fake.requests[-1]
    assert last["response_format"] == {"type": "json_object"} and "tools" not in last
    assert "4000" in last["messages"][-1]["content"]  # the real result is what it summarises


def test_invalid_json_finalisation_falls_back_to_plain_text(client, dataset_id):
    out, fake = _run([_sql(COUNT_SQL), _resp(content=None), _resp(content="not json"),
                      _resp(content="There are 4,000 rows.")], dataset_id)
    assert out["summary"] == "There are 4,000 rows." and "response_format" not in fake.requests[-1]


def test_fully_empty_model_still_returns_the_real_table_with_an_honest_message(client, dataset_id):
    out, _ = _run([_sql(COUNT_SQL), _resp(content=None), _resp(content=None), _resp(content=None)], dataset_id)
    assert "could not produce" not in out["summary"].lower() and "last successful query result" in out["summary"]
    assert out["table"]["rows"] == [[4000]] and out["sql"]


def test_rate_limit_during_finalisation_is_not_hammered_and_table_is_kept(client, dataset_id):
    out, fake = _run([_sql(COUNT_SQL), _resp(content=None), _http_error(openai.RateLimitError, 429)], dataset_id)
    assert len(fake.requests) == 3  # no second finalisation attempt against a rate limit
    assert out["table"]["rows"] == [[4000]] and "last successful query result" in out["summary"]


def test_agent_loop_cannot_run_forever(client, dataset_id):
    n = get_settings().max_agent_steps
    steps = [_sql(COUNT_SQL, cid=f"c{i}") for i in range(n)] + [_resp(content=_json_answer())]
    out, fake = _run(steps, dataset_id)
    assert len(fake.requests) == n + 1 and len(out["queries"]) == n
    assert out["summary"] == "There are 4,000 rows."


def test_tool_failure_after_a_successful_query_is_finalised_not_raised(client, dataset_id):
    out, _ = _run([_sql(COUNT_SQL), _tool_failure(), _tool_failure(), _resp(content=_json_answer())], dataset_id)
    assert out["summary"] == "There are 4,000 rows." and out["table"]["rows"] == [[4000]]


def test_tool_failure_on_the_first_step_is_reported_as_an_error(client, dataset_id):
    with pytest.raises(LLMError) as ei:
        _run([_tool_failure(), _tool_failure()], dataset_id)
    assert ei.value.status == 502


# --------------------------------------------------------------------------- API integration (/api/chat)
MESSY_CSV = b"order_id,region,sales,qty\n1,North,100,2\n2,South,,3\n3,North,300,7\n4,,50,1\n"


def _install(monkeypatch, *steps, model="m"):
    fake = FakeClient(*steps)
    monkeypatch.setattr("app.routers.chat.get_llm", lambda: GroqLLM("k", model, client=fake))
    return fake


def test_chat_api_on_uploaded_dataset_with_missing_values(client, monkeypatch):
    r = client.post("/api/datasets", files={"file": ("messy.csv", io.BytesIO(MESSY_CSV), "text/csv")})
    assert r.status_code == 201
    ds_id = r.json()["id"]
    sql = ("SELECT COUNT(*) AS n_rows, COUNT(sales) AS sales_filled, "
           "COUNT(*) - COUNT(sales) AS sales_missing FROM data")
    _install(monkeypatch, _sql(sql), _final("One sales value is missing."))
    r = client.post("/api/chat", json={"dataset_id": ds_id, "message": "How many sales values are missing?"})
    assert r.status_code == 200
    payload = r.json()["assistant_message"]["payload"]
    assert payload["table"]["rows"] == [[4, 3, 1]] and payload["summary"] == "One sales value is missing."
    client.delete(f"/api/datasets/{ds_id}")


def test_chat_api_follow_up_keeps_history(client, dataset_id, monkeypatch):
    _install(monkeypatch, _sql(COUNT_SQL), _final("There are 4,000 rows."))
    first = client.post("/api/chat", json={"dataset_id": dataset_id, "message": "How many rows are there?"}).json()
    convo = first["conversation_id"]

    fake = _install(monkeypatch,
                    _sql("SELECT region, COUNT(*) AS n FROM data GROUP BY region ORDER BY region", cid="c9"),
                    _final("By region.", qid=1))
    r = client.post("/api/chat", json={"dataset_id": dataset_id, "conversation_id": convo, "message": "Now by region"})
    assert r.status_code == 200 and len(r.json()["assistant_message"]["payload"]["table"]["rows"]) == 4
    sent = fake.requests[0]["messages"]
    assert any(m["role"] == "user" and m.get("content") == "How many rows are there?" for m in sent)
    assert any(m["role"] == "assistant" and "[SQL used for this answer]" in (m.get("content") or "") for m in sent)
    assert sent[-1] == {"role": "user", "content": "Now by region"}
    detail = client.get(f"/api/conversations/{convo}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]


@pytest.mark.parametrize("exc_factory, http_status, needle", [
    (lambda: _http_error(openai.RateLimitError, 429), 429, "rate limiting"),
    (lambda: _http_error(openai.InternalServerError, 500), 502, "500"),
    (lambda: _http_error(openai.AuthenticationError, 401), 502, "GROQ_API_KEY"),
    (lambda: openai.APIConnectionError(request=httpx.Request("POST", "https://x")), 502, "Could not reach"),
])
def test_chat_api_reports_groq_failures_with_a_useful_message(client, dataset_id, monkeypatch,
                                                              exc_factory, http_status, needle):
    _install(monkeypatch, exc_factory())
    r = client.post("/api/chat", json={"dataset_id": dataset_id, "message": "How many rows?"})
    assert r.status_code == http_status and needle in r.json()["detail"]
