"""Derived knowledge built on top of immutable events.

Raw events are never rewritten. A KnowledgeRecord is a revisable claim that
must link back to its source event IDs. Corrections create a new record and
mark the old one superseded; rejections mark it rejected. History is kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from .memory import tokenize
from .textutil import clamp, normalize_tags


@dataclass(frozen=True)
class KnowledgeRecord:
    id: int
    statement: str
    confidence: float
    source_ids: tuple[int, ...]
    status: str  # active|uncertain|contradicted|outdated|superseded|rejected|archived
    supersedes_id: int | None
    created_at: datetime
    updated_at: datetime
    reason: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRef:
    event_id: int
    kind: str
    text: str


@dataclass(frozen=True)
class BeliefExplanation:
    """Why a belief exists: current claim, evidence for/against, full provenance."""

    record_id: int
    statement: str
    status: str
    confidence: float
    supporting: tuple[SourceRef, ...]
    contradicted_by: tuple[SourceRef, ...]
    supersedes_id: int | None
    reason: str = ""


class KnowledgeStore:
    """Source-linked knowledge view. Shares the ledger's SQLite file."""

    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS knowledge_records (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                statement TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_ids_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                supersedes_id INTEGER,
                reason TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._connection.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Add Phase 2 columns to pre-existing databases; backfill sensibly."""
        columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(knowledge_records)")}
        if "updated_at" not in columns:
            self._connection.execute("ALTER TABLE knowledge_records ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        if "tags_json" not in columns:
            self._connection.execute("ALTER TABLE knowledge_records ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
        self._connection.execute(
            "UPDATE knowledge_records SET updated_at = created_at WHERE updated_at = ''"
        )
        self._connection.commit()

    def propose(
        self,
        statement: str,
        source_ids: list[int],
        confidence: float = 0.6,
        *,
        reason: str = "",
        tags: list[str] | None = None,
    ) -> KnowledgeRecord:
        if not statement.strip():
            raise ValueError("knowledge statement must be non-empty")
        if not source_ids:
            raise ValueError("knowledge must link to at least one source event")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        unique_sources = sorted(set(source_ids))
        self._require_sources_exist(unique_sources)
        clean_tags = normalize_tags(tags or [])
        now = datetime.now(UTC)
        cursor = self._connection.execute(
            """INSERT INTO knowledge_records
               (created_at, updated_at, statement, confidence, source_ids_json, status, supersedes_id, reason, tags_json)
               VALUES (?, ?, ?, ?, ?, 'active', NULL, ?, ?)""",
            (now.isoformat(), now.isoformat(), statement.strip(), clamp(confidence),
             json.dumps(unique_sources), reason, json.dumps(clean_tags)),
        )
        self._connection.commit()
        return self.get(cursor.lastrowid)

    def affirm(self, record_id: int, source_id: int, *, boost: float = 0.05) -> KnowledgeRecord:
        record = self.get(record_id)
        if record.status != "active":
            raise ValueError("only an active record can be affirmed")
        self._require_sources_exist([source_id])
        sources = sorted(set(record.source_ids) | {source_id})
        confidence = clamp(record.confidence + boost)
        self._connection.execute(
            "UPDATE knowledge_records SET source_ids_json = ?, confidence = ?, updated_at = ? WHERE id = ?",
            (json.dumps(sources), confidence, datetime.now(UTC).isoformat(), record_id),
        )
        self._connection.commit()
        return self.get(record_id)

    def supersede(
        self,
        old_id: int,
        new_statement: str,
        source_ids: list[int],
        confidence: float = 0.6,
        *,
        reason: str = "",
    ) -> KnowledgeRecord:
        old = self.get(old_id)
        if old.status not in {"active", "uncertain"}:
            raise ValueError("only an active or uncertain record can be superseded")
        if not new_statement.strip():
            raise ValueError("replacement statement must be non-empty")
        new = self.propose(new_statement, source_ids, confidence, reason=reason)
        self._connection.execute(
            "UPDATE knowledge_records SET status = 'superseded', updated_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), old_id),
        )
        self._connection.execute(
            "UPDATE knowledge_records SET supersedes_id = ? WHERE id = ?", (old_id, new.id)
        )
        self._connection.commit()
        return self.get(new.id)

    def reject(self, record_id: int, reason: str) -> KnowledgeRecord:
        record = self.get(record_id)
        if record.status not in {"active", "uncertain"}:
            raise ValueError("only an active or uncertain record can be rejected")
        if not reason.strip():
            raise ValueError("rejection needs a reason")
        self._connection.execute(
            "UPDATE knowledge_records SET status = 'rejected', reason = ?, updated_at = ? WHERE id = ?",
            (reason.strip(), datetime.now(UTC).isoformat(), record_id),
        )
        self._connection.commit()
        return self.get(record_id)

    def mark_uncertain(self, record_id: int, reason: str) -> KnowledgeRecord:
        """Flag an active claim as uncertain without replacing it. History kept."""
        record = self.get(record_id)
        if record.status != "active":
            raise ValueError("only an active record can be marked uncertain")
        if not reason.strip():
            raise ValueError("uncertainty needs a reason")
        self._connection.execute(
            "UPDATE knowledge_records SET status = 'uncertain', reason = ?, updated_at = ? WHERE id = ?",
            (reason.strip(), datetime.now(UTC).isoformat(), record_id),
        )
        self._connection.commit()
        return self.get(record_id)

    def mark_outdated(self, record_id: int, reason: str) -> KnowledgeRecord:
        """Flag an active or uncertain claim as outdated (temporal staleness).

        Distinct from uncertain (weak evidence): outdated means the world may
        have moved on. History kept; refresh via affirm or supersede.
        """
        record = self.get(record_id)
        if record.status not in {"active", "uncertain"}:
            raise ValueError("only an active or uncertain record can be marked outdated")
        if not reason.strip():
            raise ValueError("outdated needs a reason")
        self._connection.execute(
            "UPDATE knowledge_records SET status = 'outdated', reason = ?, updated_at = ? WHERE id = ?",
            (reason.strip(), datetime.now(UTC).isoformat(), record_id),
        )
        self._connection.commit()
        return self.get(record_id)

    def archive(self, record_id: int, reason: str) -> KnowledgeRecord:
        """Retire a record from the visible view; history retains it."""
        record = self.get(record_id)
        if record.status == "archived":
            raise ValueError("record is already archived")
        if not reason.strip():
            raise ValueError("archive needs a reason")
        self._connection.execute(
            "UPDATE knowledge_records SET status = 'archived', reason = ?, updated_at = ? WHERE id = ?",
            (reason.strip(), datetime.now(UTC).isoformat(), record_id),
        )
        self._connection.commit()
        return self.get(record_id)

    def contradict(
        self,
        old_id: int,
        new_statement: str,
        source_ids: list[int],
        confidence: float = 0.6,
        *,
        reason: str = "",
        tags: list[str] | None = None,
    ) -> KnowledgeRecord:
        """Record conflicting evidence. The old claim is preserved and marked
        contradicted; the new claim becomes active and links back to it.
        Unlike supersede (replacement), both sides stay explainable."""
        old = self.get(old_id)
        if old.status not in {"active", "uncertain"}:
            raise ValueError("only an active or uncertain record can be contradicted")
        if not new_statement.strip():
            raise ValueError("contradicting statement must be non-empty")
        new = self.propose(new_statement, source_ids, confidence, reason=reason, tags=tags)
        self._connection.execute(
            "UPDATE knowledge_records SET status = 'contradicted', updated_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), old_id),
        )
        self._connection.execute(
            "UPDATE knowledge_records SET supersedes_id = ? WHERE id = ?", (old_id, new.id)
        )
        self._connection.commit()
        return self.get(new.id)

    def superseded_by(self, record_id: int) -> KnowledgeRecord | None:
        row = self._connection.execute(
            "SELECT * FROM knowledge_records WHERE supersedes_id = ? ORDER BY id DESC LIMIT 1",
            (record_id,),
        ).fetchone()
        return self._to_record(row) if row is not None else None

    def explain(self, record_id: int) -> BeliefExplanation:
        """Current belief + supporting evidence + contradictory evidence + sources."""
        record = self.get(record_id)
        supporting = tuple(self._resolve_sources(record.source_ids))
        successor = self.superseded_by(record_id)
        against: tuple[SourceRef, ...] = ()
        if successor is not None:
            against = tuple(self._resolve_sources(successor.source_ids))
        return BeliefExplanation(
            record_id=record.id,
            statement=record.statement,
            status=record.status,
            confidence=record.confidence,
            supporting=supporting,
            contradicted_by=against,
            supersedes_id=record.supersedes_id,
            reason=record.reason,
        )

    def provenance(self, record_id: int) -> list[dict]:
        """Resolve a claim's full provenance from its source events.

        Returns one dict per source event carrying kind, url, source_type,
        backend, and retrieval time — preserved across persistence and
        updates because the underlying events are immutable.
        """
        record = self.get(record_id)
        out: list[dict] = []
        for source_id in record.source_ids:
            row = self._connection.execute(
                "SELECT kind, text, metadata_json FROM events WHERE id = ?", (source_id,)
            ).fetchone()
            if row is None:
                continue
            metadata = json.loads(row["metadata_json"] or "{}")
            out.append({
                "event_id": source_id,
                "kind": row["kind"],
                "url": metadata.get("url"),
                "source_type": metadata.get("source_type"),
                "backend": metadata.get("backend"),
                "retrieved_at": metadata.get("retrieved_at"),
            })
        return out

    def _resolve_sources(self, source_ids: tuple[int, ...]) -> list[SourceRef]:
        refs: list[SourceRef] = []
        for source_id in source_ids:
            row = self._connection.execute(
                "SELECT id, kind, text FROM events WHERE id = ?", (source_id,)
            ).fetchone()
            if row is not None:
                refs.append(SourceRef(source_id, row["kind"], row["text"]))
        return refs

    def get(self, record_id: int) -> KnowledgeRecord:
        row = self._connection.execute(
            "SELECT * FROM knowledge_records WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown knowledge record: {record_id}")
        return self._to_record(row)

    def active(self, *, limit: int = 100) -> list[KnowledgeRecord]:
        rows = self._connection.execute(
            "SELECT * FROM knowledge_records WHERE status = 'active' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def visible(self, *, limit: int = 100) -> list[KnowledgeRecord]:
        """Active, uncertain, contradicted, and outdated records. Superseded,
        rejected, and archived stay in history; inspect via get()/explain()."""
        rows = self._connection.execute(
            "SELECT * FROM knowledge_records WHERE status IN ('active', 'uncertain', 'contradicted', 'outdated')"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def history(self, *, limit: int = 500) -> list[KnowledgeRecord]:
        """Every record including superseded/rejected: the full revision trail.
        Used by derived views (graph); never presented as current belief."""
        rows = self._connection.execute(
            "SELECT * FROM knowledge_records ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def by_status(self, status: str, *, limit: int = 100) -> list[KnowledgeRecord]:
        if status not in {"active", "uncertain", "contradicted", "superseded",
                          "rejected", "outdated", "archived"}:
            raise ValueError(f"unknown knowledge status: {status}")
        rows = self._connection.execute(
            "SELECT * FROM knowledge_records WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def covered_source_ids(self) -> set[int]:
        covered: set[int] = set()
        rows = self._connection.execute("SELECT source_ids_json FROM knowledge_records").fetchall()
        for row in rows:
            covered.update(json.loads(row["source_ids_json"]))
        return covered

    def recall(self, query: str, *, limit: int = 5, embedder=None) -> list[KnowledgeRecord]:
        """Rank visible knowledge (active, uncertain, contradicted) by token
        overlap, with an optional semantic signal via `embedder` (same contract
        as EventLedger.recall).

        Superseded and rejected records stay in history but are excluded here;
        use get()/explain() to inspect them.
        """
        query_tokens = set(tokenize(query))
        records = self.visible(limit=500)
        if not query_tokens:
            return records[:limit]
        query_vector = None
        if embedder is not None:
            query_vector = embedder.embed(query)
        scored: list[tuple[float, int, KnowledgeRecord]] = []
        for record in records:
            statement_tokens = set(tokenize(record.statement))
            score = float(len(query_tokens & statement_tokens))
            # Light prefix matching for stop/stopped style variants.
            for q in query_tokens:
                for s in statement_tokens:
                    if q != s and len(q) >= 4 and (s.startswith(q) or q.startswith(s)):
                        score += 1.0
                        break
            if query_vector is not None:
                from .embeddings import cosine_similarity

                score += max(0.0, cosine_similarity(query_vector, embedder.embed(record.statement)))
            scored.append((score, record.id, record))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [record for _, _, record in scored[:limit]]

    def close(self) -> None:
        self._connection.close()

    def _require_sources_exist(self, source_ids: list[int]) -> None:
        for source_id in source_ids:
            row = self._connection.execute(
                "SELECT id FROM events WHERE id = ?", (source_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"source event does not exist: {source_id}")

    @staticmethod
    def _to_record(row: sqlite3.Row) -> KnowledgeRecord:
        columns = set(row.keys())
        created = datetime.fromisoformat(row["created_at"])
        updated_raw = row["updated_at"] if "updated_at" in columns else ""
        updated = datetime.fromisoformat(updated_raw) if updated_raw else created
        tags_raw = row["tags_json"] if "tags_json" in columns else "[]"
        return KnowledgeRecord(
            id=row["id"],
            statement=row["statement"],
            confidence=float(row["confidence"]),
            source_ids=tuple(json.loads(row["source_ids_json"])),
            status=row["status"],
            supersedes_id=row["supersedes_id"],
            created_at=created,
            updated_at=updated,
            reason=row["reason"] or "",
            tags=tuple(json.loads(tags_raw)),
        )
