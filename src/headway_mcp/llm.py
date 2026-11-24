"""Tool-calling chat for the analyst, behind the same provider family as the incident summaries.

`ChatClient.chat(messages, tools)` returns one assistant turn: text and/or tool calls. OpenAI is
the default (standard library HTTPS, no SDK); tests inject a fake.
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

DEFAULT_MODEL = "gpt-4o-mini"
ENV_MODEL = "HEADWAY_ANALYST_LLM_MODEL"
MAX_OUTPUT_TOKENS = 500


class LlmError(RuntimeError):
    """The provider call failed."""


class ChatClient(Protocol):
    model: str

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """One assistant message: {"content": str | None, "tool_calls": [{id, name, arguments}]}.

        A client may add "usage": {"prompt_tokens": int, "completion_tokens": int} (evals read it).
        """
        ...


class OpenAIChat:
    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
    ) -> None:
        self._key = api_key
        self.model = model or os.environ.get(ENV_MODEL, DEFAULT_MODEL)
        self._url = f"{base_url}/chat/completions"
        self._timeout = timeout

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "max_completion_tokens": MAX_OUTPUT_TOKENS,
                "messages": messages,
                "tools": tools,
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310 - fixed https endpoint
            self._url,
            data=body,
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                doc = json.loads(resp.read())
            msg = doc["choices"][0]["message"]
            usage = doc.get("usage") or {}
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
            raise LlmError(type(exc).__name__) from exc
        calls = [
            {"id": c["id"], "name": c["function"]["name"], "arguments": c["function"]["arguments"]}
            for c in msg.get("tool_calls") or []
        ]
        return {
            "content": msg.get("content"),
            "tool_calls": calls,
            "usage": {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
            },
        }
