"""Provider-independent LLM interface. Core never imports a specific backend."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Protocol


@dataclass(frozen=True)
class LLMMessage:
    role: str  # system | user | assistant
    content: str


@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[LLMMessage, ...]
    max_tokens: int = 512
    json_mode: bool = False

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("at least one message is required")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMProvider(Protocol):
    kind: str

    def generate(self, request: LLMRequest) -> LLMResponse:
        ...


@dataclass
class EchoProvider:
    """Deterministic test/dev provider. Never calls a network."""

    kind: str = "fake"
    model: str = "fake-echo"

    def generate(self, request: LLMRequest) -> LLMResponse:
        last = request.messages[-1].content if request.messages else ""
        text = f"[echo:{self.model}] {last}"
        if request.json_mode:
            text = json.dumps({"response": text[:1500], "confidence": 0.5})
        return LLMResponse(text=text, model=self.model)


def provider_from_settings(provider: str, model: str, **kwargs) -> LLMProvider:
    """Factory boundary. Real backends plug in here; unknown names raise."""
    if provider in {"fake", "echo", ""}:
        return EchoProvider(model=model or "fake-echo")
    if provider == "ollama":
        from .providers.ollama import OllamaProvider  # lazy: providers import this module

        return OllamaProvider(
            model=model or "llama3.1",
            base_url=str(kwargs.get("base_url", "http://localhost:11434")),
            timeout_secs=float(kwargs.get("timeout_secs", 30.0)),
        )
    raise ValueError(f"LLM provider not configured in this build: {provider!r}")
