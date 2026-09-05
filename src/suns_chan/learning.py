"""Evidence-accumulation learning. One observation is never proof.

Preferences start as UNCERTAIN candidates and are promoted to ACTIVE only
after `threshold` distinct supporting events (default 3). Explicit user
corrections are authoritative and supersede immediately — that is a deliberate
user instruction, not statistical inference. Skills are proposed with source
links; repetition affirms them. Everything lands in the KnowledgeStore, so
provenance, supersession and contradiction handling apply uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .knowledge import KnowledgeRecord, KnowledgeStore
from .memory import EventLedger, tokenize
from .state import AgentState


@dataclass(frozen=True)
class PreferenceState:
    tag: str
    occurrences: int
    status: str  # "candidate" | "adopted"
    record_ids: tuple[int, ...]
    confidence: float


class LearningTracker:
    def __init__(self, store: KnowledgeStore, *, threshold: int = 3) -> None:
        if threshold < 2:
            raise ValueError("threshold must be at least 2 (single observations never adopt)")
        self._store = store
        self._threshold = threshold

    @property
    def threshold(self) -> int:
        return self._threshold

    def _preference_records(self, tag: str) -> list[KnowledgeRecord]:
        wanted = tag.strip().lower()
        return [
            record
            for record in self._store.visible(limit=500)
            if "preference" in record.tags and wanted in record.tags
        ]

    def observe_preference(self, tag: str, statement: str, source_id: int) -> PreferenceState:
        """Accumulate one sighting. Returns candidate until threshold is met."""
        normalized = tag.strip().lower()
        if not normalized:
            raise ValueError("preference tag must be non-empty")
        if not statement.strip():
            raise ValueError("preference statement must be non-empty")
        existing = self._preference_records(normalized)
        occurrences = len(existing) + 1
        if occurrences >= self._threshold:
            adopted = [r for r in existing if r.status == "active"]
            if adopted:
                record = self._store.affirm(adopted[0].id, source_id)
            else:
                record = self._store.propose(
                    statement, [source_id], 0.75,
                    reason=f"adopted after {occurrences} supporting observations",
                    tags=["preference", normalized],
                )
            records = [r for r in existing if r.status == "active"] or [record]
            return PreferenceState(normalized, occurrences, "adopted",
                                   tuple(r.id for r in records), record.confidence)
        record = self._store.propose(
            statement, [source_id], 0.35,
            reason=f"candidate ({occurrences}/{self._threshold} supporting observations)",
            tags=["preference", normalized],
        )
        uncertain = self._store.mark_uncertain(record.id, "single weak evidence so far")
        return PreferenceState(normalized, occurrences, "candidate", (uncertain.id,), uncertain.confidence)

    def observe_correction(
        self, old_record_id: int, new_statement: str, source_id: int, *, reason: str = "explicit user correction"
    ) -> KnowledgeRecord:
        """Authoritative correction: supersede immediately with source linkage."""
        return self._store.supersede(old_record_id, new_statement, [source_id], 0.8, reason=reason)

    def observe_repeated_failure(self, statement: str, source_id: int) -> KnowledgeRecord:
        """A repeated failure becomes a lesson candidate, not instant doctrine."""
        return self._store.propose(statement, [source_id], 0.55, reason="repeated failure", tags=["lesson"])

    def propose_skill(self, statement: str, source_ids: list[int], confidence: float = 0.6) -> KnowledgeRecord:
        return self._store.propose(statement, source_ids, confidence, reason="extracted workflow", tags=["skill"])

    def preference_state(self, tag: str) -> PreferenceState | None:
        records = self._preference_records(tag.strip().lower())
        if not records:
            return None
        adopted = [r for r in records if r.status == "active"]
        status = "adopted" if adopted else "candidate"
        confidence = max(r.confidence for r in records)
        return PreferenceState(tag.strip().lower(), len(records), status,
                               tuple(r.id for r in records), confidence)


def tend_interests(ledger: EventLedger, *, idle_days: int = 30,
                   now: datetime | None = None, decay: float = 0.05,
                   drop_below: float = 0.15) -> dict[str, float]:
    """Let interests weaken and disappear without recent behavioral support.

    Growth still happens only through lived turns (AgentState.express_interest).
    Here, each interest keeps its level when any recent event mentions it
    (text tokens or recorded interest tags); otherwise it decays. Dropped
    tags vanish from state — history in the ledger is untouched. Returns the
    resulting interest map. Deterministic given `now`.
    """
    if idle_days < 1:
        raise ValueError("idle_days must be at least 1")
    moment = now or datetime.now(UTC)
    state = AgentState.from_dict(ledger.load_state())
    if not state.interests:
        return {}
    recent_tokens: set[str] = set()
    for event in ledger.events_of_kind("observation", limit=200) + \
            ledger.events_of_kind("experience", limit=200):
        if (moment - event.occurred_at).days <= idle_days:
            recent_tokens.update(tokenize(event.text))
            for tag in event.metadata.get("interest_tags", []) or []:
                if isinstance(tag, str):
                    recent_tokens.add(tag.strip().lower())
    changed: dict[str, float] = {}
    for tag in list(state.interests):
        if tag in recent_tokens:
            continue
        level = round(max(0.0, state.interests[tag] - decay), 4)
        if level < drop_below:
            del state.interests[tag]
            changed[tag] = 0.0
        else:
            state.interests[tag] = level
            changed[tag] = level
    if changed:
        ledger.save_state(state.to_dict(), cause_event_id=None)
    return changed
