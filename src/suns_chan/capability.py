"""Dynamic capability registry: Suns Chan's discoverable, acquirable abilities.

This is the "what can I do, and what can I gain the ability to do" layer.
Capabilities are NOT a static plugin list: they are runtime objects acquired
dynamically (tool, executable, package, script, library, agent, service, web
app, or other resource) with full operational metadata — provenance, version,
install location, invocation, validation, and usage/failure history.

The store lives in its own table in the SHARED database file (curiosity /
learned / understanding precedent — never a second memory system). The
lifecycle is an explicit state machine so a capability that repeatedly fails
can become `disabled` instead of wasting resources forever:

    discovered -> downloaded -> installed -> initialized -> validated -> available
                   \\-> failed                              \\-> disabled -> removed

Nothing here grants authorization: registering a capability never reaches
PolicyGate, tools, or infrastructure on its own. KNOWN ≠ TRUSTED ≠ ALLOWED.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from .comms import scrub_secrets
from .memory import tokenize


CAPABILITY_KINDS = (
    "tool", "executable", "package", "script", "library",
    "agent", "service", "webapp", "other",
)

CAPABILITY_STATUSES = (
    "discovered", "downloaded", "installed", "initialized",
    "validated", "available", "failed", "disabled", "removed",
)

_TRANSITIONS = {
    "discovered": {"downloaded", "failed", "removed"},
    "downloaded": {"installed", "failed", "removed"},
    "installed": {"initialized", "failed", "removed"},
    "initialized": {"validated", "failed", "removed"},
    "validated": {"available", "failed", "disabled", "removed"},
    "available": {"disabled", "failed", "removed"},
    "failed": {"discovered", "disabled", "removed"},
    "disabled": {"available", "removed"},
    "removed": set(),
}


@dataclass(frozen=True)
class CapabilityRecord:
    id: int
    name: str
    kind: str
    description: str
    version: str
    status: str
    source_type: str
    source_url: str
    origin: str
    install_method: str
    invoke: str
    install_location: str
    checksum: str
    dependencies: tuple[str, ...]
    capabilities: tuple[str, ...]
    acquired_at: datetime
    last_validated: datetime | None
    validation_note: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CapabilityUsage:
    id: int
    capability_id: int
    task: str
    result: str  # success | failure | unknown
    note: str
    occurred_at: datetime


@dataclass(frozen=True)
class CapabilityGap:
    requirement: str
    reason: str
    constraints: tuple[str, ...] = ()


class CapabilityStore:
    """Persistent capability registry + usage/failure history. Shared DB."""

    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS capabilities (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                version TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'discovered',
                source_type TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT '',
                install_method TEXT NOT NULL DEFAULT '',
                invoke TEXT NOT NULL DEFAULT '',
                install_location TEXT NOT NULL DEFAULT '',
                checksum TEXT NOT NULL DEFAULT '',
                dependencies_json TEXT NOT NULL DEFAULT '[]',
                capabilities_json TEXT NOT NULL DEFAULT '[]',
                acquired_at TEXT NOT NULL,
                last_validated TEXT,
                validation_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_capability_name_version "
            "ON capabilities(name, version)"
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS capability_usage (
                id INTEGER PRIMARY KEY,
                capability_id INTEGER NOT NULL,
                task TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT 'unknown',
                note TEXT NOT NULL DEFAULT '',
                occurred_at TEXT NOT NULL
            )"""
        )
        self._connection.commit()

    def discover(self, name: str, kind: str, description: str = "", *,
                 version: str = "", source_type: str = "", source_url: str = "",
                 origin: str = "", install_method: str = "", invoke: str = "",
                 checksum: str = "", dependencies: list[str] | None = None,
                 capabilities: list[str] | None = None) -> CapabilityRecord:
        """Record a newly discovered candidate. Deduplicates (name, version)."""
        if kind not in CAPABILITY_KINDS:
            raise ValueError(f"unknown capability kind: {kind}")
        if not name.strip():
            raise ValueError("capability name must be non-empty")
        clean_name = scrub_secrets(name.strip())[:200]
        now = datetime.now(UTC).isoformat()
        existing = self._connection.execute(
            "SELECT id FROM capabilities WHERE name = ? AND version = ?",
            (clean_name, version.strip())).fetchone()
        if existing is not None:
            return self.get(existing["id"])
        cursor = self._connection.execute(
            """INSERT INTO capabilities
               (name, kind, description, version, status, source_type, source_url,
                origin, install_method, invoke, install_location, checksum,
                dependencies_json, capabilities_json, acquired_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'discovered', ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)""",
            (clean_name, kind, description.strip(), version.strip(),
             source_type.strip(), source_url.strip(), origin.strip(),
             install_method.strip(), invoke.strip(), checksum.strip(),
             json.dumps(dependencies or []), json.dumps(capabilities or []),
             now, now, now),
        )
        self._connection.commit()
        return self.get(cursor.lastrowid)

    def transition(self, capability_id: int, to_status: str, *,
                   reason: str = "") -> CapabilityRecord:
        if to_status not in CAPABILITY_STATUSES:
            raise ValueError(f"unknown capability status: {to_status}")
        record = self.get(capability_id)
        if to_status not in _TRANSITIONS[record.status]:
            raise ValueError(f"cannot move capability {record.status} -> {to_status}")
        now = datetime.now(UTC).isoformat()
        self._connection.execute(
            "UPDATE capabilities SET status = ?, validation_note = ?, updated_at = ? WHERE id = ?",
            (to_status, reason.strip(), now, capability_id),
        )
        if to_status in {"validated", "available"}:
            self._connection.execute(
                "UPDATE capabilities SET last_validated = ? WHERE id = ?",
                (now, capability_id),
            )
        self._connection.commit()
        return self.get(capability_id)

    def set_invocation(self, capability_id: int, *, install_location: str = "",
                       invoke: str = "") -> CapabilityRecord:
        record = self.get(capability_id)
        self._connection.execute(
            "UPDATE capabilities SET install_location = ?, invoke = ?, updated_at = ? WHERE id = ?",
            (install_location.strip(), invoke.strip(), datetime.now(UTC).isoformat(), capability_id),
        )
        self._connection.commit()
        return self.get(capability_id)

    def record_usage(self, capability_id: int, result: str, *,
                     task: str = "", note: str = "") -> CapabilityUsage:
        self.get(capability_id)
        if result not in {"success", "failure", "unknown"}:
            raise ValueError("usage result must be success, failure, or unknown")
        now = datetime.now(UTC).isoformat()
        cursor = self._connection.execute(
            "INSERT INTO capability_usage (capability_id, task, result, note, occurred_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (capability_id, task.strip(), result, note.strip(), now),
        )
        self._connection.commit()
        return CapabilityUsage(cursor.lastrowid, capability_id, task.strip(),
                               result, note.strip(), datetime.fromisoformat(now))

    def get(self, capability_id: int) -> CapabilityRecord:
        row = self._connection.execute(
            "SELECT * FROM capabilities WHERE id = ?", (capability_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown capability: {capability_id}")
        return self._to_record(row)

    def by_name(self, name: str) -> list[CapabilityRecord]:
        rows = self._connection.execute(
            "SELECT * FROM capabilities WHERE name = ? ORDER BY id DESC", (name,)).fetchall()
        return [self._to_record(row) for row in rows]

    def available(self, *, limit: int = 200) -> list[CapabilityRecord]:
        rows = self._connection.execute(
            "SELECT * FROM capabilities WHERE status = 'available' ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [self._to_record(row) for row in rows]

    def all(self, *, limit: int = 500) -> list[CapabilityRecord]:
        rows = self._connection.execute(
            "SELECT * FROM capabilities ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._to_record(row) for row in rows]

    def by_status(self, status: str, *, limit: int = 200) -> list[CapabilityRecord]:
        if status not in CAPABILITY_STATUSES:
            raise ValueError(f"unknown capability status: {status}")
        rows = self._connection.execute(
            "SELECT * FROM capabilities WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit)).fetchall()
        return [self._to_record(row) for row in rows]

    def usage(self, capability_id: int, *, limit: int = 100) -> list[CapabilityUsage]:
        rows = self._connection.execute(
            "SELECT * FROM capability_usage WHERE capability_id = ? ORDER BY id DESC LIMIT ?",
            (capability_id, limit)).fetchall()
        return [CapabilityUsage(r["id"], r["capability_id"], r["task"], r["result"],
                                r["note"], datetime.fromisoformat(r["occurred_at"]))
                for r in rows]

    def reliability(self, capability_id: int) -> float | None:
        """Fraction of uses that succeeded; None when there is no history."""
        row = self._connection.execute(
            "SELECT result, COUNT(*) AS n FROM capability_usage WHERE capability_id = ? "
            "GROUP BY result", (capability_id,)).fetchall()
        counts = {r["result"]: r["n"] for r in row}
        total = counts.get("success", 0) + counts.get("failure", 0)
        if total == 0:
            return None
        return round(counts.get("success", 0) / total, 4)

    def match(self, requirement: str, *, limit: int = 10,
              statuses: tuple[str, ...] = ("available",)) -> list[tuple[CapabilityRecord, int]]:
        """Rank capabilities by token overlap with a requirement. Bounded and
        status-filtered so disabled/failed capabilities don't surface for use."""
        query_tokens = set(tokenize(requirement))
        scored: list[tuple[int, int, CapabilityRecord]] = []
        for record in self.all(limit=500):
            if record.status not in statuses:
                continue
            blob = " ".join([record.name, record.description, *record.capabilities])
            tokens = set(tokenize(blob))
            score = len(query_tokens & tokens)
            for q in query_tokens:
                for t in tokens:
                    if q != t and len(q) >= 4 and (t.startswith(q) or q.startswith(t)):
                        score += 1
                        break
            if score > 0:
                scored.append((score, record.id, record))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [(record, score) for score, _, record in scored[:limit]]

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _to_record(row: sqlite3.Row) -> CapabilityRecord:
        return CapabilityRecord(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            description=row["description"] or "",
            version=row["version"] or "",
            status=row["status"],
            source_type=row["source_type"] or "",
            source_url=row["source_url"] or "",
            origin=row["origin"] or "",
            install_method=row["install_method"] or "",
            invoke=row["invoke"] or "",
            install_location=row["install_location"] or "",
            checksum=row["checksum"] or "",
            dependencies=tuple(json.loads(row["dependencies_json"] or "[]")),
            capabilities=tuple(json.loads(row["capabilities_json"] or "[]")),
            acquired_at=datetime.fromisoformat(row["acquired_at"]),
            last_validated=datetime.fromisoformat(row["last_validated"]) if row["last_validated"] else None,
            validation_note=row["validation_note"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


def detect_capability_gap(requirement: str, store: CapabilityStore, *,
                          constraints: tuple[str, ...] = ()) -> CapabilityGap | None:
    """Ask whether existing AVAILABLE capabilities can satisfy a requirement.

    Returns None when a matching available capability exists, else a
    structured CapabilityGap with a machine-readable but cognitively usable
    reason. This is the planner's "can I already do this?" check — it never
    creates a second planning system.
    """
    if not requirement.strip():
        raise ValueError("requirement must be non-empty")
    matches = store.match(requirement)
    if matches:
        return None
    reason = (f"No available capability matches requirement "
              f"{requirement!r}; discovery may be needed.")
    return CapabilityGap(requirement=requirement, reason=reason,
                         constraints=tuple(constraints))


def recommend_capability(requirement: str, store: CapabilityStore
                         ) -> tuple[CapabilityRecord, int] | None:
    """Pick the best available capability for a requirement.

    Prefers higher reliability (accumulated success/failure evidence), so a
    capability that repeatedly failed is deprioritized without being
    hard-coded away. Returns None when nothing matches — the caller can then
    detect a gap and acquire.
    """
    matches = store.match(requirement)
    if not matches:
        return None

    def key(item: tuple[CapabilityRecord, int]) -> tuple[float, int]:
        record, score = item
        reliability = store.reliability(record.id)
        return (reliability if reliability is not None else 0.5, score)

    return max(matches, key=key)
