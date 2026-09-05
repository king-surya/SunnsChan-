"""Autonomy session persistence. Own table, same SQLite file as the ledger.

Sessions survive agent restarts, process crashes, and VM recreation: status,
current activity, hypotheses, pending work, and attempt history are all
reloaded verbatim. The Event Ledger remains the narrative record; this table
is the machine-readable bookmark that makes resume exact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3


SESSION_STATUSES = (
    "idle", "assessing", "thinking", "generating", "selecting", "preparing",
    "executing", "observing", "recording", "reflecting", "updating",
    "waiting", "paused", "completed", "failed", "aborted",
)

MID_ACTIVITY = ("preparing", "executing", "observing", "recording")


@dataclass
class AutonomySession:
    session_id: str
    status: str = "idle"
    current_activity: dict = field(default_factory=dict)
    hypothesis: str = ""
    attempt_signatures: list = field(default_factory=list)
    avoid_keys: list = field(default_factory=list)
    recent_keys: list = field(default_factory=list)
    last_selection: dict = field(default_factory=dict)
    activity_counter: int = 0
    pending: list = field(default_factory=list)


class SessionStore:
    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS autonomy_sessions (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'idle',
                state_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._connection.commit()

    def open_or_create(self, session_id: str) -> AutonomySession:
        if not session_id.strip():
            raise ValueError("session_id must be non-empty")
        row = self._connection.execute(
            "SELECT * FROM autonomy_sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            session = AutonomySession(session_id=session_id)
            self.save(session)
            return session
        return self._to_session(row)

    def save(self, session: AutonomySession) -> None:
        if session.status not in SESSION_STATUSES:
            raise ValueError(f"unknown session status: {session.status}")
        from datetime import UTC, datetime

        state = {
            "current_activity": session.current_activity,
            "hypothesis": session.hypothesis,
            "attempt_signatures": session.attempt_signatures,
            "avoid_keys": session.avoid_keys,
            "recent_keys": session.recent_keys,
            "last_selection": session.last_selection,
            "activity_counter": session.activity_counter,
            "pending": session.pending,
        }
        self._connection.execute(
            "INSERT INTO autonomy_sessions (session_id, status, state_json, updated_at)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET"
            " status = excluded.status, state_json = excluded.state_json,"
            " updated_at = excluded.updated_at",
            (session.session_id, session.status, json.dumps(state),
             datetime.now(UTC).isoformat()),
        )
        self._connection.commit()

    def list_open(self) -> list[AutonomySession]:
        rows = self._connection.execute(
            "SELECT * FROM autonomy_sessions WHERE status NOT IN ('completed', 'failed', 'aborted')"
            " ORDER BY updated_at DESC").fetchall()
        return [self._to_session(row) for row in rows]

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _to_session(row: sqlite3.Row) -> AutonomySession:
        state = json.loads(row["state_json"] or "{}")
        return AutonomySession(
            session_id=row["session_id"],
            status=row["status"],
            current_activity=state.get("current_activity", {}),
            hypothesis=state.get("hypothesis", ""),
            attempt_signatures=state.get("attempt_signatures", []),
            avoid_keys=state.get("avoid_keys", []),
            recent_keys=state.get("recent_keys", []),
            last_selection=state.get("last_selection", {}),
            activity_counter=state.get("activity_counter", 0),
            pending=state.get("pending", []),
        )
