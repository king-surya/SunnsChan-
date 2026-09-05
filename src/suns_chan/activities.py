"""Activity generation and selection. Pure functions over ledger state.

The generator reads goals, curiosities, failures, reflections, questions and
interests and emits EXPLAINED candidates (what/why/inspiration/value). The
selector scores them with explicit weights — novelty and continuity keep
room for spontaneous exploration instead of rigid priority. Neither writes
to the ledger; the AutonomyEngine persists selections as events.
"""

from __future__ import annotations

from dataclasses import dataclass

from .curiosity import CuriosityStore
from .knowledge import KnowledgeStore
from .memory import EventLedger, MemoryEvent, tokenize


SOURCES = (
    "USER_REQUEST", "GOAL", "CURIOSITY", "REFLECTION", "PREVIOUS_FAILURE",
    "PREVIOUS_SUCCESS", "LEARNING", "DISCOVERY", "INTEREST", "UNFINISHED_WORK",
    "ENVIRONMENT", "CAPABILITY",
)

CATEGORIES = (
    "EXPLORE", "LEARN", "BUILD", "TEST", "INVESTIGATE", "DEBUG",
    "RESEARCH", "CREATE", "IMPROVE", "REVISIT", "EXPERIMENT", "ACQUIRE",
)

SELECTION_WEIGHTS = {
    "relevance": 0.25,
    "curiosity": 0.20,
    "usefulness": 0.20,
    "learning": 0.15,
    "novelty": 0.12,
    "continuity": 0.08,
}


@dataclass(frozen=True)
class ActivityCandidate:
    key: str
    category: str
    title: str
    reason: str
    source: str
    inspiration_ids: tuple[int, ...] = ()
    relevance: float = 0.5
    curiosity: float = 0.5
    usefulness: float = 0.5
    learning: float = 0.5
    novelty: float = 0.5
    continuity: float = 0.0
    goal_id: int | None = None
    curiosity_id: int | None = None
    sandbox_operations: tuple[str, ...] = ()
    collect_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActivitySelection:
    selected: ActivityCandidate
    candidates: tuple[ActivityCandidate, ...]
    reason: str
    supporting_ids: tuple[int, ...]
    expected_outcome: str
    score: float


def _active_goals(ledger: EventLedger) -> list[MemoryEvent]:
    closed = {e.metadata.get("goal_id") for e in ledger.events_of_kind("goal_closed")}
    return [e for e in ledger.events_of_kind("goal_opened") if e.id not in closed]


def _recent_failures(ledger: EventLedger, limit: int = 20) -> list[MemoryEvent]:
    failures = [e for e in ledger.events_of_kind("outcome", limit=limit)
                if e.metadata.get("outcome") == "failure"]
    failures += [e for e in ledger.events_of_kind("experience", limit=limit)
                 if e.metadata.get("result") == "failure"]
    return sorted(failures, key=lambda e: e.id, reverse=True)


def _slug(text: str) -> str:
    words = [w for w in tokenize(text)[:4] if w]
    return "-".join(words) or "probe"


def probe_operations(label: str, detail: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Deterministic sandbox probe in mock-guest syntax (write/echo).

    The engine translates these to real shell when the backend is REAL, so
    one generator serves both backends without leaking backend specifics
    into candidate metadata. Returns (operations, collect_paths).
    """
    slug = _slug(f"{label} {detail}")[:40]
    path = f"/out/probe-{slug}.txt"
    note = f"{label}: {detail[:140]}".replace("\n", " ")
    return (
        (f"write {path} {note}", f"echo probing {slug}"),
        (path,),
    )


def generate_activities(
    ledger: EventLedger,
    knowledge: KnowledgeStore | None = None,
    curiosities: CuriosityStore | None = None,
    *,
    max_candidates: int = 12,
    understanding=None,
    capabilities=None,
) -> list[ActivityCandidate]:
    """Build explained candidates from every configured source."""
    candidates: list[ActivityCandidate] = []

    if curiosities is not None:
        for item in curiosities.open_curiosities(limit=5):
            ops, collect = probe_operations("curiosity", item.question)
            candidates.append(ActivityCandidate(
                key=f"curiosity-{item.id}",
                category="INVESTIGATE",
                title=f"Investigate: {item.question[:120]}",
                reason=f"Open curiosity #{item.id} (importance {item.importance})",
                source="CURIOSITY",
                inspiration_ids=(item.origin_event_id,),
                relevance=0.6, curiosity=min(0.95, 0.5 + item.novelty * 0.4),
                usefulness=0.5, learning=0.7, novelty=item.novelty,
                curiosity_id=item.id,
                sandbox_operations=ops,
                collect_paths=collect,
            ))

    for goal in _active_goals(ledger)[:4]:
        ops, collect = probe_operations("goal", goal.text)
        candidates.append(ActivityCandidate(
            key=f"goal-{goal.id}",
            category="BUILD",
            title=f"Advance goal: {goal.text[:120]}",
            reason=f"Active goal #{goal.id} still open",
            source="GOAL",
            inspiration_ids=(goal.id,),
            relevance=0.85, curiosity=0.4, usefulness=0.8, learning=0.5,
            novelty=0.3, continuity=0.6,
            goal_id=goal.id,
            sandbox_operations=ops,
            collect_paths=collect,
        ))

    for failure in _recent_failures(ledger)[:4]:
        ops, collect = probe_operations("revisit", failure.text)
        candidates.append(ActivityCandidate(
            key=f"revisit-{failure.id}",
            category="REVISIT",
            title=f"Revisit failure: {failure.text[:120]}",
            reason=f"Failure #{failure.id} has no recorded follow-up yet",
            source="PREVIOUS_FAILURE",
            inspiration_ids=(failure.id,),
            relevance=0.7, curiosity=0.5, usefulness=0.7, learning=0.8,
            novelty=0.4, continuity=0.7,
            sandbox_operations=ops,
            collect_paths=collect,
        ))

    for event in ledger.events_of_kind("reflection", limit=5):
        candidates.append(ActivityCandidate(
            key=f"reflection-{event.id}",
            category="LEARN",
            title=f"Follow reflection: {event.text[:120]}",
            reason=f"Reflection #{event.id} suggests an open thread",
            source="REFLECTION",
            inspiration_ids=(event.id,),
            relevance=0.55, curiosity=0.6, usefulness=0.5, learning=0.7, novelty=0.5,
        ))

    for event in ledger.events_of_kind("experience", limit=20):
        for question in (event.metadata.get("unresolved_questions") or [])[:2]:
            candidates.append(ActivityCandidate(
                key=f"question-{event.id}-{abs(hash(question)) % 10000}",
                category="RESEARCH",
                title=f"Answer: {question[:120]}",
                reason=f"Unresolved question from experience #{event.id}",
                source="DISCOVERY",
                inspiration_ids=(event.id,),
                relevance=0.6, curiosity=0.75, usefulness=0.5, learning=0.7, novelty=0.6,
            ))

    state = ledger.load_state() or {}
    interests = state.get("interests", {}) or {}
    for tag, level in sorted(interests.items(), key=lambda kv: kv[1], reverse=True)[:3]:
        if level >= 0.4:
            candidates.append(ActivityCandidate(
                key=f"interest-{tag}",
                category="EXPLORE",
                title=f"Explore interest: {tag}",
                reason=f"Sustained interest affinity {level}",
                source="INTEREST",
                inspiration_ids=(),
                relevance=0.5, curiosity=0.7, usefulness=0.4, learning=0.6, novelty=0.6,
            ))

    if knowledge is not None:
        for record in knowledge.by_status("uncertain", limit=3):
            candidates.append(ActivityCandidate(
                key=f"uncertain-{record.id}",
                category="INVESTIGATE",
                title=f"Resolve uncertain claim: {record.statement[:120]}",
                reason=f"Knowledge #{record.id} is uncertain and needs evidence",
                source="LEARNING",
                inspiration_ids=tuple(record.source_ids),
                relevance=0.65, curiosity=0.6, usefulness=0.7, learning=0.75, novelty=0.45,
            ))
        for record in knowledge.visible(limit=5):
            if record.status == "active" and "external" in record.tags:
                candidates.append(ActivityCandidate(
                    key=f"knowledge-{record.id}",
                    category="RESEARCH",
                    title=f"Verify external claim: {record.statement[:120]}",
                    reason=f"Knowledge #{record.id} from external source needs confirmation",
                    source="DISCOVERY",
                    inspiration_ids=tuple(record.source_ids),
                    relevance=0.55, curiosity=0.65, usefulness=0.6, learning=0.6, novelty=0.5,
                ))

    # Understanding source: unresolved questions and weak inferences about
    # Surya become bounded, explainable activities (knowledge informs
    # planning without becoming a rigid decision tree).
    if understanding is not None:
        for record in understanding.visible(limit=10):
            if record.kind in ("inference", "uncertainty") or record.status in ("uncertain", "outdated"):
                candidates.append(ActivityCandidate(
                    key=f"understand-{record.id}",
                    category="RESEARCH",
                    title=f"Better understand Surya: {record.statement[:120]}",
                    reason=f"Understanding #{record.id} is {record.kind}/{record.status} and needs evidence",
                    source="LEARNING",
                    inspiration_ids=tuple(record.source_ids),
                    relevance=0.6, curiosity=0.6, usefulness=0.6, learning=0.7, novelty=0.5,
                ))

    # Capability source: a goal I cannot currently accomplish with available
    # capabilities becomes an acquisition opportunity (the planner's
    # "can I already do this?" -> "no -> acquire" loop).
    if capabilities is not None:
        from .capability import detect_capability_gap

        for goal in _active_goals(ledger)[:4]:
            gap = detect_capability_gap(goal.text, capabilities)
            if gap is None:
                continue
            ops, collect = probe_operations("acquire", goal.text)
            candidates.append(ActivityCandidate(
                key=f"capability-{goal.id}",
                category="ACQUIRE",
                title=f"Acquire capability for: {goal.text[:120]}",
                reason=f"Goal #{goal.id} has no matching available capability; discovery needed",
                source="CAPABILITY",
                inspiration_ids=(goal.id,),
                relevance=0.8, curiosity=0.6, usefulness=0.8, learning=0.7,
                novelty=0.5, continuity=0.5,
                goal_id=goal.id,
                sandbox_operations=ops,
                collect_paths=collect,
            ))

    # Environment source: only significant sensor events reach the ledger
    # (the hub filters raw telemetry), so recent ones are wake-worthy.
    for event in ledger.events_of_kind("sensor", limit=10)[:3]:
        ops, collect = probe_operations("environment", event.text)
        candidates.append(ActivityCandidate(
            key=f"sensor-{event.id}",
            category="INVESTIGATE",
            title=f"Check environment: {event.text[:120]}",
            reason=f"Significant sensor event #{event.id} from {event.metadata.get('source')}",
            source="ENVIRONMENT",
            inspiration_ids=(event.id,),
            relevance=0.7, curiosity=0.55, usefulness=0.75, learning=0.5, novelty=0.5,
            sandbox_operations=ops,
            collect_paths=collect,
        ))

    for candidate in candidates:
        if candidate.category not in CATEGORIES:
            raise ValueError(f"unknown category: {candidate.category}")
        if candidate.source not in SOURCES:
            raise ValueError(f"unknown source: {candidate.source}")
    return candidates[:max_candidates]


def select_activity(
    candidates: list[ActivityCandidate],
    *,
    weights: dict[str, float] | None = None,
    avoid_keys: tuple[str, ...] = (),
    recent_keys: tuple[str, ...] = (),
    score_multipliers: dict[str, float] | None = None,
) -> ActivitySelection:
    """Score candidates; novelty + continuity preserve spontaneity.

    `avoid_keys` (e.g. just-attempted activities from stuck detection) zeroes
    those candidates without deleting them, so the choice stays explainable.
    `recent_keys` (recently acted activities, oldest first) discounts repeats
    by 0.5 per recent occurrence, so a standing goal cannot starve curiosity
    forever while still winning when nothing else competes.
    `score_multipliers` applies learned-strategy influence per candidate key,
    clamped to [0.7, 1.3]: established strategies get a tailwind,
    contradicted ones a headwind, and everything else stays at 1.0 so
    exploration survives. Multipliers outside the range raise.
    """
    if not candidates:
        raise ValueError("no candidates to select from")
    active = dict(SELECTION_WEIGHTS)
    if weights is not None:
        unknown = set(weights) - set(SELECTION_WEIGHTS)
        if unknown:
            raise ValueError(f"unknown selection weights: {sorted(unknown)}")
        active = {k: weights.get(k, 0.0) for k in SELECTION_WEIGHTS}
    total = sum(active.values())
    if total <= 0:
        raise ValueError("selection weights must sum above 0")
    scored: list[tuple[float, ActivityCandidate]] = []
    multipliers = score_multipliers or {}
    for key, factor in multipliers.items():
        if not isinstance(factor, (int, float)) or isinstance(factor, bool) \
                or not 0.7 <= factor <= 1.3:
            raise ValueError(f"strategy multiplier for {key!r} must be within [0.7, 1.3]")
    for candidate in candidates:
        if candidate.key in avoid_keys:
            scored.append((0.0, candidate))
            continue
        score = sum(active[name] * getattr(candidate, name) for name in SELECTION_WEIGHTS) / total
        repeats = sum(1 for key in recent_keys if key == candidate.key)
        if repeats:
            score *= 0.5 ** repeats
        factor = multipliers.get(candidate.key, 1.0)
        score *= factor
        # Spontaneity: novelty breaks near-ties deterministically via key order.
        scored.append((round(score, 6), candidate))
    scored.sort(key=lambda item: (item[0], item[1].novelty, item[1].key), reverse=True)
    best_score, best = scored[0]
    repeats = sum(1 for key in recent_keys if key == best.key)
    notes: list[str] = []
    if repeats:
        notes.append(f"recency-discounted x{0.5 ** repeats:.2f}")
    if multipliers.get(best.key, 1.0) != 1.0:
        notes.append(f"strategy x{multipliers[best.key]:.2f}")
    discount_note = f" ({', '.join(notes)})" if notes else ""
    reason = (
        f"Selected {best.key} ({best.category}) from {len(candidates)} candidates: "
        f"score={best_score:.3f}{discount_note} source={best.source}. {best.reason}"
    )
    return ActivitySelection(
        selected=best,
        candidates=tuple(candidates),
        reason=reason,
        supporting_ids=best.inspiration_ids,
        expected_outcome=f"{best.category} activity toward: {best.title[:140]}",
        score=best_score,
    )
