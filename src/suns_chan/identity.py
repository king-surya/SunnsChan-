"""Editable identity seed. It guides expression; it never grants tool authority."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class IdentitySeed:
    name: str
    role: str
    traits: tuple[str, ...]
    expression_style: str
    relational_context: str
    honesty_boundary: str
    simulated_rhythm_note: str

    @classmethod
    def from_file(cls, path: str | Path) -> "IdentitySeed":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            name=data["name"],
            role=data["role"],
            traits=tuple(data["traits"]),
            expression_style=data["expression_style"],
            relational_context=data["relational_context"],
            honesty_boundary=data["honesty_boundary"],
            simulated_rhythm_note=data["simulated_rhythm_note"],
        )
