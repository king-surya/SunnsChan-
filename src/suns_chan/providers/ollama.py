"""Ollama chat backend over HTTP (stdlib urllib only).

Honest failure contract: if the server is unreachable or returns invalid
data, generate() raises instead of fabricating a response. Callers decide
whether to fall back to another provider.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.request import Request, urlopen

from ..llm import LLMMessage, LLMRequest, LLMResponse


def _prompt_from(messages: tuple[LLMMessage, ...]) -> str:
    lines: list[str] = []
    for message in messages:
        role = message.role.upper()
        lines.append(f"{role}: {message.content}")
    lines.append("ASSISTANT:")
    return "\n".join(lines)


@dataclass
class OllamaProvider:
    kind: str = "ollama"
    model: str = "llama3.1"
    base_url: str = "http://localhost:11434"
    timeout_secs: float = 30.0

    def generate(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": self.model,
            "prompt": _prompt_from(request.messages),
            "stream": False,
        }
        if request.json_mode:
            payload["format"] = "json"
        body = json.dumps(payload).encode("utf-8")
        http_request = Request(
            self.base_url.rstrip("/") + "/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_secs) as response:
                raw = response.read().decode("utf-8")
        except OSError as exc:
            raise RuntimeError(f"ollama server unreachable at {self.base_url}: {exc}") from exc
        try:
            data = json.loads(raw)
            text = data["response"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"ollama returned invalid data: {exc}") from exc
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("ollama returned an empty response")
        prompt_tokens = data.get("prompt_eval_count", 0) or 0
        completion_tokens = data.get("eval_count", 0) or 0
        return LLMResponse(
            text=text,
            model=self.model,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
        )
