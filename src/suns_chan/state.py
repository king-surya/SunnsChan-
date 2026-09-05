"""Experience-shaped state, kept separate from the fixed identity seed."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

from .textutil import clamp


@dataclass
class AgentState:
    curiosity: float = 0.78
    confidence: float = 0.50
    stress: float = 0.28
    energy: float = 0.70
    warmth: float = 0.55
    directness: float = 0.62
    rhythm: str = "steady"
    interests: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "AgentState":
        return cls(**data) if data else cls()

    def to_dict(self) -> dict:
        return asdict(self)

    def express_interest(self, tags: Iterable[str]) -> None:
        for tag in tags:
            normalized = tag.strip().lower()
            if normalized:
                self.interests[normalized] = clamp(self.interests.get(normalized, 0.30) + 0.04)

    def learn_from_outcome(self, outcome: str, tags: Iterable[str]) -> None:
        if outcome == "success":
            self.confidence = clamp(self.confidence + 0.06)
            self.stress = clamp(self.stress - 0.04)
            self.energy = clamp(self.energy - 0.02)
        elif outcome == "failure":
            self.confidence = clamp(self.confidence - 0.07)
            self.stress = clamp(self.stress + 0.08)
            self.curiosity = clamp(self.curiosity + 0.03)
        else:
            self.confidence = clamp(self.confidence - 0.01)
        self.express_interest(tags)
