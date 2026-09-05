"""Self-improving learning layer: evidence-backed learned objects.

Learned objects (patterns, skills, preferences, strategies) live in their
own table in the SHARED database file (curiosity-table precedent — not a
second memory system). Raw events are never touched; derived claims promote
into the existing KnowledgeStore only at evidence thresholds, keeping
provenance, statuses, and contradiction handling uniform.

Confidence is a transparent formula over evidence counts, diversity, and
recency — every value explainable via explain_learning(). Learning NEVER
authorizes: no output here reaches PolicyGate, tools, or infrastructure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from .comms import scrub_secrets
from .memory import EventLedger, require_event, tokenize
from .textutil import jaccard, signature as _token_signature


KINDS = ("pattern", "skill", "preference", "strategy")
STATUSES = ("candidate", "established", "uncertain", "contradicted", "superseded")

MIN_SUPPORT = 3          # supporting events before anything establishes
PROMOTE_CONFIDENCE = 0.6  # confidence needed to promote a knowledge claim


@dataclass(frozen=True)
class LearnedObject:
    id: int
    kind: str
    subject: str
    predicate: str
    conditions: dict
    confidence: float
    support_count: int
    contra_count: int
    supporting_ids: tuple[int, ...]
    contradicting_ids: tuple[int, ...]
    status: str
    knowledge_id: int | None
    created_at: datetime
    updated_at: datetime
    last_observed: datetime


@dataclass(frozen=True)
class ConfidenceBreakdown:
    confidence: float
    support: int
    contradictions: int
    evidence_term: float
    diversity_bonus: float
    recency_bonus: float
    reason: str


def compute_confidence(support: int, contra: int, *, diversity: int = 0,
                       recent: bool = False) -> ConfidenceBreakdown:
    """Transparent confidence. No hidden increments.

    base 0.5, evidence term 0.4*(S-C)/(S+C+4) (strong smoothing, so a lone
    sighting stays below promotion range), diversity +0.02 per distinct
    evidence day (cap 0.1), recency +0.03 when observed recently. Clamped
    [0.05, 0.95].
    """
    if support < 0 or contra < 0:
        raise ValueError("evidence counts must be >= 0")
    total = support + contra + 4
    evidence_term = round(0.4 * (support - contra) / total, 4)
    diversity_bonus = round(min(0.1, 0.02 * max(0, diversity)), 4)
    recency_bonus = 0.03 if recent else 0.0
    confidence = round(max(0.05, min(0.95, 0.5 + evidence_term + diversity_bonus + recency_bonus)), 4)
    reason = (f"{support} supporting, {contra} contradicting; "
              f"evidence {evidence_term:+.3f}, diversity +{diversity_bonus:.2f}, "
              f"recency +{recency_bonus:.2f}")
    return ConfidenceBreakdown(confidence, support, contra, evidence_term,
                               diversity_bonus, recency_bonus, reason)


def status_for(support: int, contra: int, confidence: float) -> str:
    if contra > support and support + contra >= 2:
        return "contradicted"
    if support < MIN_SUPPORT:
        return "candidate"
    if confidence < 0.4 or (contra > 0 and contra / max(1, support) >= 0.5):
        return "uncertain"
    return "established"


class LearnedStore:
    """Learned objects sharing the ledger's SQLite file. Atomic batch writes."""

    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS learned_objects (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL DEFAULT '',
                conditions_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.5,
                support_ids_json TEXT NOT NULL DEFAULT '[]',
                contra_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'candidate',
                knowledge_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_observed TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_learned_identity ON learned_objects(kind, subject)"
        )
        self._connection.commit()

    def observe(self, kind: str, subject: str, event_id: int, *,
                predicate: str = "", conditions: dict | None = None,
                contradicts: bool = False) -> LearnedObject:
        """Record one evidence sighting. Creates candidate or updates counts,
        recomputes confidence, and re-derives status. Never rewrites history."""
        if kind not in KINDS:
            raise ValueError(f"unknown learned kind: {kind}")
        if not subject.strip():
            raise ValueError("subject must be non-empty")
        self._require_event(event_id)
        subject = scrub_secrets(subject.strip())[:300]
        now = datetime.now(UTC).isoformat()
        row = self._connection.execute(
            "SELECT * FROM learned_objects WHERE kind = ? AND subject = ?",
            (kind, subject.strip())).fetchone()
        if row is None:
            support = [event_id] if not contradicts else []
            contra = [event_id] if contradicts else []
            cursor = self._connection.execute(
                """INSERT INTO learned_objects
                   (kind, subject, predicate, conditions_json, confidence,
                    support_ids_json, contra_ids_json, status, knowledge_id,
                    created_at, updated_at, last_observed)
                   VALUES (?, ?, ?, ?, 0.5, ?, ?, 'candidate', NULL, ?, ?, ?)""",
                (kind, subject.strip(), predicate, json.dumps(conditions or {}),
                 json.dumps(support), json.dumps(contra), now, now, now))
            self._connection.commit()
            return self.get(cursor.lastrowid)
        support = json.loads(row["support_ids_json"])
        contra = json.loads(row["contra_ids_json"])
        if event_id in support or event_id in contra:
            return self.get(row["id"])  # idempotent: same evidence counted once
        (contra if contradicts else support).append(event_id)
        conditions_merged = json.loads(row["conditions_json"])
        for key, value in (conditions or {}).items():
            conditions_merged.setdefault(key, value)
        breakdown = compute_confidence(len(support), len(contra), recent=True,
                                       diversity=len(set(support + contra)))
        status = status_for(len(support), len(contra), breakdown.confidence)
        self._connection.execute(
            """UPDATE learned_objects SET support_ids_json = ?, contra_ids_json = ?,
               conditions_json = ?, confidence = ?, status = ?, updated_at = ?,
               last_observed = ? WHERE id = ?""",
            (json.dumps(support), json.dumps(contra), json.dumps(conditions_merged),
             breakdown.confidence, status, now, now, row["id"]))
        self._connection.commit()
        return self.get(row["id"])

    def apply_correction(self, learned_id: int, event_id: int, *, note: str = "") -> LearnedObject:
        """Explicit correction: strong contradicting evidence with provenance.

        Corrections are authoritative: even when outnumbered, they cap the
        status at uncertain until fresh supporting evidence arrives.
        """
        self._require_event(event_id)
        record = self.get(learned_id)
        contra = list(record.contradicting_ids) + [event_id]
        breakdown = compute_confidence(record.support_count, len(contra), recent=True,
                                       diversity=len(set(record.supporting_ids + tuple(contra))))
        status = status_for(record.support_count, len(contra), breakdown.confidence)
        if status == "established":
            status = "uncertain"
        now = datetime.now(UTC).isoformat()
        self._connection.execute(
            """UPDATE learned_objects SET contra_ids_json = ?, confidence = ?,
               status = ?, updated_at = ?, last_observed = ? WHERE id = ?""",
            (json.dumps(contra), breakdown.confidence, status, now, now, learned_id))
        self._connection.commit()
        return self.get(learned_id)

    def apply_decay(self, learned_id: int, *, reason: str) -> LearnedObject:
        """Semantically justified decay: stale, unobserved learning loses confidence."""
        if not reason.strip():
            raise ValueError("decay needs a reason")
        record = self.get(learned_id)
        confidence = round(max(0.05, record.confidence - 0.1), 4)
        status = status_for(record.support_count, record.contra_count, confidence)
        if record.status == "contradicted":
            status = "contradicted"
        now = datetime.now(UTC).isoformat()
        self._connection.execute(
            "UPDATE learned_objects SET confidence = ?, status = ?, updated_at = ? WHERE id = ?",
            (confidence, status, now, learned_id))
        self._connection.commit()
        return self.get(learned_id)

    def link_knowledge(self, learned_id: int, knowledge_id: int) -> LearnedObject:
        self.get(learned_id)
        self._connection.execute(
            "UPDATE learned_objects SET knowledge_id = ?, updated_at = ? WHERE id = ?",
            (knowledge_id, datetime.now(UTC).isoformat(), learned_id))
        self._connection.commit()
        return self.get(learned_id)

    def get(self, learned_id: int) -> LearnedObject:
        row = self._connection.execute(
            "SELECT * FROM learned_objects WHERE id = ?", (learned_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown learned object: {learned_id}")
        return self._to_record(row)

    def by_kind(self, kind: str, *, statuses: tuple[str, ...] | None = None,
                limit: int = 200) -> list[LearnedObject]:
        if kind not in KINDS:
            raise ValueError(f"unknown learned kind: {kind}")
        if statuses is None:
            rows = self._connection.execute(
                "SELECT * FROM learned_objects WHERE kind = ? ORDER BY id DESC LIMIT ?",
                (kind, limit)).fetchall()
        else:
            unknown = set(statuses) - set(STATUSES)
            if unknown:
                raise ValueError(f"unknown statuses: {sorted(unknown)}")
            placeholders = ",".join("?" for _ in statuses)
            rows = self._connection.execute(
                f"SELECT * FROM learned_objects WHERE kind = ? AND status IN ({placeholders})"
                " ORDER BY id DESC LIMIT ?",
                (kind, *statuses, limit)).fetchall()
        return [self._to_record(row) for row in rows]

    def all(self, *, limit: int = 500) -> list[LearnedObject]:
        rows = self._connection.execute(
            "SELECT * FROM learned_objects ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._to_record(row) for row in rows]

    def close(self) -> None:
        self._connection.close()

    def _require_event(self, event_id: int) -> None:
        require_event(self._connection, event_id)

    @staticmethod
    def _to_record(row: sqlite3.Row) -> LearnedObject:
        support = json.loads(row["support_ids_json"])
        contra = json.loads(row["contra_ids_json"])
        return LearnedObject(
            id=row["id"], kind=row["kind"], subject=row["subject"],
            predicate=row["predicate"] or "",
            conditions=json.loads(row["conditions_json"]),
            confidence=float(row["confidence"]),
            support_count=len(support), contra_count=len(contra),
            supporting_ids=tuple(support), contradicting_ids=tuple(contra),
            status=row["status"], knowledge_id=row["knowledge_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_observed=datetime.fromisoformat(row["last_observed"]),
        )


# ---------------------------------------------------------------------------
# Miners: ledger events -> learned objects. Deterministic, bounded, read-only
# over the ledger (writes go only to the learned store).
# ---------------------------------------------------------------------------

def _group_by_similarity(items: list, *, threshold: float = 0.5) -> list[list]:
    """Greedy Jaccard clustering over token signatures (reflection parity)."""
    clusters: list[list] = []
    keyed = [(item, _token_signature(item[0])) for item in items]
    for (text, tokens), item in zip(keyed, items):
        placed = False
        for cluster in clusters:
            if jaccard(tokens, _token_signature(cluster[0][0])) >= threshold:
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return clusters


def _outcome_of(event) -> str:
    if event.kind == "outcome":
        return str(event.metadata.get("outcome", "unknown"))
    return str(event.metadata.get("result", "unknown"))


def mine_patterns(ledger: EventLedger, learned: LearnedStore, *,
                  limit: int = 200) -> list[LearnedObject]:
    """Cluster outcome/experience events by scrubbed signature.

    One pattern object per (signature, outcome) — a contextual distinction,
    not a universal claim. Each observation supports its own outcome object
    and contradicts SAME-signature objects of other outcomes (both sides
    preserved, neither deleted). Re-runs are idempotent per evidence id.
    """
    events = ([e for e in ledger.events_of_kind("outcome", limit=limit)] +
              [e for e in ledger.events_of_kind("experience", limit=limit)])
    if not events:
        return []
    touched: list[LearnedObject] = []
    # Loaded once: per-cluster table scans would make mining quadratic.
    known_patterns = {o.subject: o for o in learned.by_kind("pattern")}
    for cluster in _group_by_similarity([(e.text, e) for e in events]):
        if len(cluster) < 2:
            continue
        signature = " ".join(sorted(_token_signature(cluster[0][0]))[:6])
        # Pass 1: every outcome gets its contextual object (support only).
        for _, event in cluster:
            outcome = _outcome_of(event)
            subject = f"pattern:{outcome}:{signature}"
            obj = learned.observe(
                "pattern", subject, event.id,
                predicate=f"tends to {outcome}", conditions={"outcome": outcome})
            known_patterns[subject] = obj
            touched.append(obj)
        # Pass 2: every observation contradicts same-signature objects of
        # other outcomes. Order-independent: pass 1 created all siblings.
        siblings = {s: o for s, o in known_patterns.items() if f":{signature}" in s}
        for _, event in cluster:
            own = f"pattern:{_outcome_of(event)}:{signature}"
            for sibling_subject, sibling in siblings.items():
                if sibling_subject == own or event.id in sibling.supporting_ids \
                        or event.id in sibling.contradicting_ids:
                    continue
                touched.append(learned.observe("pattern", sibling_subject, event.id,
                                               predicate=sibling.predicate,
                                               conditions=dict(sibling.conditions),
                                               contradicts=True))
    return touched


def mine_skills(ledger: EventLedger, learned: LearnedStore, *, limit: int = 200) -> list[LearnedObject]:
    """Demonstrated performance: pair activity_started (category) with
    activity_finished (result) by activity_key. Successes support, failures
    contradict. Each attempt feeds BOTH the parent skill (category) and an
    emergent sub-skill (category + activity slug) — sub-skills appear only
    after repeated clustering, never from a single event. Conditions carry
    the goal context (or general) so competence stays contextual."""
    started = {e.metadata.get("activity_key"): e
               for e in ledger.events_of_kind("activity_started", limit=limit)}
    goals = sorted(ledger.events_of_kind("goal_opened", limit=limit), key=lambda e: e.id)
    touched: list[LearnedObject] = []
    for finished in ledger.events_of_kind("activity_finished", limit=limit):
        key = finished.metadata.get("activity_key")
        start = started.get(key)
        if start is None:
            continue
        category = str(start.metadata.get("category", "UNKNOWN"))
        result = str(finished.metadata.get("result", "unknown"))
        if result not in {"success", "failure"}:
            continue
        goal_text = "general"
        for goal in goals:
            if goal.id < finished.id:
                goal_text = goal.text
            else:
                break
        slug_words = [w for w in tokenize(start.text)[:3] if w]
        slug = "-".join(slug_words)[:40] or "general"
        conditions = {"category": category, "context": goal_text[:120]}
        contradicts = result == "failure"
        touched.append(learned.observe("skill", f"skill:{category.lower()}", finished.id,
                                       predicate="demonstrated capability",
                                       conditions=conditions, contradicts=contradicts))
        touched.append(learned.observe("skill", f"skill:{category.lower()}:{slug}", finished.id,
                                       predicate="demonstrated capability",
                                       conditions=conditions, contradicts=contradicts))
    return touched


def mine_preferences(ledger: EventLedger, learned: LearnedStore, *, limit: int = 200) -> list[LearnedObject]:
    """Voluntary selections are preference evidence; stuck-avoidance is
    counter-evidence. The stuck event's category resolves through its
    matching activity_started record."""
    touched: list[LearnedObject] = []
    started_by_key = {e.metadata.get("activity_key"): e
                      for e in ledger.events_of_kind("activity_started", limit=limit)}
    for event in ledger.events_of_kind("activity_started", limit=limit):
        category = str(event.metadata.get("category", ""))
        if not category:
            continue
        touched.append(learned.observe("preference", f"prefers:{category.lower()}", event.id,
                                       predicate="voluntarily selected",
                                       conditions={"category": category}))
    for event in ledger.events_of_kind("activity_finished", limit=limit):
        if not event.metadata.get("stuck"):
            continue
        start = started_by_key.get(event.metadata.get("activity_key"))
        category = str((start.metadata.get("category", "") if start else ""))
        if category:
            touched.append(learned.observe(
                "preference", f"prefers:{category.lower()}", event.id,
                predicate="voluntarily selected",
                conditions={"category": category}, contradicts=True))
    return touched


def mine_strategies(ledger: EventLedger, learned: LearnedStore, *, limit: int = 200) -> list[LearnedObject]:
    """Strategy = approach (activity category) in a problem context (goal).

    Context comes from experience goal_id metadata; goal-less activity is
    the "general" context. Never claimed universal beyond observed goals.
    """
    started = sorted(ledger.events_of_kind("activity_started", limit=limit),
                     key=lambda e: e.id)
    touched: list[LearnedObject] = []
    for exp in ledger.events_of_kind("experience", limit=limit):
        goal_id = exp.metadata.get("goal_id")
        context = f"goal-{goal_id}" if goal_id else "general"
        # Temporal join: the activity running when this experience was recorded
        # is the latest activity_started event preceding it.
        category = "UNKNOWN"
        for start in started:
            if start.id < exp.id:
                category = str(start.metadata.get("category", "UNKNOWN"))
            else:
                break
        result = str(exp.metadata.get("result", "unknown"))
        subject = f"strategy:{context}:{category.lower()}"
        conditions = {"context": context, "category": category}
        if result == "success":
            touched.append(learned.observe("strategy", subject, exp.id,
                                           predicate="effective approach",
                                           conditions=conditions))
        elif result == "failure":
            touched.append(learned.observe("strategy", subject, exp.id,
                                           predicate="effective approach",
                                           conditions=conditions, contradicts=True))
    return touched


def strategy_bonus_for(learned: LearnedStore, *, goal_id: int | None,
                       category: str) -> float:
    """Contextual planning multiplier, bounded [0.7, 1.3].

    Established strategy for this context+category: 1.2. Contradicted: 0.7.
    Everything else (candidate/uncertain/unknown): 1.0 — exploration intact.
    """
    context = f"goal-{goal_id}" if goal_id else "general"
    subject = f"strategy:{context}:{category.lower()}"
    matches = [o for o in learned.by_kind("strategy") if o.subject == subject]
    if not matches:
        general = [o for o in learned.by_kind("strategy")
                   if o.subject == f"strategy:general:{category.lower()}"
                   and o.status == "established"]
        return 1.1 if general else 1.0
    record = matches[0]
    if record.status == "established" and record.confidence >= PROMOTE_CONFIDENCE:
        return 1.2
    if record.status == "contradicted":
        return 0.7
    return 1.0


def decay_stale(learned: LearnedStore, *, older_than_days: int = 30,
                now: datetime | None = None) -> list[LearnedObject]:
    """Justified decay: established objects unobserved for a long time lose
    confidence with an explicit reason. Nothing is deleted."""
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    moment = now or datetime.now(UTC)
    decayed: list[LearnedObject] = []
    for record in learned.all():
        if record.status == "established" and (moment - record.last_observed) > timedelta(days=older_than_days):
            decayed.append(learned.apply_decay(
                record.id, reason=f"unobserved for over {older_than_days} days"))
    return decayed


@dataclass(frozen=True)
class LearningExplanation:
    what: str
    confidence: float
    breakdown: str
    supporting: tuple[tuple[int, str, str], ...]  # (event_id, kind, snippet)
    contradictions: tuple[tuple[int, str, str], ...]
    conditions: dict
    status: str
    last_observed: datetime
    knowledge_id: int | None


def explain_learning(learned: LearnedStore, ledger: EventLedger, learned_id: int) -> LearningExplanation:
    """Why do you believe this? Full provenance in one structure."""
    record = learned.get(learned_id)
    breakdown = compute_confidence(record.support_count, record.contra_count,
                                   recent=True, diversity=len(set(
                                       record.supporting_ids + record.contradicting_ids)))

    def resolve(ids: tuple[int, ...]) -> tuple[tuple[int, str, str], ...]:
        refs: list[tuple[int, str, str]] = []
        for event_id in ids:
            row = ledger._connection.execute(
                "SELECT kind, text FROM events WHERE id = ?", (event_id,)).fetchone()
            if row is not None:
                refs.append((event_id, row["kind"], row["text"][:160]))
        return tuple(refs)

    return LearningExplanation(
        what=f"{record.kind} {record.subject}: {record.predicate}",
        confidence=record.confidence,
        breakdown=breakdown.reason,
        supporting=resolve(record.supporting_ids),
        contradictions=resolve(record.contradicting_ids),
        conditions=dict(record.conditions),
        status=record.status,
        last_observed=record.last_observed,
        knowledge_id=record.knowledge_id,
    )
