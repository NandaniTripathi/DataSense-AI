"""Deterministic stand-in for the LLM, used ONLY by the automated tests
(LLM_PROVIDER=mock). It lets us test the whole pipeline - tool loop, SQL
validation, repair on error, charts, history - without an API key.
It is never used unless LLM_PROVIDER=mock is set explicitly."""
from __future__ import annotations

import json
import re
import uuid

from app.services.llm import LLMResponse


class MockLLM:
    model = "mock-llm"
    calls: list[dict] = []  # recorded for assertions in tests

    def create(self, system, messages, tools=None, force_tool=False, max_tokens=4096,
               json_mode=False) -> LLMResponse:
        MockLLM.calls.append({"system": system, "messages": messages})
        usage = {"input_tokens": 10, "output_tokens": 5}
        if json_mode:  # tool-free finalisation call
            return LLMResponse(blocks=[{"type": "text", "text": json.dumps({
                "summary": "Mock finalised answer.", "chart": None,
                "follow_up_questions": ["What about by month?", "Show the top 5", "Any outliers?"]})}], usage=usage)
        if not tools:  # plain text call (executive summary)
            return LLMResponse(blocks=[{"type": "text", "text": "- Mock summary bullet.\n**Suggested next step:** review anomalies."}], usage=usage)

        question = self._first_user_text(messages)
        last_user = messages[-1]
        tool_results = last_user["content"] if isinstance(last_user["content"], list) else []
        cats = re.findall(r"^- (\w+) \| TEXT \| categorical", system, re.M)
        nums = re.findall(r"^- (\w+) \| (?:REAL|INTEGER) \| numeric", system, re.M)

        if not tool_results:
            if "UNRELATED" in question:
                return self._tool("final_answer", {"summary": "That is not something this dataset can answer.", "follow_up_questions": []}, usage)
            cat, num = (cats or ["region"])[0], (nums or ["value"])[0]
            sql = f"SELECT {cat}, ROUND(SUM({num}), 2) AS total_{num}, COUNT(*) AS n FROM data GROUP BY {cat} ORDER BY total_{num} DESC LIMIT 10"
            if "BREAKIT" in question:
                sql = f"SELECT {cat}, SUM(no_such_column) FROM data GROUP BY {cat}"
            if "EVIL" in question:
                sql = "DROP TABLE data"
            return self._tool("run_sql", {"sql": sql, "purpose": "mock aggregate"}, usage)

        last = tool_results[0]
        if last.get("is_error"):
            cat, num = (cats or ["region"])[0], (nums or ["value"])[0]
            sql = f"SELECT {cat}, ROUND(SUM({num}), 2) AS total_{num} FROM data GROUP BY {cat} ORDER BY 2 DESC LIMIT 10"
            return self._tool("run_sql", {"sql": sql, "purpose": "mock repaired query"}, usage)

        qid = int(re.search(r"query_id: (\d+)", last["content"]).group(1))
        header = last["content"].splitlines()[2].split(",")
        history_note = f" (conversation has {len(messages)} messages)"
        return self._tool("final_answer", {
            "summary": f"Mock answer for: {question[:60]}{history_note}",
            "display_query_id": qid,
            "chart": {"type": "bar", "x": header[0], "y": header[1], "title": "Mock chart"},
            "follow_up_questions": ["What about by month?", "Show the top 5", "Any outliers?"],
        }, usage)

    @staticmethod
    def _first_user_text(messages) -> str:
        for m in reversed(messages):
            if m["role"] == "user" and isinstance(m["content"], str):
                return m["content"]
        return ""

    @staticmethod
    def _tool(name: str, payload: dict, usage: dict) -> LLMResponse:
        return LLMResponse(blocks=[{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": name, "input": payload}], usage=usage, stop_reason="tool_use")
