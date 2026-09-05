"""Optional avatar presentation layer. Presentation only.

Suns Chan Core -> PresentationState -> AvatarAdapter. The adapter owns no
memory, goals, curiosity, policy, autonomy, or identity persistence — it
renders a snapshot it is given. With no adapter configured, the core
operates normally; presentation degrades to the response text itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PresentationState:
    response_text: str
    expression: str = "neutral"   # speaking state / mood cue for the renderer
    attention: str = "idle"       # explore | exploit | rest | idle
    activity: str = ""            # current activity title, if any
    affect_note: str = ""         # from affect.expression_note()
    animation_cues: tuple[str, ...] = ()


class AvatarAdapter(Protocol):
    kind: str

    def render(self, state: PresentationState) -> str:
        """Return an opaque renderer token/cue. Never executes anything."""
        ...


@dataclass
class MockAvatar:
    kind: str = "mock-avatar"
    rendered: list[PresentationState] = field(default_factory=list)

    def render(self, state: PresentationState) -> str:
        self.rendered.append(state)
        return f"[mock-avatar:{state.expression}:{state.attention}]"


def build_presentation(response_text: str, *, expression: str = "neutral",
                       attention: str = "idle", activity: str = "",
                       affect_note: str = "",
                       animation_cues: tuple[str, ...] = ()) -> PresentationState:
    if not response_text.strip():
        raise ValueError("presentation needs response text")
    return PresentationState(response_text, expression, attention, activity,
                             affect_note, animation_cues)


def present_text(state: PresentationState, adapter: AvatarAdapter | None = None) -> str:
    """Core-safe presentation: text always works; adapter cue appended when present."""
    if adapter is None:
        return state.response_text
    return f"{state.response_text}\n{adapter.render(state)}"
