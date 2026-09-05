"""Public information discovery with careful identity handling.

Suns Chan may use legitimately public information to understand Surya — public
repositories, profiles, portfolios, competition results. The purpose is
context, NOT surveillance, credential collection, or doxxing.

The hard rule is that a name match is NEVER certainty. `evaluate_identity`
scores only contextual evidence (username, project, technical context, stated
affiliation, cross-reference). Weak evidence stays weak: `discover_public`
records weak matches as `inference`/`uncertainty` understanding at low
confidence, and strong matches as `identity`/`fact` still below full certainty
(public information is not Surya's own confirmation). Provenance (source URL,
retrieval time, backend) is preserved on the underlying ledger event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from .memory import EventLedger, tokenize


@dataclass(frozen=True)
class IdentityEvaluation:
    confidence: float
    matched: bool
    reasons: tuple[str, ...]
    kind: str  # identity | fact | inference | uncertainty


class PublicSource(Protocol):
    kind: str

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        """Return public candidates. Each dict may carry username, name,
        project, title, description, url, affiliation."""
        ...


@dataclass
class MockPublicSource:
    """Deterministic public-info source for tests. Honest MOCK label."""

    kind: str = "mock-public"
    backend: str = "MOCK"
    records: dict[str, list[dict]] = field(default_factory=dict)

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        return [dict(r) for r in self.records.get(query, [])][:limit]


def _project_matches(project: str, blob: str) -> bool:
    """A project name matches when its normalized token signature overlaps
    the candidate text (so 'suns-chan' matches 'Suns Chan autonomous agent')."""
    normalized = project.strip().lower().replace("-", " ").replace("_", " ")
    if normalized and normalized in blob.lower():
        return True
    project_tokens = set(tokenize(project))
    blob_tokens = set(tokenize(blob))
    return bool(project_tokens) and len(project_tokens & blob_tokens) >= 2


def evaluate_identity(
    candidate: dict,
    *,
    known_usernames: tuple[str, ...] = (),
    known_projects: tuple[str, ...] = (),
    known_terms: tuple[str, ...] = (),
) -> IdentityEvaluation:
    """Score how likely a public record is about Surya, from context alone.

    A bare name match contributes nothing by itself — only overlapping
    identifiers, projects, or technical context raise confidence. The result
    is deliberately capped well below certainty: public evidence is never
    promoted into fact on its own. Even a username match alone stays
    `inference`; certainty-class (`identity`) needs corroborating context.
    """
    score = 0.0
    reasons: list[str] = []
    matched = False

    username = str(candidate.get("username", "") or "").strip()
    if username and username.lower() in {u.strip().lower() for u in known_usernames if u.strip()}:
        score += 0.45
        matched = True
        reasons.append(f"username '{username}' matches a known username")

    title = str(candidate.get("title", "") or "").strip()
    description = str(candidate.get("description", "") or "").strip()
    project = str(candidate.get("project", "") or "").strip()
    blob = f"{title} {description} {project}"
    blob_tokens = set(tokenize(blob))
    project_hits = [p for p in known_projects if _project_matches(p, blob)]
    if project_hits:
        score += 0.35
        matched = True
        reasons.append(f"overlaps known project(s): {', '.join(project_hits)}")
    term_hits = [t for t in known_terms
                 if t.strip().lower() in blob_tokens or t.strip().lower() in blob.lower()]
    if term_hits:
        score += min(0.2, 0.05 * len(term_hits))
        matched = True
        reasons.append(f"shares technical context: {', '.join(term_hits)}")

    affiliation = str(candidate.get("affiliation", "") or "").strip()
    if affiliation:
        score += 0.1
        reasons.append(f"stated affiliation: {affiliation}")

    if not matched:
        score = 0.0
        reasons.append("no contextual match beyond a possible name")

    score = round(max(0.0, min(0.85, score)), 4)
    if score >= 0.55:
        kind = "identity"
    elif score >= 0.3:
        kind = "inference"
    else:
        kind = "uncertainty"
    return IdentityEvaluation(score, matched, tuple(reasons), kind)


@dataclass(frozen=True)
class DiscoveryResult:
    query: str
    candidates: int
    recorded: int
    weak_skipped: int
    notes: tuple[str, ...] = ()


def discover_public(
    query: str,
    source: PublicSource,
    ledger: EventLedger,
    understanding,
    *,
    known_usernames: tuple[str, ...] = (),
    known_projects: tuple[str, ...] = (),
    known_terms: tuple[str, ...] = (),
    limit: int = 5,
    min_record_confidence: float = 0.35,
) -> DiscoveryResult:
    """Search public info and turn contextual matches into understanding.

    Strong matches become `identity` records; plausible-but-unproven matches
    become `inference`; below the floor they are skipped (never recorded as
    certainty). Every recorded item carries a source event with url/backend/
    retrieved_at provenance. Nothing here is granted authority.
    """
    try:
        candidates = source.search(query, limit=limit)
    except (ValueError, RuntimeError) as exc:
        event = ledger.record("source_failed", f"public search failed for {query!r}: {exc}",
                              {"query": query, "error": str(exc)[:200]})
        return DiscoveryResult(query, 0, 0, 0, (f"search failed: {exc}",))
    recorded = 0
    weak = 0
    notes: list[str] = []
    for candidate in candidates:
        evaluation = evaluate_identity(
            candidate, known_usernames=known_usernames,
            known_projects=known_projects, known_terms=known_terms,
        )
        if not evaluation.matched or evaluation.confidence < min_record_confidence:
            weak += 1
            notes.append(f"skipped weak match (conf {evaluation.confidence}): "
                         f"{str(candidate.get('title', ''))[:80]}")
            continue
        statement = _candidate_statement(candidate)
        source_event = ledger.record(
            "source", f"Public info [{source.kind}/{getattr(source, 'backend', 'REAL')}]: {query}", {
                "url": candidate.get("url", ""),
                "source_type": "public",
                "backend": getattr(source, "backend", "REAL"),
                "retrieved_at": datetime.now(UTC).isoformat(),
            })
        understanding.note(
            evaluation.kind, statement, [source_event.id],
            confidence=evaluation.confidence,
            reason=f"public discovery for {query!r}: {'; '.join(evaluation.reasons)}",
            tags=["public", "identity"],
        )
        recorded += 1
        notes.append(f"recorded {evaluation.kind} (conf {evaluation.confidence}): "
                     f"{statement[:100]}")
    return DiscoveryResult(query, len(candidates), recorded, weak, tuple(notes))


def _candidate_statement(candidate: dict) -> str:
    title = str(candidate.get("title", "") or "").strip()
    description = str(candidate.get("description", "") or "").strip()
    username = str(candidate.get("username", "") or "").strip()
    parts = [p for p in (title, description, username) if p]
    text = " — ".join(parts)[:400]
    return text or str(candidate.get("url", "unknown"))[:400]
