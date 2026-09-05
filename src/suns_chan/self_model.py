"""Derived self-model: who Suns Chan is becoming, recomputed from evidence.

NOT a hard-coded personality object. Every section derives from existing
records (learned objects, curiosities, state, stuck patterns) and is rebuilt
on every call — nothing to desynchronize, everything revisable when new
evidence arrives. Read-only over all stores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class SelfModel:
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    enjoys: tuple[str, ...] = ()
    returns_to: tuple[str, ...] = ()
    approaches: tuple[str, ...] = ()
    recent_learning: tuple[str, ...] = ()
    curiosities: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def build_self_model(ledger, learned, *, curiosities=None, state=None,
                     limit: int = 5) -> SelfModel:
    """Assemble the current self-model purely from existing evidence."""
    established = learned.by_kind("skill", statuses=("established",)) + \
        learned.by_kind("strategy", statuses=("established",))
    strengths = tuple(f"{o.subject} ({o.confidence:.2f})" for o in established[:limit])
    weak = learned.by_kind("skill", statuses=("contradicted", "uncertain")) + \
        learned.by_kind("strategy", statuses=("contradicted", "uncertain"))
    weaknesses = tuple(f"{o.subject} ({o.status})" for o in weak[:limit])
    enjoys = tuple(f"{o.subject} ({o.confidence:.2f})" for o in
                   learned.by_kind("preference", statuses=("established",))[:limit])
    topics: dict[str, int] = {}
    if curiosities is not None:
        for item in curiosities.all(limit=200):
            if item.status in ("new", "active", "reopened", "investigating"):
                topics[item.topic or "general"] = topics.get(item.topic or "general", 0) + 1
    if state is not None:
        for tag, level in (state.interests or {}).items():
            if level >= 0.4:
                topics[tag] = topics.get(tag, 0) + 1
    returns_to = tuple(sorted(topics, key=lambda t: topics[t], reverse=True)[:limit])
    approaches = tuple(
        f"{o.subject} [{(o.conditions or {}).get('context', 'general')}]" for o in
        learned.by_kind("strategy", statuses=("established",))[:limit])
    recent = sorted(learned.all(limit=200), key=lambda o: o.updated_at, reverse=True)
    recent_learning = tuple(f"{o.kind}:{o.subject} ({o.status})" for o in recent[:limit])
    open_curiosities = ()
    if curiosities is not None:
        open_curiosities = tuple(c.question[:120] for c in curiosities.open_curiosities(limit=limit))
    stuck_categories: dict[str, int] = {}
    started_by_key = {e.metadata.get("activity_key"): e
                      for e in ledger.events_of_kind("activity_started", limit=200)}
    for event in ledger.events_of_kind("activity_finished", limit=200):
        if event.metadata.get("stuck"):
            start = started_by_key.get(event.metadata.get("activity_key"))
            category = str((start.metadata.get("category", "") if start else "")).strip()
            if category:
                stuck_categories[category] = stuck_categories.get(category, 0) + 1
    limitations = tuple(f"{category} (stuck x{count})" for category, count in
                        sorted(stuck_categories.items(), key=lambda kv: kv[1], reverse=True)[:limit])
    return SelfModel(strengths, weaknesses, enjoys, returns_to, approaches,
                     recent_learning, open_curiosities, limitations)
