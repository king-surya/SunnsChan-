"""LLM provider implementations. Each speaks one backend; core stays provider-agnostic."""

from .ollama import OllamaProvider

__all__ = ["OllamaProvider"]
