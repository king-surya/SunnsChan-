"""Main-server observability: read-only telemetry into existing cognition.

The main server is an OBSERVED environment, never a writable one. This module
turns raw log/event streams into a small stream of normalized, deduplicated,
relevance-filtered observations that feed the EXISTING experience and
understanding systems — it never creates a second memory, bus, or brain.

Pipeline: TelemetrySource -> normalize -> redact -> dedupe (fingerprint) ->
importance filter -> ledger `telemetry` events -> experience / understanding.

Observation and action are separate capabilities: nothing here executes
commands, modifies files, restarts services, or touches credentials. Read-only
by construction — sources only read, and sensitive fields are scrubbed before
anything persists.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3
from typing import Any, Protocol

from .comms import scrub_secrets
from .memory import EventLedger, tokenize


_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(token|secret|password|passwd|api[_-]?key|auth|private[_-]?key|credential)"
)


def _redact_value(key: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if _SENSITIVE_KEY_RE.search(key):
        return "[REDACTED]"
    return scrub_secrets(value)


IMPORTANCE_LEVELS = ("low", "normal", "important", "critical")
IMPORTANCE_RANK = {"low": 0, "normal": 1, "important": 2, "critical": 3}

# Deterministic default: which (category.event_type) deserves cognition.
_IMPORTANCE_BY_TYPE = {
    "service.failed": "critical",
    "service.crashed": "critical",
    "authentication.failed": "important",
    "authentication.login": "important",
    "authentication.logout": "normal",
    "configuration.changed": "important",
    "service.restarted": "important",
    "service.stopped": "normal",
    "service.started": "normal",
    "application.error": "important",
    "application.started": "normal",
    "application.stopped": "normal",
    "deployment.completed": "important",
    "deployment.started": "normal",
    "system.resource": "normal",
}


@dataclass(frozen=True)
class RawTelemetry:
    offset: int
    timestamp: datetime
    source: str
    category: str  # authentication | service | application | configuration | system | deployment
    event_type: str  # login | logout | started | stopped | failed | changed | ...
    message: str
    severity: str = "info"
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedTelemetry:
    event_id: str
    offset: int
    timestamp: datetime
    source: str
    category: str
    event_type: str
    severity: str
    subject: str
    action: str
    message: str
    importance: str
    metadata: dict
    provenance: dict


class TelemetrySource(Protocol):
    name: str

    def poll(self) -> list[RawTelemetry]:
        """Return new events since the last poll. May raise RuntimeError when
        the server is unavailable (callers degrade gracefully)."""
        ...

    def describe(self) -> dict:
        """Connection/health diagnostics for observability."""
        ...


@dataclass
class ScriptedTelemetrySource:
    """Deterministic event batches for tests/dev. Each poll() advances one step.
    Supports simulated disconnection (poll raises) and reconnection."""

    name: str = "main-server"
    script: list[list[RawTelemetry]] = field(default_factory=list)
    _step: int = field(default=0, init=False)
    _offset: int = field(default=0, init=False)
    _disconnected: bool = field(default=False, init=False)

    def disconnect(self) -> None:
        self._disconnected = True

    def reconnect(self) -> None:
        self._disconnected = False

    def poll(self) -> list[RawTelemetry]:
        if self._disconnected:
            raise RuntimeError(f"telemetry source {self.name} unavailable")
        if not self.script or self._step >= len(self.script):
            return []
        batch = self.script[self._step]
        self._step += 1
        out: list[RawTelemetry] = []
        for raw in batch:
            self._offset += 1
            out.append(RawTelemetry(
                offset=self._offset, timestamp=raw.timestamp, source=raw.source or self.name,
                category=raw.category, event_type=raw.event_type, message=raw.message,
                severity=raw.severity, metadata=dict(raw.metadata)))
        return out

    def describe(self) -> dict:
        return {"name": self.name, "connected": not self._disconnected,
                "step": self._step, "backend": "MOCK"}


@dataclass
class FileTelemetrySource:
    """REAL read-only tail of a log file by byte offset.

    Each new line is one event: JSON-lines are parsed into category/event_type/
    message/severity/metadata; plain lines become `system`/`message` events.
    Read-only: this source never writes the file. Credentials stay out of the
    returned events; redaction happens in the collector.
    """

    name: str = "main-server"
    path: str | Path = ""
    _offset: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def poll(self) -> list[RawTelemetry]:
        if not str(self.path) or not self.path.exists():
            raise RuntimeError(f"telemetry source {self.name} unavailable: {self.path}")
        events: list[RawTelemetry] = []
        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                raw = handle.read()
        except OSError as exc:
            raise RuntimeError(f"telemetry source {self.name} read failed: {exc}") from exc
        if not raw:
            return []
        self._offset += len(raw)
        for line in raw.decode("utf-8", errors="replace").split("\n"):
            line = line.rstrip("\r")
            if not line.strip():
                continue
            events.append(self._parse_line(line, self._offset))
        return events

    def _parse_line(self, line: str, offset: int) -> RawTelemetry:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return RawTelemetry(offset, datetime.now(UTC), self.name, "system",
                                "message", line, "info")
        ts = data.get("timestamp") or datetime.now(UTC).isoformat()
        try:
            timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            timestamp = datetime.now(UTC)
        return RawTelemetry(
            offset=offset, timestamp=timestamp,
            source=str(data.get("source", self.name)),
            category=str(data.get("category", "system")),
            event_type=str(data.get("event_type", "message")),
            message=str(data.get("message", "") or data.get("text", "") or line),
            severity=str(data.get("severity", "info")),
            metadata=dict(data.get("metadata", {}) or {}),
        )

    def describe(self) -> dict:
        return {"name": self.name, "path": str(self.path),
                "offset": self._offset, "backend": "REAL"}


def classify_importance(category: str, event_type: str, severity: str = "info") -> str:
    """Map a normalized event to an importance tier (deterministic default)."""
    sev = severity.strip().lower()
    if sev in ("critical", "emergency", "alert", "fatal"):
        return "critical"
    key = f"{category}.{event_type}".lower()
    if key in _IMPORTANCE_BY_TYPE:
        return _IMPORTANCE_BY_TYPE[key]
    if sev in ("warning", "error"):
        return "important"
    return "low"


def _fingerprint(source: str, timestamp: datetime, category: str, event_type: str,
                 subject: str, action: str, message: str) -> str:
    blob = f"{source}|{timestamp.isoformat()}|{category}|{event_type}|{subject}|{action}|{message}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def normalize(raw: RawTelemetry, *, redact: bool = True) -> NormalizedTelemetry:
    message = scrub_secrets(raw.message) if redact else raw.message
    metadata = {k: _redact_value(k, v) for k, v in (raw.metadata or {}).items()}
    subject = str(metadata.get("subject") or raw.category)
    action = str(metadata.get("action") or raw.event_type)
    importance = classify_importance(raw.category, raw.event_type, raw.severity)
    event_id = _fingerprint(raw.source, raw.timestamp, raw.category, raw.event_type,
                            subject, action, message)
    provenance = {"source": raw.source, "offset": raw.offset,
                  "category": raw.category, "event_type": raw.event_type}
    return NormalizedTelemetry(
        event_id=event_id, offset=raw.offset, timestamp=raw.timestamp,
        source=raw.source, category=raw.category, event_type=raw.event_type,
        severity=raw.severity, subject=subject, action=action, message=message,
        importance=importance, metadata=metadata, provenance=provenance)


class TelemetryState:
    """Persisted cursor + dedup fingerprints. Shared DB (established pattern)."""

    def __init__(self, database: str | Path, *, retention: int = 1000) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS telemetry_cursor (
                source TEXT PRIMARY KEY, offset INTEGER NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS telemetry_fingerprints (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, fingerprint TEXT NOT NULL,
                seen_at TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_fp ON telemetry_fingerprints(source, fingerprint)"
        )
        self._connection.commit()
        self._retention = retention

    def get_cursor(self, source: str) -> int:
        row = self._connection.execute(
            "SELECT offset FROM telemetry_cursor WHERE source = ?", (source,)).fetchone()
        return int(row["offset"]) if row else 0

    def set_cursor(self, source: str, offset: int) -> None:
        now = datetime.now(UTC).isoformat()
        self._connection.execute(
            """INSERT INTO telemetry_cursor (source, offset, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET offset = excluded.offset, updated_at = excluded.updated_at""",
            (source, offset, now))
        self._connection.commit()

    def seen(self, source: str, fingerprint: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM telemetry_fingerprints WHERE source = ? AND fingerprint = ?",
            (source, fingerprint)).fetchone()
        return row is not None

    def mark_seen(self, source: str, fingerprint: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._connection.execute(
            "INSERT INTO telemetry_fingerprints (source, fingerprint, seen_at) VALUES (?, ?, ?)",
            (source, fingerprint, now))
        self._connection.execute(
            "DELETE FROM telemetry_fingerprints WHERE id NOT IN "
            "(SELECT id FROM telemetry_fingerprints ORDER BY id DESC LIMIT ?)",
            (self._retention,))
        self._connection.commit()

    def count_fingerprints(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS n FROM telemetry_fingerprints").fetchone()
        return int(row["n"])

    def close(self) -> None:
        self._connection.close()


@dataclass(frozen=True)
class TelemetryCollectionReport:
    source: str
    status: str  # ok | disconnected
    new_events: int
    significant: int
    deduplicated: int
    low_skipped: int
    last_offset: int
    error: str = ""


class TelemetryCollector:
    """Poll -> normalize -> redact -> dedupe -> filter -> ledger `telemetry` events."""

    def __init__(self, source: TelemetrySource, state: TelemetryState, *,
                 importance_threshold: str = "normal", redact: bool = True) -> None:
        if importance_threshold not in IMPORTANCE_LEVELS:
            raise ValueError(f"unknown importance threshold: {importance_threshold}")
        self._source = source
        self._state = state
        self._threshold = IMPORTANCE_RANK[importance_threshold]
        self._redact = redact

    @property
    def source(self) -> TelemetrySource:
        return self._source

    def collect(self, ledger: EventLedger) -> TelemetryCollectionReport:
        try:
            raw_events = self._source.poll()
        except (RuntimeError, ValueError) as exc:
            return TelemetryCollectionReport(self._source.name, "disconnected",
                                             0, 0, 0, 0, self._state.get_cursor(self._source.name),
                                             error=str(exc)[:200])
        cursor = self._state.get_cursor(self._source.name)
        new_events = 0
        significant = 0
        deduplicated = 0
        low_skipped = 0
        for raw in raw_events:
            if raw.offset <= cursor:
                continue
            new_events += 1
            normalized = normalize(raw, redact=self._redact)
            if self._state.seen(normalized.source, normalized.event_id):
                deduplicated += 1
                cursor = raw.offset
                continue
            self._state.mark_seen(normalized.source, normalized.event_id)
            if IMPORTANCE_RANK[normalized.importance] < self._threshold:
                low_skipped += 1
                cursor = raw.offset
                continue
            self._record(ledger, normalized)
            significant += 1
            cursor = raw.offset
        self._state.set_cursor(self._source.name, cursor)
        return TelemetryCollectionReport(
            self._source.name, "ok", new_events, significant, deduplicated,
            low_skipped, cursor)

    def _record(self, ledger: EventLedger, event: NormalizedTelemetry) -> None:
        text = (f"[{event.importance}] {event.source} {event.category}.{event.event_type}: "
                f"{event.subject} {event.action} — {event.message}")[:400]
        ledger.record("telemetry", text, {
            "source": event.source,
            "category": event.category,
            "event_type": event.event_type,
            "severity": event.severity,
            "importance": event.importance,
            "subject": event.subject,
            "action": event.action,
            "event_id": event.event_id,
            "offset": event.offset,
            "observed_at": event.timestamp.isoformat(),
        })


def record_telemetry_experience(ledger: EventLedger, events: list, *,
                                session_id: str = "telemetry") -> int | None:
    """Group one batch of significant telemetry ledger events into ONE
    experience (bounded: an experience per raw event would flood the ledger).
    Reuses record_experience. Marks the covered telemetry ids so promotion is
    idempotent.
    """
    if not events:
        return None
    from .experience import record_experience

    actions = [f"{e.metadata.get('category', '?')}.{e.metadata.get('event_type', '?')}: "
               f"{e.metadata.get('subject', '?')}" for e in events[:10]]
    observations = [e.text[:200] for e in events[:10]]
    covered = max(e.id for e in events)
    event = record_experience(
        ledger, session_id=session_id,
        activity_id=f"telemetry-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        intent="Observed main-server activity",
        hypothesis="",
        actions=actions,
        observations=observations,
        result="unknown",
        lessons=[],
    )
    ledger.record("telemetry_promoted", f"Promoted telemetry through event #{covered}.",
                  {"covered_up_to": covered, "experience_id": event.id})
    return event.id


def unpromoted_telemetry(ledger: EventLedger, *, min_importance: str = "important",
                         limit: int = 100) -> list:
    """Significant telemetry events not yet promoted to an experience."""
    threshold = IMPORTANCE_RANK.get(min_importance, IMPORTANCE_RANK["important"])
    covered = 0
    for marker in ledger.events_of_kind("telemetry_promoted", limit=1):
        covered = int(marker.metadata.get("covered_up_to", 0) or 0)
        break
    out: list = []
    for event in ledger.events_of_kind("telemetry", limit=limit):
        if event.id <= covered:
            break
        if IMPORTANCE_RANK.get(str(event.metadata.get("importance", "low")), 0) >= threshold:
            out.append(event)
    return out


def promote_telemetry(ledger: EventLedger, understanding=None, *,
                      min_importance: str = "important",
                      session_id: str = "telemetry") -> tuple[int, list]:
    """Experience + understanding promotion for significant, unprocessed events.

    Returns (experience_id_or_0, touched_understanding). Idempotent across
    calls: only telemetry newer than the last promotion is processed.
    """
    events = unpromoted_telemetry(ledger, min_importance=min_importance)
    experience_id = record_telemetry_experience(ledger, events, session_id=session_id)
    touched: list = []
    if understanding is not None:
        touched = mine_telemetry_understanding(ledger, understanding)
    return (experience_id or 0, touched)


def mine_telemetry_understanding(ledger: EventLedger, understanding, *,
                                 limit: int = 20, min_importance: str = "important") -> list:
    """Turn significant telemetry into OBSERVATION-kind understanding (never
    fact). Deduplicates by statement signature and affirms on repeat.

    OBSERVATION ≠ FACT: these are things Suns Chan observed happening, not
    things Surya stated, so they enter as `observation` at low confidence.
    """
    threshold = IMPORTANCE_RANK.get(min_importance, IMPORTANCE_RANK["important"])
    events = ledger.events_of_kind("telemetry", limit=limit)
    existing = {_statement_signature(r.statement): r for r in understanding.visible(limit=500)}
    touched: list = []
    for event in reversed(events):
        if IMPORTANCE_RANK.get(str(event.metadata.get("importance", "low")), 0) < threshold:
            continue
        statement = _telemetry_statement(event)
        signature = _statement_signature(statement)
        record = existing.get(signature)
        if record is not None:
            if event.id not in record.source_ids:
                touched.append(understanding.affirm(record.id, event.id))
            continue
        created = understanding.note("observation", statement, [event.id],
                                     confidence=0.45, reason="observed via telemetry",
                                     tags=["telemetry", "server"])
        existing[signature] = created
        touched.append(created)
    return touched


def _telemetry_statement(event) -> str:
    source = str(event.metadata.get("source", "main-server"))
    category = str(event.metadata.get("category", "system"))
    event_type = str(event.metadata.get("event_type", "event"))
    subject = str(event.metadata.get("subject", category))
    return f"Main server: {subject} {event_type} ({source})"


def _statement_signature(statement: str) -> frozenset[str]:
    return frozenset(tokenize(scrub_secrets(statement)))


def summarize_recent_telemetry(ledger: EventLedger, *, limit: int = 10) -> list[str]:
    """Bounded, human-readable recent significant telemetry (for context/CLI).
    Never dumps raw payloads."""
    lines: list[str] = []
    for event in ledger.events_of_kind("telemetry", limit=limit):
        meta = event.metadata
        lines.append(
            f"[{meta.get('importance', 'low')}] {meta.get('source', '?')} "
            f"{meta.get('category', '?')}.{meta.get('event_type', '?')}: "
            f"{meta.get('subject', '?')} — {event.text[event.text.find('—') + 1:].strip()[:120]}")
    return lines
