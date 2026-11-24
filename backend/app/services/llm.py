"""Thin wrapper around the Groq API (OpenAI-compatible chat completions).

Everything that talks to the LLM vendor lives here, so the rest of the app never
imports the SDK. The query engine keeps using a neutral, block-based message
format; this module translates it to/from the OpenAI chat format:

  internal (what query_engine / mock_llm use)      Groq / OpenAI wire format
  ------------------------------------------      ---------------------------------
  assistant content = [text|tool_use blocks]  ->   assistant message + tool_calls
  user content = [tool_result blocks]         ->   one role="tool" message per result

Responses are normalised into LLMResponse:
    blocks=[{"type": "text"|"tool_use", ...}], usage={...}, stop_reason=str

Groq/gpt-oss notes (why the client is defensive):
  * Groq does not constrain gpt-oss decoding for tool_choice="required": when the model
    answers in prose Groq replies HTTP 400 ("Tool choice is required, but model did not call
    a tool") and puts the prose in `failed_generation`. We salvage that prose instead of
    throwing the model's answer away.
  * gpt-oss "thinks" first and that thinking counts against max_tokens, so we keep the
    reasoning effort low and log empty generations (finish_reason, usage) for diagnosis.
  * `response_format={"type": "json_object"}` (JSON mode) is supported and does not depend on
    tool calling, so the engine uses it to finalise an answer if the tool route fails.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai

from app.core.config import get_settings

log = logging.getLogger("datasense.llm")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Keep gpt-oss thinking short so it cannot use up the token budget before it writes the answer.
GPT_OSS_REASONING_EFFORT = "low"


class LLMError(RuntimeError):
    """User-presentable failure talking to the model."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class LLMNotConfigured(LLMError):
    def __init__(self):
        super().__init__(
            "The Groq API key is not configured. Add GROQ_API_KEY to backend/.env "
            "and restart the backend.",
            status=503,
        )


class ToolChoiceError(LLMError):
    """Groq 400 ``tool_use_failed``: the model's tool call was missing or malformed."""

    failed_generation: str = ""


@dataclass
class LLMResponse:
    blocks: list[dict[str, Any]]
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = ""


class LLMClient(Protocol):
    model: str

    def create(self, system: str, messages: list[dict], tools: list[dict] | None = None,
               force_tool: bool = False, max_tokens: int = 4096,
               json_mode: bool = False) -> LLMResponse: ...


# --------------------------------------------------------------------------- #
# Format translation (pure functions - unit tested without any network)
# --------------------------------------------------------------------------- #
def tools_to_openai(tools: list[dict]) -> list[dict]:
    """{"name","description","input_schema"} -> OpenAI function-tool definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


def messages_to_openai(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    tool_names: dict[str, str] = {}  # tool_call id -> function name (Groq's tool messages carry it)
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "assistant":
            text = "\n".join(b["text"] for b in content if b.get("type") == "text" and b.get("text"))
            calls = []
            for b in content:
                if b.get("type") == "tool_use":
                    tool_names[b["id"]] = b["name"]
                    calls.append({
                        "id": b["id"],
                        "type": "function",
                        "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
                    })
            if not text and not calls:
                continue  # an empty assistant turn is invalid on the wire
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            # user turn made of tool_result blocks (and maybe plain text)
            texts: list[str] = []
            for b in content:
                if b.get("type") == "tool_result":
                    body = str(b.get("content", ""))
                    if b.get("is_error") and not body.startswith("ERROR"):
                        body = "ERROR: " + body
                    tool_msg = {"role": "tool", "tool_call_id": b["tool_use_id"], "content": body}
                    if b["tool_use_id"] in tool_names:
                        tool_msg["name"] = tool_names[b["tool_use_id"]]
                    out.append(tool_msg)
                elif b.get("type") == "text":
                    texts.append(b["text"])
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
    return out


def _parse_arguments(raw: Any) -> dict:
    """Tool arguments arrive as a JSON string; be forgiving of empty/malformed ones."""
    if isinstance(raw, dict):
        return raw
    if not raw or not str(raw).strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("Model produced malformed tool arguments: %.200s", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}


_STOP_REASONS = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}


def response_from_openai(resp: Any) -> LLMResponse:
    choice = resp.choices[0]
    msg = choice.message
    blocks: list[dict[str, Any]] = []
    if getattr(msg, "content", None):
        blocks.append({"type": "text", "text": msg.content})
    for tc in getattr(msg, "tool_calls", None) or []:
        blocks.append({
            "type": "tool_use", "id": tc.id, "name": tc.function.name,
            "input": _parse_arguments(tc.function.arguments),
        })
    u = getattr(resp, "usage", None)
    usage = {
        "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(u, "completion_tokens", 0) or 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    if isinstance(cached, int) and cached:
        usage["cache_read_tokens"] = cached
    reason = choice.finish_reason or ""
    if any(b["type"] == "tool_use" for b in blocks):
        reason = "tool_calls"
    return LLMResponse(blocks=blocks, usage=usage, stop_reason=_STOP_REASONS.get(reason, reason))


# --------------------------------------------------------------------------- #
# Groq client
# --------------------------------------------------------------------------- #
_TOOL_FAILURE_MARKERS = ("tool choice is required", "tool_use_failed", "failed to call a function")


def _is_tool_choice_failure(exc: Exception) -> bool:
    text = f"{getattr(exc, 'message', '')} {getattr(exc, 'body', '')} {exc}".lower()
    return any(m in text for m in _TOOL_FAILURE_MARKERS)


def _failed_generation(exc: Exception) -> str:
    """What the model actually wrote when Groq rejected it (Groq puts it in the 400 body)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        fg = body.get("failed_generation") or (body.get("error") or {}).get("failed_generation")
        if isinstance(fg, str):
            return fg
    return ""


def _salvage_text(failed_generation: str) -> str:
    """Plain prose in `failed_generation` is a usable text answer; tool-call syntax is not."""
    text = (failed_generation or "").strip()
    if not text or "<|" in text or text[0] in "{[<":
        return ""
    return text


def _detail(exc: Exception) -> str:
    return str(getattr(exc, "message", None) or exc)[:300]


class GroqLLM:
    def __init__(self, api_key: str, model: str, client: Any | None = None):
        self.model = model
        self._client = client or openai.OpenAI(
            api_key=api_key, base_url=GROQ_BASE_URL, timeout=90.0, max_retries=2
        )

    def create(self, system, messages, tools=None, force_tool=False, max_tokens=4096,
               json_mode=False) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages_to_openai(system, messages),
            "max_tokens": max_tokens,
        }
        if self.model.startswith("openai/gpt-oss"):
            kwargs["extra_body"] = {"reasoning_effort": GPT_OSS_REASONING_EFFORT}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        elif tools:
            kwargs["tools"] = tools_to_openai(tools)
            kwargs["tool_choice"] = "required" if force_tool else "auto"

        try:
            resp = self._request(kwargs)
        except ToolChoiceError as exc:
            required = kwargs.get("tool_choice") == "required"
            salvaged = _salvage_text(exc.failed_generation) if required else ""
            if salvaged:
                log.info("tool_choice=required: model answered in prose; using it as a text reply")
                return LLMResponse(blocks=[{"type": "text", "text": salvaged}],
                                   usage={"input_tokens": 0, "output_tokens": 0,
                                          "cache_read_tokens": 0, "cache_write_tokens": 0},
                                   stop_reason="end_turn")
            # Malformed/missing tool call: sampling is not deterministic, so retry once
            # (letting the model decide instead of insisting on a tool).
            log.info("Tool call failed on %s (tool_choice=%s); retrying once with auto",
                     self.model, kwargs.get("tool_choice"))
            if required:
                kwargs["tool_choice"] = "auto"
            resp = self._request(kwargs)

        if not getattr(resp, "choices", None):
            raise LLMError("Groq returned an empty response. Try again.", 502)
        result = response_from_openai(resp)
        if not result.blocks:
            ch = resp.choices[0]
            reasoning = getattr(ch.message, "reasoning", None) or ""
            log.warning("Groq returned an empty message (model=%s, finish_reason=%s, usage=%s, "
                        "tool_choice=%s, reasoning_chars=%s)", self.model, ch.finish_reason,
                        result.usage, kwargs.get("tool_choice"), len(reasoning))
        return result

    def _request(self, kwargs: dict[str, Any]) -> Any:
        try:
            return self._client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as exc:
            raise LLMError("The Groq API key was rejected. Check GROQ_API_KEY in backend/.env.", 502) from exc
        except openai.PermissionDeniedError as exc:
            raise LLMError("The Groq API key does not have access to the configured model "
                           f"('{self.model}'). Check GROQ_MODEL in backend/.env.", 502) from exc
        except openai.NotFoundError as exc:
            raise LLMError(f"Model '{self.model}' was not found on Groq (it may have been retired). "
                           "Set GROQ_MODEL in backend/.env to an available tool-calling model.", 502) from exc
        except openai.RateLimitError as exc:
            raise LLMError("Groq is rate limiting requests right now (free-tier token limits are "
                           "per minute). Wait a moment and retry.", 429) from exc
        except openai.APITimeoutError as exc:
            raise LLMError("The Groq API timed out. Try again in a moment.", 502) from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Could not reach the Groq API. Check your internet connection.", 502) from exc
        except openai.BadRequestError as exc:
            if _is_tool_choice_failure(exc):
                fg = _failed_generation(exc)
                log.warning("Groq tool-call failure: %s | failed_generation=%.600s", _detail(exc), fg)
                err = ToolChoiceError("The model produced an invalid tool call. Try again or rephrase "
                                      "your question.", 502)
                err.failed_generation = fg
                raise err from exc
            # e.g. the model does not support tools, or the request was malformed
            log.warning("Groq rejected the request: %s", exc)
            raise LLMError(f"Groq could not process this request ({_detail(exc)}). Try rephrasing, "
                           "or set GROQ_MODEL to a model that supports tool calling.", 502) from exc
        except openai.APIStatusError as exc:
            log.warning("Groq API error %s: %s", exc.status_code, exc)
            raise LLMError(f"Groq API error ({exc.status_code}): {_detail(exc)}", 502) from exc


_client_cache: dict[tuple, LLMClient] = {}


def get_llm() -> LLMClient:
    settings = get_settings()
    if settings.llm_provider == "mock":
        from app.services.mock_llm import MockLLM

        return MockLLM()
    if not settings.groq_api_key.strip():
        raise LLMNotConfigured()
    key = (settings.groq_api_key, settings.groq_model)
    if key not in _client_cache:
        _client_cache[key] = GroqLLM(settings.groq_api_key.strip(), settings.groq_model)
    return _client_cache[key]
