"""Shared pure text/validation primitives.

These tiny helpers were previously re-implemented (identically) across the
codebase. Centralizing them removes drift risk and gives future subsystems one
canonical place for token math. All functions are pure and dependency-light.
"""

from __future__ import annotations

from .comms import scrub_secrets
from .memory import tokenize


def clamp(value: float, *, ndigits: int = 4) -> float:
    """Clamp a number into [0, 1] and round. Deterministic."""
    return round(max(0.0, min(1.0, value)), ndigits)


def normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    """Lowercase, strip, and de-duplicate tags; drop empties. Deterministic."""
    clean: list[str] = []
    for tag in tags or []:
        if not isinstance(tag, str):
            raise ValueError("tags must be strings")
        normalized = tag.strip().lower()
        if normalized and normalized not in clean:
            clean.append(normalized)
    return clean


def jaccard(left: set, right: set) -> float:
    """Jaccard similarity over token sets (1.0 for two empty sets)."""
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def signature(text: str) -> frozenset[str]:
    """Scrubbed token signature used for dedup/clustering."""
    return frozenset(tokenize(scrub_secrets(text)))
