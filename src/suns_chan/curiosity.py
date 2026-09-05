"""First-class curiosity records with an explicit lifecycle.

Curiosities live in the same SQLite file as the ledger but in their own
table (no Phase 0 schema touched). Every transition requires evidence: an
origin or supporting event id. Status flow:

NEW -> ACTIVE -> INVESTIGATING -> ANSWERED
  \\-> ABANDONED (reopenable -> REOPENED -> ACTIVE)

Curiosity never grants capability and never edits personality traits; it
only proposes future activities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from .memory import require_event


STATUSES = ("new", "active", "investigating", "answered", "abandoned", "reopened")

_TRANSITIONS = {
    "new": {"active", "abandoned"},
    "active": {"investigating", "answered", "abandoned"},
    "investigating": {"answered", "active", "abandoned"},
    "answered": {"reopened"},
    "abandoned": {"reopened"},
    "reopened": {"active", "investigating", "answered", "abandoned"},
}


@dataclass(frozen=True)
class Curiosity:
    id: int
    question: str
    origin_event_id: int
    topic: str
    importance: float
    novelty: float
    confidence: float
    status: str
    created_at: datetime
    updated_at: datetime


def _clamp(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("curiosity scores must be numbers between 0 and 1")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("curiosity scores must be between 0 and 1")
    return round(number, 4)


class CuriosityStore:
    """Evidence-linked curiosity lifecycle. Shares the ledger's SQLite file."""

    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS curiosities (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                question TEXT NOT NULL,
                origin_event_id INTEGER NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                importance REAL NOT NULL DEFAULT 0.5,
                novelty REAL NOT NULL DEFAULT 0.5,
                confidence REAL NOT NULL DEFAULT 0.4,
                status TEXT NOT NULL DEFAULT 'new'
            )"""
        )
        self._connection.commit()

    def open(self, question: str, origin_event_id: int, *, topic: str = "",
             importance: float = 0.5, novelty: float = 0.5) -> Curiosity:
        if not question.strip():
            raise ValueError("curiosity question must be non-empty")
        self._require_event(origin_event_id)
        now = datetime.now(UTC).isoformat()
        cursor = self._connection.execute(
            """INSERT INTO curiosities
               (created_at, updated_at, question, origin_event_id, topic,
                importance, novelty, confidence, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0.4, 'new')""",
            (now, now, question.strip(), origin_event_id, topic.strip().lower(),
             _clamp(importance), _clamp(novelty)),
        )
        self._connection.commit()
        return self.get(cursor.lastrowid)

    def transition(self, curiosity_id: int, to_status: str, *, evidence_event_id: int) -> Curiosity:
        if to_status not in STATUSES:
            raise ValueError(f"unknown curiosity status: {to_status}")
        record = self.get(curiosity_id)
        if to_status not in _TRANSITIONS[record.status]:
            raise ValueError(f"cannot move curiosity {record.status} -> {to_status}")
        self._require_event(evidence_event_id)
        confidence = record.confidence
        if to_status in {"active", "investigating"}:
            confidence = _clamp(min(0.9, confidence + 0.1))
        elif to_status == "answered":
            confidence = _clamp(min(0.95, confidence + 0.15))
        self._connection.execute(
            "UPDATE curiosities SET status = ?, confidence = ?, updated_at = ? WHERE id = ?",
            (to_status, confidence, datetime.now(UTC).isoformat(), curiosity_id),
        )
        self._connection.commit()
        return self.get(curiosity_id)

    def get(self, curiosity_id: int) -> Curiosity:
        row = self._connection.execute(
            "SELECT * FROM curiosities WHERE id = ?", (curiosity_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown curiosity: {curiosity_id}")
        return self._to_record(row)

    def open_curiosities(self, *, limit: int = 100) -> list[Curiosity]:
        rows = self._connection.execute(
            "SELECT * FROM curiosities WHERE status IN ('new', 'active', 'reopened', 'investigating')"
            " ORDER BY importance DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [self._to_record(row) for row in rows]

    def all(self, *, limit: int = 200) -> list[Curiosity]:
        rows = self._connection.execute(
            "SELECT * FROM curiosities ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._to_record(row) for row in rows]

    def close(self) -> None:
        self._connection.close()

    def _require_event(self, event_id: int) -> None:
        require_event(self._connection, event_id)

    @staticmethod
    def _to_record(row: sqlite3.Row) -> Curiosity:
        return Curiosity(
            id=row["id"],
            question=row["question"],
            origin_event_id=row["origin_event_id"],
            topic=row["topic"] or "",
            importance=float(row["importance"]),
            novelty=float(row["novelty"]),
            confidence=float(row["confidence"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
