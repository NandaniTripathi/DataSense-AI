"""Verify your Groq setup from backend/.env (the key is never printed). This is the REAL-API check;
the automated tests never call Groq.

    cd backend
    python -m scripts.check_groq

1. Lists the models your key can use and checks that GROQ_MODEL is among them.
2. Makes one tiny tool call to confirm the model supports tool calling.
3. Makes one JSON-mode call (the engine's fallback for writing the final answer).
"""
from __future__ import annotations

import sys

import openai

from app.core.config import get_settings
from app.services.llm import GROQ_BASE_URL, GroqLLM

PING_TOOL = [{
    "name": "ping", "description": "Reply by calling this tool with ok=true.",
    "input_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
}]


def main() -> int:
    s = get_settings()
    if not s.groq_api_key.strip():
        print("GROQ_API_KEY is empty. Add it to backend/.env first.")
        return 1
    client = openai.OpenAI(api_key=s.groq_api_key.strip(), base_url=GROQ_BASE_URL, timeout=30.0)
    try:
        ids = sorted(m.id for m in client.models.list().data)
    except openai.AuthenticationError:
        print("The key was rejected (401). Re-generate it at https://console.groq.com/keys")
        return 1
    except openai.APIConnectionError:
        print("Could not reach api.groq.com. Check your internet connection / proxy.")
        return 1
    print(f"Models available to this key ({len(ids)}):")
    for i in ids:
        print("  ", i, "<-- GROQ_MODEL" if i == s.groq_model else "")
    if s.groq_model not in ids:
        print(f"\nGROQ_MODEL '{s.groq_model}' is NOT available. Pick a tool-calling model from the list "
              "(see https://console.groq.com/docs/tool-use/overview).")
        return 1
    try:
        r = GroqLLM(s.groq_api_key.strip(), s.groq_model).create(
            system="You must answer by calling the ping tool.",
            messages=[{"role": "user", "content": "Call the ping tool with ok=true."}],
            tools=PING_TOOL, max_tokens=500)
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        print(f"\nTool-call test FAILED: {exc}")
        return 1
    ok = any(b["type"] == "tool_use" and b["name"] == "ping" for b in r.blocks)
    print("\nTool calling:", "OK" if ok else "model did not call the tool - choose another model")
    try:
        j = GroqLLM(s.groq_api_key.strip(), s.groq_model).create(
            system='Reply with the JSON object {"ok": true} and nothing else.',
            messages=[{"role": "user", "content": "go"}], json_mode=True, max_tokens=500)
        text = "".join(b.get("text", "") for b in j.blocks)
        json_ok = '"ok"' in text
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        print(f"JSON mode: FAILED ({exc})")
        json_ok = False
    else:
        print("JSON mode:", "OK" if json_ok else f"unexpected reply: {text[:80]!r}")
    return 0 if ok and json_ok else 1


if __name__ == "__main__":
    sys.exit(main())
