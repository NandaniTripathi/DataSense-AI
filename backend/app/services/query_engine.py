"""The AI analyst: natural-language question -> SQL (via LLM tool use) ->
sandboxed execution -> verified answer with table + chart + follow-ups.

Flow (an "agent loop" with two tools):
    LLM --run_sql--> we validate + execute --result/error--> LLM
    ... (repeat, up to max_agent_steps) ...
    LLM --final_answer--> we build the table, chart and response.
"""
from __future__ import annotations

import io
import json
import logging
import time
from typing import Any

import pandas as pd

from app.core.config import get_settings
from app.services import charts
from app.services.llm import LLMClient, LLMError, ToolChoiceError
from app.services.profiling import TABLE_NAME, schema_card
from app.services.prompts import ANALYST_SYSTEM, TOOLS
from app.services.sql_guard import UnsafeSQLError
from app.services.sqlite_runner import QueryExecutionError, QueryResult, run_query

log = logging.getLogger("datasense.engine")
MAX_TABLE_ROWS_TO_CLIENT = 500
SUMMARY_FALLBACK_SYSTEM = (
    "You are DataSense, a careful senior data analyst. Answer the user's question using ONLY the "
    "numbers in the SQL result you are given. Plain business language, no SQL jargon."
)
FINALISE_JSON_SYSTEM = SUMMARY_FALLBACK_SYSTEM + (
    " Reply with a single JSON object and nothing else, with exactly these keys: "
    '"summary" (markdown string, 2-5 sentences, lead with the direct answer and the key exact numbers), '
    '"chart" (an object {"type": one of bar|line|area|scatter|pie|histogram|box, "x": column, "y": column, '
    '"title": string} using column names of the result, or null when a chart adds nothing), '
    '"follow_up_questions" (array of 3 short, natural follow-up questions about this dataset).'
)


def serialize_frame(df: pd.DataFrame, limit: int | None = None) -> dict:
    """DataFrame -> {columns, rows} with JSON-safe values (NaN -> null)."""
    if limit is not None:
        df = df.head(limit)
    payload = json.loads(df.to_json(orient="split", date_format="iso", default_handler=str))
    return {"columns": payload["columns"], "rows": payload["data"]}


def _result_for_model(result: QueryResult, query_id: int, preview_rows: int) -> str:
    df = result.df
    buf = io.StringIO()
    df.head(preview_rows).to_csv(buf, index=False)
    note = ""
    if len(df) > preview_rows:
        note = f"\n(showing first {preview_rows} of {len(df)}{'+' if result.truncated else ''} rows)"
    return f"query_id: {query_id}\nrows: {len(df)}{'+' if result.truncated else ''}\n{buf.getvalue()}{note}"


def build_history_messages(history: list[dict], turns: int) -> list[dict]:
    """Earlier turns -> compact text messages, so follow-ups have context
    (we keep the question, the SQL that answered it and the summary)."""
    out: list[dict] = []
    for m in history[-turns * 2:]:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        else:
            sql = (m.get("payload") or {}).get("sql")
            text = m["content"]
            if sql:
                text += f"\n\n[SQL used for this answer]\n{sql}"
            out.append({"role": "assistant", "content": text})
    # the API requires alternating roles starting with "user"
    merged: list[dict] = []
    for m in out:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n\n" + m["content"]
        else:
            merged.append(dict(m))
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


def _text_of(blocks: list[dict]) -> str:
    return " ".join(b["text"] for b in blocks if b["type"] == "text").strip()


def _has_digits(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def clean_final(raw: Any, results: dict[int, QueryResult]) -> dict:
    """Validate/normalise the model's final answer so later code can trust its types."""
    raw = raw if isinstance(raw, dict) else {}
    summary = raw.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""

    qid = raw.get("display_query_id")
    if isinstance(qid, bool) or qid is None:
        qid = None
    else:
        try:
            qid = int(qid)
        except (TypeError, ValueError):
            qid = None
        else:
            # an id that does not exist falls back to the latest successful result
            qid = qid if qid in results else (max(results) if results else None)

    chart = raw.get("chart") if isinstance(raw.get("chart"), dict) else None
    fups = raw.get("follow_up_questions")
    fups = [str(q).strip() for q in fups if str(q).strip()][:4] if isinstance(fups, list) else []
    return {"summary": summary, "display_query_id": qid, "chart": chart, "follow_up_questions": fups}


def answer_question(llm: LLMClient, dataset_id: str, profile: dict,
                    history: list[dict], question: str) -> dict:
    """Agent loop. Every number in an answer comes from SQL that ran on the real dataset:
    the model may only (1) call run_sql, (2) call final_answer, or (3) chat when no data is
    needed. Anything else is nudged, finalised from the real results, or reported honestly."""
    settings = get_settings()
    started = time.monotonic()
    system = ANALYST_SYSTEM.format(
        schema_card=schema_card(profile), table=TABLE_NAME, max_steps=settings.max_agent_steps
    )
    messages = build_history_messages(history, settings.history_turns)
    if messages and messages[-1]["role"] == "user":
        # previous user turn had no answer stored; merge to keep alternation valid
        messages[-1]["content"] += "\n\n" + question
    else:
        messages.append({"role": "user", "content": question})

    queries: list[dict[str, Any]] = []
    results: dict[int, QueryResult] = {}
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    final: dict | None = None
    nudged = False

    for _step in range(settings.max_agent_steps):  # hard cap: the loop can never run away
        try:
            # tool_choice stays "auto": Groq does not enforce "required" for gpt-oss, and a
            # data-free question (greeting) is legitimately answered in text.
            resp = llm.create(system=system, messages=messages, tools=TOOLS)
        except ToolChoiceError as exc:
            if results:
                log.warning("Tool call failed after a successful query; finalising from results: %s", exc)
                break
            raise
        for k in usage:
            usage[k] += resp.usage.get(k, 0)
        messages.append({"role": "assistant", "content": resp.blocks})
        tool_uses = [b for b in resp.blocks if b["type"] == "tool_use"]

        if not tool_uses:
            text = _text_of(resp.blocks)
            if not results:  # nothing has actually been computed yet (no query, or all failed)
                if not nudged:
                    # No query has run yet. Never accept a prose answer that no data backs:
                    # ask once for run_sql (or final_answer if no data is needed).
                    nudged = True
                    messages.append({"role": "user", "content": (
                        "Use the tools: call run_sql to get the data you need, or call final_answer if "
                        "no data is needed or the dataset cannot answer this. Do not answer in plain text.")})
                    continue
                # Conversational reply (greeting, "what can you do?") is fine; a reply with numbers
                # but no query behind it would be fabricated, so it is dropped.
                if text and not _has_digits(text):
                    final = {"summary": text, "follow_up_questions": []}
                break
            # Queries ran: prose is acceptable (the model has seen the real results).
            if text:
                final = {"summary": text, "display_query_id": max(results) if results else None,
                         "follow_up_questions": []}
            break  # empty message -> finalised below from the real results

        tool_results: list[dict] = []
        has_run_sql = any(t["name"] == "run_sql" for t in tool_uses)
        for tu in tool_uses:
            if tu["name"] == "run_sql":
                tool_results.append(_handle_run_sql(tu, dataset_id, queries, results, settings))
            elif tu["name"] == "final_answer":
                candidate = clean_final(tu["input"], results)
                if has_run_sql:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                        "content": "Call final_answer only after you have seen the query results, in a separate step.",
                    })
                elif not candidate["summary"]:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                        "content": "final_answer needs a non-empty string field \"summary\". Call it again.",
                    })
                else:
                    final = candidate
                    tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": "ok"})
            else:
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                    "content": f"Unknown tool {tu['name']}",
                })
        if final is not None:
            break
        messages.append({"role": "user", "content": tool_results})

    if results and not (final or {}).get("summary"):
        # The model never delivered a usable explanation (empty message, dropped tool call,
        # step limit). Finalise from the REAL query result instead of failing.
        done = _finalise(llm, question, results, settings, usage)
        if done:
            final = done

    return _assemble(final, queries, results, usage, llm.model, started)


def _finalise(llm: LLMClient, question: str, results: dict[int, QueryResult], settings,
              usage: dict) -> dict | None:
    """Tool-free finalisation: JSON mode first (summary + chart + follow-ups), then plain text."""
    qid = max(results)
    block = (f"Question: {question}\n\nSQL result:\n"
             f"{_result_for_model(results[qid], qid, settings.llm_preview_rows)}\n\n")
    attempts = (
        ("json", FINALISE_JSON_SYSTEM, block + "Return the JSON object now.", True),
        ("text", SUMMARY_FALLBACK_SYSTEM,
         block + "Answer the question in 2-5 sentences of markdown, using only numbers from this result.", False),
    )
    for mode, system, prompt, json_mode in attempts:
        try:
            r = llm.create(system=system, messages=[{"role": "user", "content": prompt}],
                           max_tokens=2000, json_mode=json_mode)
        except LLMError as exc:
            log.warning("Finalisation (%s) failed: %s", mode, exc)
            if exc.status in (401, 403, 404, 429):
                break  # retrying the same account/model/limit would only fail again
            continue
        for k in usage:
            usage[k] += r.usage.get(k, 0)
        text = _text_of(r.blocks)
        if not text:
            continue
        if mode == "json":
            parsed = _parse_json_object(text)
            cleaned = clean_final({**parsed, "display_query_id": qid}, results) if parsed else None
            if cleaned and cleaned["summary"]:
                return cleaned
            continue
        return clean_final({"summary": text, "display_query_id": qid}, results)
    return None


def _parse_json_object(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    try:
        obj = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return obj if isinstance(obj, dict) else None


def _handle_run_sql(tu: dict, dataset_id: str, queries: list, results: dict, settings) -> dict:
    qid = len(queries) + 1
    sql = str(tu["input"].get("sql", ""))
    purpose = str(tu["input"].get("purpose", ""))
    entry: dict[str, Any] = {"id": qid, "purpose": purpose, "sql": sql, "ok": False, "error": None, "row_count": 0}
    queries.append(entry)
    try:
        result = run_query(dataset_id, sql)
    except (UnsafeSQLError, QueryExecutionError) as exc:
        entry["error"] = str(exc)
        return {"type": "tool_result", "tool_use_id": tu["id"], "is_error": True,
                "content": f"ERROR: {exc}. Fix the query and try again."}
    entry.update(ok=True, sql=result.sql, row_count=len(result.df), elapsed_ms=result.elapsed_ms)
    results[qid] = result
    return {"type": "tool_result", "tool_use_id": tu["id"],
            "content": _result_for_model(result, qid, settings.llm_preview_rows)}


def _assemble(final: dict | None, queries: list, results: dict, usage: dict,
              model: str, started: float) -> dict:
    final = final or {}
    chosen = final.get("display_query_id")
    if chosen not in results:
        chosen = max(results) if results and final.get("display_query_id") is not None else None
    if chosen is None and results and not final.get("summary"):
        chosen = max(results)

    summary = (final.get("summary") or "").strip()
    if not summary:
        summary = ("I couldn't finish writing the explanation, but the last successful query result is shown below."
                   if results else
                   "I couldn't produce a working query for that question. Try rephrasing it or naming the columns you care about.")

    table = chart = sql = None
    row_count, truncated = 0, False
    if chosen is not None:
        res = results[chosen]
        sql = res.sql
        table = serialize_frame(res.df, MAX_TABLE_ROWS_TO_CLIENT)
        row_count, truncated = len(res.df), res.truncated or len(res.df) > MAX_TABLE_ROWS_TO_CLIENT
        chart = charts.build_chart(res.df, final.get("chart"))

    raw_fups = final.get("follow_up_questions")
    follow_ups = [str(q) for q in raw_fups if str(q).strip()][:4] if isinstance(raw_fups, list) else []
    return {
        "summary": summary,
        "sql": sql,
        "table": table,
        "row_count": row_count,
        "truncated": truncated,
        "chart": chart,
        "follow_ups": follow_ups,
        "queries": queries,
        "meta": {"model": model, "usage": usage, "elapsed_ms": int((time.monotonic() - started) * 1000)},
    }
