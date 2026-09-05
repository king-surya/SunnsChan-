"""Provider-independent voice interface. Voice is an interface, not the brain.

Text remains the canonical representation: STT adapters decode audio to the
same strings the chat loop consumes, and TTS adapters encode agent responses
to audio. No reasoning lives here. With no provider configured, the system
operates fully in text mode — VoiceBus degrades to documented no-ops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class VoiceInputProvider(Protocol):
    kind: str

    def transcribe(self, audio: bytes) -> str:
        ...


class VoiceOutputProvider(Protocol):
    kind: str

    def synthesize(self, text: str) -> bytes:
        ...


@dataclass
class MockVoiceInput:
    """Deterministic stand-in: maps exact payloads to transcripts."""

    kind: str = "mock-stt"
    script: dict[bytes, str] = field(default_factory=dict)

    def transcribe(self, audio: bytes) -> str:
        if audio in self.script:
            return self.script[audio]
        raise ValueError("mock-stt has no transcript for this payload (MOCK)")


@dataclass
class MockVoiceOutput:
    kind: str = "mock-tts"
    rendered: list[str] = field(default_factory=list)

    def synthesize(self, text: str) -> bytes:
        if not text.strip():
            raise ValueError("cannot synthesize empty text")
        self.rendered.append(text)
        return f"[mock-audio:{len(self.rendered)}]".encode()


@dataclass
class VoiceBus:
    """Optional voice wiring. Either side may be absent (text mode)."""

    input_provider: VoiceInputProvider | None = None
    output_provider: VoiceOutputProvider | None = None

    @property
    def input_enabled(self) -> bool:
        return self.input_provider is not None

    @property
    def output_enabled(self) -> bool:
        return self.output_provider is not None

    def to_text(self, audio: bytes) -> str:
        if self.input_provider is None:
            raise RuntimeError("voice input not configured; use text mode")
        return self.input_provider.transcribe(audio)

    def to_audio(self, text: str) -> bytes:
        if self.output_provider is None:
            raise RuntimeError("voice output not configured; use text mode")
        return self.output_provider.synthesize(text)
