"""Experience records on the Event Ledger + lightweight derived relationships.

No second memory database: an experience is a ledger event of kind
`experience` whose metadata carries intent, actions, observations, result,
and links (inspired_by, follow_up_of, ...). Relationships are derived
metadata — queryable, never structural rewrites of history.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .memory import EventLedger, MemoryEvent


RELATIONSHIPS = (
    "inspired_by",
    "follow_up_of",
    "related_to",
    "contradicts",
    "confirms",
    "refines",
    "reproduces",
    "failed_after",
    "succeeded_after",
)


@dataclass(frozen=True)
class Experience:
    event_id: int
    session_id: str
    activity_id: str
    intent: str
    result: str  # success | failure | unknown
    related: dict[str, tuple[int, ...]] = field(default_factory=dict)


def record_experience(
    ledger: EventLedger,
    *,
    session_id: str,
    activity_id: str,
    intent: str,
    motivation: str = "",
    hypothesis: str = "",
    actions: list[str] | None = None,
    observations: list[str] | None = None,
    result: str = "unknown",
    artifacts: list[str] | None = None,
    lessons: list[str] | None = None,
    unresolved_questions: list[str] | None = None,
    related: dict[str, list[int]] | None = None,
    confidence: float = 0.5,
    goal_id: int | None = None,
    affect: dict | None = None,
    knowledge_ids: list[int] | None = None,
    capability_id: int | None = None,
) -> MemoryEvent:
    """Persist one rich experience event. Failures preserved like successes.

    `knowledge_ids` links the experience back to the knowledge claims it
    tested or drew on (the reverse of knowledge's `source_ids`), so external
    knowledge and lived experience inform one another without merging into a
    single record type.
    """
    if not session_id.strip() or not activity_id.strip() or not intent.strip():
        raise ValueError("session_id, activity_id and intent must be non-empty")
    if result not in {"success", "failure", "unknown"}:
        raise ValueError("result must be success, failure, or unknown")
    links: dict[str, tuple[int, ...]] = {}
    for relation, ids in (related or {}).items():
        if relation not in RELATIONSHIPS:
            raise ValueError(f"unknown relationship: {relation}")
        links[relation] = tuple(ids)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    text = f"[{result}] {intent}"
    if hypothesis.strip():
        text += f" Hypothesis: {hypothesis.strip()}"
    affect_snapshot = {k: v for k, v in (affect or {}).items()
                       if k in {"energy", "confidence", "curiosity", "stress", "warmth", "rhythm"}}
    return ledger.record("experience", text, {
        "session_id": session_id,
        "activity_id": activity_id,
        "intent": intent,
        "motivation": motivation,
        "hypothesis": hypothesis,
        "actions": actions or [],
        "observations": observations or [],
        "result": result,
        "artifacts": artifacts or [],
        "lessons": lessons or [],
        "unresolved_questions": unresolved_questions or [],
        "related": {k: list(v) for k, v in links.items()},
        "confidence": round(float(confidence), 4),
        "goal_id": goal_id,
        "affect": affect_snapshot,
        "knowledge_ids": list(knowledge_ids or []),
        "capability_id": capability_id,
    })


def experiences_of_session(ledger: EventLedger, session_id: str, *, limit: int = 200) -> list[MemoryEvent]:
    return [e for e in ledger.events_of_kind("experience", limit=limit)
            if e.metadata.get("session_id") == session_id]


def related_experiences(ledger: EventLedger, event_id: int) -> dict[str, list[MemoryEvent]]:
    """Resolve an experience's derived relationship links to live events."""
    source = None
    for event in ledger.events_of_kind("experience", limit=1000):
        if event.id == event_id:
            source = event
            break
    if source is None:
        raise ValueError(f"unknown experience event: {event_id}")
    by_id = {e.id: e for e in ledger.events_of_kind("experience", limit=1000)}
    resolved: dict[str, list[MemoryEvent]] = {}
    for relation, ids in (source.metadata.get("related") or {}).items():
        resolved[relation] = [by_id[i] for i in ids if i in by_id]
    return resolved
