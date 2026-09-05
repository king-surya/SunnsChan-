"""Personal understanding of Surya — derived, revisable, provenance-carrying.

This is the "I know Surya" layer, NOT a static profile table and NOT a dump
into every prompt. Understandings are derived records built on top of the
immutable event ledger (same shared SQLite file as knowledge/curiosity/learned
tables — the established pattern, never a second memory system).

The critical discipline is the KIND, which is never collapsed:

    fact        — Surya stated it directly (source is his own words).
    observation — Suns Chan noticed a recurring pattern of behaviour.
    preference  — a preference Surya expressed or repeatedly demonstrated.
    inference   — Suns Chan's own suspicion/guess (capped LOW, can never
                  silently become a fact without a direct statement).
    uncertainty — explicitly unsure; a question, not an answer.

Inference and uncertainty are confidence-capped so cognition can never
promote them into fact by accumulation alone. Every record links back to the
event(s) that produced it; corrections/contradictions create new records and
mark the old ones, preserving the revision trail — history is never destroyed.

This layer never grants authorization and never edits personality/policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from .comms import scrub_secrets
from .memory import tokenize
from .textutil import clamp, normalize_tags, signature


UNDERSTANDING_KINDS = (
    "fact",
    "observation",
    "preference",
    "inference",
    "uncertainty",
    "interest",
    "project",
    "identity",
)

STATUSES = ("active", "uncertain", "contradicted", "outdated", "superseded", "rejected", "archived")

# Upper confidence bound per kind. Inference/uncertainty can never reach the
# range a directly-stated fact occupies: cognition cannot upgrade a guess
# into a fact by counting sightings.
KIND_CONFIDENCE_CAP = {
    "fact": 1.0,
    "observation": 0.9,
    "preference": 0.9,
    "interest": 0.85,
    "project": 0.9,
    "identity": 0.9,
    "inference": 0.55,
    "uncertainty": 0.4,
}

# Confidence a statement starts at when first noted by the miner.
KIND_DEFAULT_CONFIDENCE = {
    "fact": 0.5,
    "observation": 0.6,
    "preference": 0.5,
    "interest": 0.5,
    "project": 0.55,
    "identity": 0.5,
    "inference": 0.4,
    "uncertainty": 0.35,
}


@dataclass(frozen=True)
class UnderstandingRecord:
    id: int
    kind: str
    statement: str
    confidence: float
    source_ids: tuple[int, ...]
    status: str
    supersedes_id: int | None
    created_at: datetime
    updated_at: datetime
    reason: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnderstandingSource:
    event_id: int
    kind: str
    text: str


@dataclass(frozen=True)
class UnderstandingExplanation:
    record_id: int
    kind: str
    statement: str
    status: str
    confidence: float
    supporting: tuple[UnderstandingSource, ...]
    contradicted_by: tuple[UnderstandingSource, ...]
    supersedes_id: int | None
    reason: str = ""


def _cap_for(kind: str) -> float:
    return KIND_CONFIDENCE_CAP.get(kind, 1.0)


def _default_confidence(kind: str) -> float:
    return KIND_DEFAULT_CONFIDENCE.get(kind, 0.5)


class UnderstandingStore:
    """Revisable personal understanding sharing the ledger's SQLite file."""

    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS understanding_records (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                statement TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_ids_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                supersedes_id INTEGER,
                reason TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]'
            )"""
        )
        self._connection.commit()

    def note(
        self,
        kind: str,
        statement: str,
        source_ids: list[int],
        confidence: float | None = None,
        *,
        reason: str = "",
        tags: list[str] | None = None,
    ) -> UnderstandingRecord:
        if kind not in UNDERSTANDING_KINDS:
            raise ValueError(f"unknown understanding kind: {kind}")
        if not statement.strip():
            raise ValueError("understanding statement must be non-empty")
        if not source_ids:
            raise ValueError("understanding must link to at least one source event")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        unique_sources = sorted(set(source_ids))
        self._require_sources_exist(unique_sources)
        clean_tags = normalize_tags(tags or [])
        value = clamp(confidence if confidence is not None else _default_confidence(kind))
        value = min(value, _cap_for(kind))
        now = datetime.now(UTC)
        cursor = self._connection.execute(
            """INSERT INTO understanding_records
               (created_at, updated_at, kind, statement, confidence,
                source_ids_json, status, supersedes_id, reason, tags_json)
               VALUES (?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?)""",
            (now.isoformat(), now.isoformat(), kind, scrub_secrets(statement.strip()),
             value, json.dumps(unique_sources), reason, json.dumps(clean_tags)),
        )
        self._connection.commit()
        return self.get(cursor.lastrowid)

    def affirm(self, record_id: int, source_id: int, *, boost: float = 0.08) -> UnderstandingRecord:
        record = self.get(record_id)
        if record.status != "active":
            raise ValueError("only an active record can be affirmed")
        self._require_sources_exist([source_id])
        sources = sorted(set(record.source_ids) | {source_id})
        confidence = min(clamp(record.confidence + boost), _cap_for(record.kind))
        self._connection.execute(
            "UPDATE understanding_records SET source_ids_json = ?, confidence = ?, updated_at = ? WHERE id = ?",
            (json.dumps(sources), confidence, datetime.now(UTC).isoformat(), record_id),
        )
        self._connection.commit()
        return self.get(record_id)

    def revise(
        self,
        old_id: int,
        new_kind: str,
        new_statement: str,
        source_ids: list[int],
        confidence: float | None = None,
        *,
        reason: str = "",
        tags: list[str] | None = None,
        contradict: bool = False,
    ) -> UnderstandingRecord:
        """Replace or contradict an old understanding, preserving both sides.

        `contradict=False` (default) supersedes: the new record replaces the
        old, which is marked superseded. `contradict=True` marks the old
        contradicted instead, keeping the conflict explainable. In both cases
        the old record and its provenance stay in history.
        """
        old = self.get(old_id)
        if old.status not in {"active", "uncertain"}:
            raise ValueError("only an active or uncertain record can be revised")
        if not new_statement.strip():
            raise ValueError("replacement statement must be non-empty")
        new = self.note(new_kind, new_statement, source_ids, confidence,
                        reason=reason, tags=tags)
        status = "contradicted" if contradict else "superseded"
        self._connection.execute(
            "UPDATE understanding_records SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now(UTC).isoformat(), old_id),
        )
        self._connection.execute(
            "UPDATE understanding_records SET supersedes_id = ? WHERE id = ?",
            (old_id, new.id),
        )
        self._connection.commit()
        return self.get(new.id)

    def mark_uncertain(self, record_id: int, reason: str) -> UnderstandingRecord:
        record = self.get(record_id)
        if record.status != "active":
            raise ValueError("only an active record can be marked uncertain")
        if not reason.strip():
            raise ValueError("uncertainty needs a reason")
        self._connection.execute(
            "UPDATE understanding_records SET status = 'uncertain', reason = ?, updated_at = ? WHERE id = ?",
            (reason.strip(), datetime.now(UTC).isoformat(), record_id),
        )
        self._connection.commit()
        return self.get(record_id)

    def mark_outdated(self, record_id: int, reason: str) -> UnderstandingRecord:
        record = self.get(record_id)
        if record.status not in {"active", "uncertain"}:
            raise ValueError("only an active or uncertain record can be marked outdated")
        if not reason.strip():
            raise ValueError("outdated needs a reason")
        self._connection.execute(
            "UPDATE understanding_records SET status = 'outdated', reason = ?, updated_at = ? WHERE id = ?",
            (reason.strip(), datetime.now(UTC).isoformat(), record_id),
        )
        self._connection.commit()
        return self.get(record_id)

    def get(self, record_id: int) -> UnderstandingRecord:
        row = self._connection.execute(
            "SELECT * FROM understanding_records WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown understanding record: {record_id}")
        return self._to_record(row)

    def active(self, *, limit: int = 100) -> list[UnderstandingRecord]:
        rows = self._connection.execute(
            "SELECT * FROM understanding_records WHERE status = 'active' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def visible(self, *, limit: int = 200) -> list[UnderstandingRecord]:
        rows = self._connection.execute(
            "SELECT * FROM understanding_records WHERE status IN "
            "('active', 'uncertain', 'contradicted', 'outdated') ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def history(self, *, limit: int = 500) -> list[UnderstandingRecord]:
        rows = self._connection.execute(
            "SELECT * FROM understanding_records ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def by_kind(self, kind: str, *, limit: int = 100) -> list[UnderstandingRecord]:
        if kind not in UNDERSTANDING_KINDS:
            raise ValueError(f"unknown understanding kind: {kind}")
        rows = self._connection.execute(
            "SELECT * FROM understanding_records WHERE kind = ? AND status IN "
            "('active', 'uncertain', 'contradicted', 'outdated') ORDER BY id DESC LIMIT ?",
            (kind, limit),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def relevant(self, query: str, *, limit: int = 5, embedder=None) -> list[UnderstandingRecord]:
        """Contextual retrieval: only what is relevant to `query`, never the
        whole profile. Token overlap first; optional semantic signal via
        `embedder` mirrors EventLedger.recall."""
        query_tokens = set(tokenize(query))
        records = self.visible(limit=500)
        if not query_tokens:
            return records[:limit]
        scored: list[tuple[float, int, UnderstandingRecord]] = []
        for record in records:
            statement_tokens = set(tokenize(record.statement))
            score = float(len(query_tokens & statement_tokens))
            for q in query_tokens:
                for s in statement_tokens:
                    if q != s and len(q) >= 4 and (s.startswith(q) or q.startswith(s)):
                        score += 1.0
                        break
            if embedder is not None:
                from .embeddings import cosine_similarity

                score += max(0.0, cosine_similarity(embedder.embed(query),
                                                    embedder.embed(record.statement)))
            scored.append((score, record.id, record))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [record for _, _, record in scored[:limit]]

    def explain(self, record_id: int) -> UnderstandingExplanation:
        record = self.get(record_id)
        supporting = tuple(self._resolve_sources(record.source_ids))
        successor = self._superseded_by(record_id)
        against: tuple[UnderstandingSource, ...] = ()
        if successor is not None:
            against = tuple(self._resolve_sources(successor.source_ids))
        return UnderstandingExplanation(
            record_id=record.id,
            kind=record.kind,
            statement=record.statement,
            status=record.status,
            confidence=record.confidence,
            supporting=supporting,
            contradicted_by=against,
            supersedes_id=record.supersedes_id,
            reason=record.reason,
        )

    def covered_source_ids(self) -> set[int]:
        covered: set[int] = set()
        rows = self._connection.execute("SELECT source_ids_json FROM understanding_records").fetchall()
        for row in rows:
            covered.update(json.loads(row["source_ids_json"]))
        return covered

    def close(self) -> None:
        self._connection.close()

    def _superseded_by(self, record_id: int) -> UnderstandingRecord | None:
        row = self._connection.execute(
            "SELECT * FROM understanding_records WHERE supersedes_id = ? ORDER BY id DESC LIMIT 1",
            (record_id,),
        ).fetchone()
        return self._to_record(row) if row is not None else None

    def _resolve_sources(self, source_ids: tuple[int, ...]) -> list[UnderstandingSource]:
        refs: list[UnderstandingSource] = []
        for source_id in source_ids:
            row = self._connection.execute(
                "SELECT id, kind, text FROM events WHERE id = ?", (source_id,)
            ).fetchone()
            if row is not None:
                refs.append(UnderstandingSource(source_id, row["kind"], row["text"]))
        return refs

    def _require_sources_exist(self, source_ids: list[int]) -> None:
        for source_id in source_ids:
            row = self._connection.execute(
                "SELECT id FROM events WHERE id = ?", (source_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"source event does not exist: {source_id}")

    @staticmethod
    def _to_record(row: sqlite3.Row) -> UnderstandingRecord:
        return UnderstandingRecord(
            id=row["id"],
            kind=row["kind"],
            statement=row["statement"],
            confidence=float(row["confidence"]),
            source_ids=tuple(json.loads(row["source_ids_json"])),
            status=row["status"],
            supersedes_id=row["supersedes_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            reason=row["reason"] or "",
            tags=tuple(json.loads(row["tags_json"] or "[]")),
        )


# ---------------------------------------------------------------------------
# Miner: ledger observations -> personal understanding. Deterministic, bounded,
# read-only over the ledger (writes go only to the UnderstandingStore). This is
# how Suns Chan gradually comes to know Surya through interaction rather than
# via a hard-coded profile.
# ---------------------------------------------------------------------------

from .reflection import CONTRAST_MARKERS, PREFERENCE_MARKERS  # noqa: E402


def _overlap_size(left: frozenset[str], right: frozenset[str]) -> int:
    matched: set[str] = set()
    for token in left:
        if any(t == token or (len(t) >= 4 and token.startswith(t)) or
               (len(token) >= 4 and t.startswith(token)) for t in right):
            matched.add(token)
    return len(matched)


def _mine_preference(store: UnderstandingStore, event, statement: str,
                     existing: dict[frozenset[str], UnderstandingRecord]) -> UnderstandingRecord:
    key = signature(statement)
    if key in existing:
        record = existing[key]
        if event.id not in record.source_ids:
            return store.affirm(record.id, event.id)
        return record
    record = store.note("preference", statement, [event.id],
                        reason="expressed preference", tags=["personal"])
    existing[key] = record
    return record


def _mine_fact(store: UnderstandingStore, event, statement: str,
               existing: dict[frozenset[str], UnderstandingRecord]) -> UnderstandingRecord:
    key = signature(statement)
    if key in existing:
        record = existing[key]
        if event.id not in record.source_ids:
            return store.affirm(record.id, event.id)
        return record
    record = store.note("fact", statement, [event.id],
                        reason="stated by Surya", tags=["personal"])
    existing[key] = record
    return record


def _mine_contradiction(store: UnderstandingStore, event, statement: str,
                        existing: dict[frozenset[str], UnderstandingRecord]) -> UnderstandingRecord | None:
    """A contrast statement revises the closest conflicting understanding.

    Never silently overwrites: the old record is marked contradicted and the
    new statement becomes active, so both sides stay explainable in history.
    Exact matches are handled by the caller (they affirm, not contradict).
    """
    tokens = signature(statement)
    best: tuple[int, UnderstandingRecord] | None = None
    for key, record in existing.items():
        if key == tokens:
            continue  # identical statement is a repeat, not a conflict
        if record.status not in {"active", "uncertain"}:
            continue  # already resolved records are not revisable targets
        overlap = _overlap_size(tokens, key)
        if overlap >= 2 and (best is None or overlap > best[0]):
            best = (overlap, record)
    if best is None:
        return None
    return store.revise(best[1].id, "fact", statement, [event.id],
                        reason=f"revised after contradiction (event {event.id})",
                        tags=["personal"], contradict=True)


def mine_understanding(ledger, understanding, *, limit: int = 200,
                       kinds: tuple[str, ...] = ("observation", "experience")) -> list[UnderstandingRecord]:
    """Turn recent user statements into revisable understanding.

    Preference/contrast markers classify statements; everything else
    substantive becomes a `fact` at BELIEF confidence (a single statement is
    not certainty). Repeated statements affirm upward; a contrast marker
    revises the closest conflicting record without erasing it. Questions are
    ignored. Idempotent per event: re-running never double-counts.
    """
    events: list = []
    for kind in kinds:
        events.extend(ledger.events_of_kind(kind, limit=limit))
    events.sort(key=lambda e: e.id)
    touched: list[UnderstandingRecord] = []
    existing: dict[frozenset[str], UnderstandingRecord] = {
        signature(r.statement): r for r in understanding.visible(limit=500)
    }
    for event in events:
        text = (event.text or "").strip()
        if not text or text.endswith("?") or len(signature(text)) < 3:
            continue
        lowered = text.lower()
        key = signature(text)
        # Exact repeat of a known statement affirms, never duplicates.
        if key in existing:
            if event.id not in existing[key].source_ids:
                record = understanding.affirm(existing[key].id, event.id)
            else:
                record = None  # already counted; idempotent skip
        elif any(marker in lowered for marker in CONTRAST_MARKERS):
            record = _mine_contradiction(understanding, event, text, existing)
        elif any(marker in lowered for marker in PREFERENCE_MARKERS):
            record = _mine_preference(understanding, event, text, existing)
        else:
            record = _mine_fact(understanding, event, text, existing)
        if record is not None:
            existing[signature(record.statement)] = record
            touched.append(record)
    return touched


def store_affirm(store: UnderstandingStore, record_id: int, source_id: int) -> UnderstandingRecord:
    return store.affirm(record_id, source_id)


def refresh_understanding(store: UnderstandingStore, *, max_age_days: int = 120,
                          now=None) -> list[int]:
    """Mark stale, unaffirmed inferences/preferences outdated. History kept."""
    from datetime import timedelta

    moment = now or datetime.now(UTC)
    marked: list[int] = []
    for record in store.visible(limit=500):
        if record.status != "active":
            continue
        if record.kind in ("fact", "observation") and moment - record.updated_at > timedelta(days=max_age_days):
            continue
        if record.kind in ("inference", "uncertainty", "preference", "interest") \
                and moment - record.updated_at > timedelta(days=max_age_days):
            store.mark_outdated(record.id, f"unreaffirmed for over {max_age_days} days")
            marked.append(record.id)
    return marked
