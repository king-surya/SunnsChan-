"""Deterministic ranking layer. No hidden scoring.

Callers supply per-candidate signals already normalized to 0..1, plus a
deterministic tiebreak (e.g. event id). rank() combines them with explicit
weights and returns every contribution so results are inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field


KNOWN_SIGNALS = ("keyword", "semantic", "recency", "importance", "confidence", "source_quality")

DEFAULT_WEIGHTS: dict[str, float] = {
    "keyword": 0.40,
    "semantic": 0.25,
    "recency": 0.15,
    "importance": 0.10,
    "confidence": 0.05,
    "source_quality": 0.05,
}


@dataclass(frozen=True)
class ScoredCandidate:
    key: int
    signals: dict[str, float]
    tiebreak: float = 0.0


@dataclass(frozen=True)
class RankedCandidate:
    key: int
    score: float
    contributions: dict[str, float] = field(default_factory=dict)
    tiebreak: float = 0.0


def _check_signals(signals: dict[str, float]) -> None:
    for name, value in signals.items():
        if name not in KNOWN_SIGNALS:
            raise ValueError(f"unknown ranking signal: {name!r} (known: {KNOWN_SIGNALS})")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"signal {name!r} must be a number")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"signal {name!r} must be between 0 and 1")


def weighted_score(signals: dict[str, float], weights: dict[str, float] | None = None) -> tuple[float, dict[str, float]]:
    """Return (score, contributions). Weights are normalized; missing signals count as 0."""
    active = dict(DEFAULT_WEIGHTS)
    if weights is not None:
        unknown = set(weights) - set(KNOWN_SIGNALS)
        if unknown:
            raise ValueError(f"unknown ranking weight(s): {sorted(unknown)}")
        active = {k: weights.get(k, 0.0) for k in KNOWN_SIGNALS}
    _check_signals(signals)
    total = sum(active.values())
    if total <= 0.0:
        raise ValueError("ranking weights must sum above 0")
    contributions = {name: (active[name] / total) * float(signals.get(name, 0.0)) for name in KNOWN_SIGNALS}
    return round(sum(contributions.values()), 6), contributions


def rank(
    candidates: list[ScoredCandidate],
    *,
    weights: dict[str, float] | None = None,
    limit: int = 8,
) -> list[RankedCandidate]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        score, contributions = weighted_score(candidate.signals, weights)
        ranked.append(RankedCandidate(candidate.key, score, contributions, candidate.tiebreak))
    ranked.sort(key=lambda item: (item.score, item.tiebreak), reverse=True)
    return ranked[:limit]


def recency_decay(newest_first_index: int, half_life: float = 8.0) -> float:
    """1.0 for the newest item, decaying toward 0. Formula: 0.5 ** (index / half_life)."""
    if newest_first_index < 0:
        raise ValueError("index must be >= 0")
    if half_life <= 0.0:
        raise ValueError("half_life must be above 0")
    return round(0.5 ** (newest_first_index / half_life), 6)
