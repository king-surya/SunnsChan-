"""Local, append-only experience storage and deliberately simple recall."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "was",
        "were",
        "what",
        "why",
        "how",
        "did",
        "does",
        "are",
        "you",
        "your",
        "have",
        "has",
        "had",
        "this",
        "that",
        "from",
        "about",
        "into",
        "when",
        "where",
        "which",
        "while",
        "after",
        "before",
    }
)


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, dropping stopwords and tiny tokens."""
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


def _token_match(query_token: str, event_token: str) -> bool:
    if query_token == event_token:
        return True
    # Light stemming: "stop" matches "stopped", "driving" matches "drive".
    if len(query_token) >= 4 and event_token.startswith(query_token):
        return True
    if len(event_token) >= 4 and query_token.startswith(event_token):
        return True
    return False


def require_event(connection, event_id: int) -> None:
    """Raise if an event id does not exist in the given SQLite connection.

    Shared by the derived stores (curiosity/learned/understanding) so source
    validation stays consistent; the message is a contract, not user-facing.
    """
    row = connection.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise ValueError(f"source event does not exist: {event_id}")


def _signal_value(value: Any, *, default: float = 0.5) -> float:
    """Lenient 0..1 metadata signal. Retrieval never crashes on odd metadata."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if not 0.0 <= number <= 1.0:
        return default
    return number


def _metadata_matches(row: sqlite3.Row, metadata_filter: dict[str, Any]) -> bool:
    metadata = json.loads(row["metadata_json"])
    return all(metadata.get(key) == value for key, value in metadata_filter.items())


@dataclass(frozen=True)
class MemoryEvent:
    id: int
    occurred_at: datetime
    kind: str
    text: str
    metadata: dict[str, Any]


class EventLedger:
    """An inspectable event log. Existing records are never silently edited."""

    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS state_snapshots (
                id INTEGER PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                cause_event_id INTEGER,
                state_json TEXT NOT NULL
            )"""
        )
        self._connection.commit()

    def record(self, kind: str, text: str, metadata: dict[str, Any] | None = None) -> MemoryEvent:
        if not kind.strip() or not text.strip():
            raise ValueError("kind and text must be non-empty")
        now = datetime.now(UTC)
        values = (now.isoformat(), kind, text, json.dumps(metadata or {}, sort_keys=True))
        cursor = self._connection.execute(
            "INSERT INTO events (occurred_at, kind, text, metadata_json) VALUES (?, ?, ?, ?)", values
        )
        self._connection.commit()
        return MemoryEvent(cursor.lastrowid, now, kind, text, metadata or {})

    def recall(
        self,
        query: str,
        *,
        limit: int = 8,
        kinds: tuple[str, ...] | None = None,
        metadata_filter: dict[str, Any] | None = None,
        embedder: Any | None = None,
        weights: dict[str, float] | None = None,
    ) -> list[MemoryEvent]:
        """Hybrid retrieval over the event ledger (TF-IDF keyword + recency +
        importance/confidence metadata + optional semantic similarity).

        Backward compatible: recall(query) behaves as before — keyword
        relevance first, newest wins ties. Extra signals only refine order;
        nothing is ever deleted or rewritten. Pass an EmbeddingProvider as
        `embedder` to add the semantic signal; without one, keyword/metadata
        retrieval keeps working. `kinds` restricts event kinds;
        `metadata_filter` requires exact metadata key/value matches.
        """
        return [event for event, _ in self.recall_with_scores(
            query, limit=limit, kinds=kinds, metadata_filter=metadata_filter,
            embedder=embedder, weights=weights,
        )]

    def recall_with_scores(
        self,
        query: str,
        *,
        limit: int = 8,
        kinds: tuple[str, ...] | None = None,
        metadata_filter: dict[str, Any] | None = None,
        embedder: Any | None = None,
        weights: dict[str, float] | None = None,
    ) -> list[tuple[MemoryEvent, Any]]:
        """Same as recall() but also returns each hit's ranking explanation."""
        from .ranking import ScoredCandidate, rank, recency_decay

        query_tokens = tokenize(query)
        rows = self._connection.execute("SELECT * FROM events ORDER BY id DESC").fetchall()
        if kinds is not None:
            rows = [row for row in rows if row["kind"] in kinds]
        if metadata_filter:
            rows = [row for row in rows if _metadata_matches(row, metadata_filter)]
        if not rows:
            return []
        if not query_tokens and embedder is None:
            return [(self._to_event(row), None) for row in rows[:limit]]

        # Document frequency per query token across the filtered events.
        event_token_sets: list[set[str]] = []
        for row in rows:
            event_token_sets.append(set(tokenize(f"{row['kind']} {row['text']}")))
        total = len(rows)
        idf: dict[str, float] = {}
        for token in set(query_tokens):
            df = sum(
                1 for tokens in event_token_sets
                if any(_token_match(token, t) for t in tokens)
            )
            idf[token] = math.log((total + 1) / (df + 1)) + 1.0

        raw_keyword: list[float] = []
        for row, tokens in zip(rows, event_token_sets):
            score = 0.0
            for token in query_tokens:
                if any(_token_match(token, t) for t in tokens):
                    score += idf[token]
            # Small kind-name bonus so "goal ..." prefers goal events on ties.
            if any(token in row["kind"].lower() for token in query_tokens):
                score += 0.25
            raw_keyword.append(score)
        peak = max(raw_keyword) if raw_keyword else 0.0

        query_vector: list[float] | None = None
        if embedder is not None and query_tokens:
            query_vector = embedder.embed(query)

        candidates: list[ScoredCandidate] = []
        for index, (row, kw) in enumerate(zip(rows, raw_keyword)):
            metadata = json.loads(row["metadata_json"])
            signals = {
                "keyword": (kw / peak) if peak > 0 else 0.0,
                "recency": recency_decay(index),
                "importance": _signal_value(metadata.get("importance"), default=0.5),
                "confidence": _signal_value(metadata.get("confidence"), default=0.5),
            }
            if query_vector is not None and embedder is not None:
                from .embeddings import cosine_similarity

                event_vector = embedder.embed(f"{row['kind']} {row['text']}")
                signals["semantic"] = max(0.0, cosine_similarity(query_vector, event_vector))
            candidates.append(ScoredCandidate(row["id"], signals, tiebreak=float(row["id"])))
        ranked = {item.key: item for item in rank(candidates, weights=weights, limit=limit)}
        by_id = {row["id"]: row for row in rows}
        ordered = sorted(ranked.values(), key=lambda item: (item.score, item.tiebreak), reverse=True)
        return [(self._to_event(by_id[item.key]), item) for item in ordered]

    def events_of_kind(self, kind: str, *, limit: int = 100) -> list[MemoryEvent]:
        rows = self._connection.execute(
            "SELECT * FROM events WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, limit)
        ).fetchall()
        return [self._to_event(row) for row in rows]

    def save_state(self, state: dict[str, Any], *, cause_event_id: int | None = None) -> None:
        now = datetime.now(UTC)
        self._connection.execute(
            "INSERT INTO state_snapshots (occurred_at, cause_event_id, state_json) VALUES (?, ?, ?)",
            (now.isoformat(), cause_event_id, json.dumps(state, sort_keys=True)),
        )
        self._connection.commit()

    def load_state(self) -> dict[str, Any] | None:
        row = self._connection.execute("SELECT state_json FROM state_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row["state_json"]) if row else None

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _to_event(row: sqlite3.Row) -> MemoryEvent:
        return MemoryEvent(
            id=row["id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            kind=row["kind"],
            text=row["text"],
            metadata=json.loads(row["metadata_json"]),
        )
