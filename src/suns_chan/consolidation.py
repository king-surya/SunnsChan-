"""Budgeted consolidation: derive knowledge without rewriting raw events.

Two jobs share this module:

- consolidate(): the Phase 2 per-event job (one knowledge claim per
  uncovered learning/outcome/reflection event, idempotent).
- consolidate_learning(): the Phase 6 batch job. It mines patterns, skills,
  preferences, and strategies into learned objects, then promotes mature
  ones into knowledge at evidence thresholds. Failure safety: the ledger is
  only READ here; every learned/knowledge write commits independently, so a
  crash leaves prior valid state plus idempotent re-runnable leftovers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .knowledge import KnowledgeStore
from .memory import EventLedger


@dataclass(frozen=True)
class ConsolidationResult:
    created: int
    skipped_covered: int
    skipped_budget: int


_OUTCOME_CONFIDENCE = {"success": 0.65, "failure": 0.6, "unknown": 0.4}


def consolidate(
    ledger: EventLedger,
    store: KnowledgeStore,
    *,
    budget: int = 10,
    kinds: tuple[str, ...] = ("learning", "outcome", "reflection"),
    limit_per_kind: int = 100,
) -> ConsolidationResult:
    if budget < 1:
        raise ValueError("budget must be at least 1")
    covered = store.covered_source_ids()
    candidates = []
    for kind in kinds:
        candidates.extend(ledger.events_of_kind(kind, limit=limit_per_kind))
    # Oldest first so early experience is consolidated first.
    candidates.sort(key=lambda event: event.id)

    created = 0
    skipped_covered = 0
    skipped_budget = 0
    for event in candidates:
        if event.id in covered:
            skipped_covered += 1
            continue
        if created >= budget:
            skipped_budget += 1
            continue
        confidence = 0.55
        if event.kind == "outcome":
            confidence = _OUTCOME_CONFIDENCE.get(event.metadata.get("outcome", "unknown"), 0.4)
        elif event.kind == "learning":
            confidence = 0.6
        from .comms import scrub_secrets

        store.propose(
            statement=scrub_secrets(event.text),
            source_ids=[event.id],
            confidence=confidence,
            reason=f"consolidated from {event.kind} event {event.id}",
        )
        covered.add(event.id)
        created += 1
    return ConsolidationResult(
        created=created, skipped_covered=skipped_covered, skipped_budget=skipped_budget
    )


@dataclass
class LearningConsolidationResult:
    patterns: int = 0
    skills: int = 0
    preferences: int = 0
    strategies: int = 0
    promoted: int = 0
    demoted: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_secs: float = 0.0


def consolidation_due(ledger: EventLedger, learned, *, min_new_events: int = 5) -> tuple[bool, str]:
    """Should a consolidation run now? True when enough uncovered evidence
    accumulated, or a recent contradiction/correction needs absorption.

    Explicit trigger policy (no cron robot): callers (CLI, idle hooks) ask
    first and only run on True.
    """
    covered: set[int] = set()
    for record in learned.all(limit=1000):
        covered.update(record.supporting_ids)
        covered.update(record.contradicting_ids)
    # Only kinds the learning miners can absorb; per-event claims are the
    # other consolidate()'s job.
    fresh = 0
    for kind in ("outcome", "experience", "activity_started", "activity_finished"):
        for event in ledger.events_of_kind(kind, limit=200):
            if event.id not in covered:
                fresh += 1
    if fresh >= min_new_events:
        return True, f"{fresh} uncovered evidence events"
    for kind in ("learning", "reflection"):
        for event in ledger.events_of_kind(kind, limit=20):
            text = (event.text + " " + str(event.metadata.get("finding_type", ""))).lower()
            if "contradict" in text or "correction" in text:
                return True, f"recent contradiction/correction needs absorption (E{event.id})"
    return False, f"only {fresh} uncovered events (threshold {min_new_events})"


def consolidate_learning(
    ledger: EventLedger,
    knowledge: KnowledgeStore,
    learned,
    *,
    budget: int = 20,
    promote: bool = True,
) -> LearningConsolidationResult:
    """Mine learned objects from recent evidence, then promote mature ones.

    Each stage is bounded by the budget and commits independently: a crash
    mid-run leaves valid learned objects behind, and re-running is
    idempotent (same evidence is never double-counted). Raw events are only
    read, never written.
    """
    from .comms import scrub_secrets
    from .learned import (
        mine_patterns,
        mine_preferences,
        mine_skills,
        mine_strategies,
    )

    if budget < 1:
        raise ValueError("budget must be at least 1")
    import time as time_module

    started = time_module.monotonic()
    result = LearningConsolidationResult()
    spent = 0

    def spend(n: int) -> bool:
        nonlocal spent
        if spent + n > budget:
            return False
        spent += n
        return True

    miners = (
        ("patterns", mine_patterns),
        ("skills", mine_skills),
        ("preferences", mine_preferences),
        ("strategies", mine_strategies),
    )
    for name, miner in miners:
        try:
            touched = miner(ledger, learned, limit=200)
        except Exception as exc:  # one bad miner must not kill the batch
            result.errors.append(f"{name}: {exc}")
            continue
        unique = len({(o.kind, o.id) for o in touched})
        if spend(unique):
            setattr(result, name, unique)
        else:
            result.errors.append(f"{name}: budget exhausted")

    if promote:
        for record in learned.all(limit=500):
            if spent >= budget:
                result.errors.append("promotion: budget exhausted")
                break
            try:
                if _promote_if_ready(knowledge, learned, record, scrub_secrets):
                    result.promoted += 1
                elif _demote_if_contradicted(knowledge, learned, record):
                    result.demoted += 1
            except Exception as exc:
                result.errors.append(f"promote #{record.id}: {exc}")
                continue
            spent += 1
    result.elapsed_secs = round(time_module.monotonic() - started, 3)
    return result


def _promote_if_ready(knowledge: KnowledgeStore, learned, record, scrub) -> bool:
    from .learned import PROMOTE_CONFIDENCE

    if record.status != "established" or record.confidence < PROMOTE_CONFIDENCE:
        return False
    statement = scrub(f"{record.kind} {record.subject}: {record.predicate} "
                      f"(confidence {record.confidence:.2f}, "
                      f"{record.support_count} supporting experiences)")
    tags = ["learned", record.kind, record.subject.split(":")[0][:40] or record.kind]
    if record.knowledge_id is not None:
        try:
            linked = knowledge.get(record.knowledge_id)
        except ValueError:
            linked = None
        if linked is not None and linked.status == "active":
            known_sources = set(linked.source_ids)
            fresh = [i for i in record.supporting_ids if i not in known_sources]
            if not fresh:
                return False  # nothing new since last promotion
            knowledge.affirm(linked.id, fresh[-1])
            return True
    sources = list(record.supporting_ids) or list(record.contradicting_ids)
    if not sources:
        return False
    claim = knowledge.propose(statement, sources, record.confidence,
                              reason=f"promoted from learned {record.kind} #{record.id}",
                              tags=tags)
    learned.link_knowledge(record.id, claim.id)
    return True


def _demote_if_contradicted(knowledge: KnowledgeStore, learned, record) -> bool:
    if record.status != "contradicted" or record.knowledge_id is None:
        return False
    try:
        linked = knowledge.get(record.knowledge_id)
    except ValueError:
        return False
    if linked.status == "active":
        knowledge.mark_uncertain(linked.id, f"learned {record.kind} #{record.id} contradicted")
        return True
    return False
