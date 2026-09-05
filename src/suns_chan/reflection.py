"""Lightweight reflection: recent experience -> structured, evidenced findings.

Reflection inspects; it never rewrites raw events and never touches
personality traits or policy. Findings are persisted as `reflection` events
(evidence IDs in metadata) so consolidation can promote them to knowledge
later. All heuristics are deterministic and documented below:

- success/failure patterns: outcome events greedily clustered by token-set
  Jaccard similarity >= 0.5; clusters with >= min_support become findings.
- open questions: observations ending with "?".
- preference candidates: texts containing prefer/like/love/favorite words.
- skill candidates: repeated (>= min_support) success lessons with similar
  wording — a repeatable workflow worth naming.
- contradictions: an observation pair sharing >= 3 significant tokens where
  the later text contains an explicit contrast marker.
- lessons: individual learning events are passed through as lesson findings
  so single valuable lessons are not lost while patterns accumulate.
"""

from __future__ import annotations

from dataclasses import dataclass

from .memory import EventLedger, MemoryEvent, tokenize
from .textutil import jaccard


FINDING_TYPES = (
    "success_pattern",
    "failure_pattern",
    "open_question",
    "preference_candidate",
    "skill_candidate",
    "contradiction",
    "lesson",
)

PREFERENCE_MARKERS = ("prefer", "prefers", "favorite", "likes", "loves", "always", "every morning")
CONTRAST_MARKERS = ("not ", "n't ", "never ", "moved from", "switched", "correction", "contradiction", "instead", "wrong")


@dataclass(frozen=True)
class ReflectionFinding:
    finding_type: str
    summary: str
    evidence_ids: tuple[int, ...]
    confidence: float


@dataclass(frozen=True)
class ReflectionReport:
    findings: tuple[ReflectionFinding, ...]
    recorded_event_ids: tuple[int, ...]


def _tokens_match(left: str, right: str) -> bool:
    """Same light-stem parity as memory recall: stop/stopped count as overlap."""
    if left == right:
        return True
    if len(left) >= 4 and right.startswith(left):
        return True
    if len(right) >= 4 and left.startswith(right):
        return True
    return False


def _overlap_size(left: set[str], right: set[str]) -> int:
    matched: set[str] = set()
    for token in left:
        if any(_tokens_match(token, other) for other in right):
            matched.add(token)
    return len(matched)


def _cluster(events: list[MemoryEvent], *, threshold: float = 0.5) -> list[list[MemoryEvent]]:
    clusters: list[list[MemoryEvent]] = []
    for event in events:
        tokens = set(tokenize(event.text))
        placed = False
        for cluster in clusters:
            if jaccard(tokens, set(tokenize(cluster[0].text))) >= threshold:
                cluster.append(event)
                placed = True
                break
        if not placed:
            clusters.append([event])
    return clusters


def _pattern_confidence(cluster_size: int) -> float:
    return round(min(0.85, 0.45 + 0.05 * cluster_size), 4)


def reflect(
    ledger: EventLedger,
    *,
    min_support: int = 2,
    max_findings: int = 10,
    limit_per_kind: int = 100,
) -> ReflectionReport:
    if min_support < 2:
        raise ValueError("min_support must be at least 2 (no single-observation patterns)")
    if max_findings < 1:
        raise ValueError("max_findings must be at least 1")
    findings: list[ReflectionFinding] = []

    outcomes = ledger.events_of_kind("outcome", limit=limit_per_kind)
    for result in ("success", "failure"):
        matching = [e for e in outcomes if e.metadata.get("outcome") == result]
        for cluster in _cluster(matching):
            if len(cluster) < min_support:
                continue
            finding_type = "success_pattern" if result == "success" else "failure_pattern"
            representative = sorted(cluster, key=lambda e: e.id)[0]
            findings.append(
                ReflectionFinding(
                    finding_type,
                    f"{result} pattern ({len(cluster)}x): {representative.text[:160]}",
                    tuple(sorted(e.id for e in cluster)),
                    _pattern_confidence(len(cluster)),
                )
            )

    learnings = ledger.events_of_kind("learning", limit=limit_per_kind)
    for event in learnings:
        findings.append(
            ReflectionFinding("lesson", event.text[:200], (event.id,), 0.6)
        )

    observations = ledger.events_of_kind("observation", limit=limit_per_kind)
    for event in observations:
        lowered = event.text.lower()
        if event.text.rstrip().endswith("?"):
            findings.append(
                ReflectionFinding("open_question", event.text[:200], (event.id,), 0.4)
            )
        elif any(marker in lowered for marker in PREFERENCE_MARKERS):
            findings.append(
                ReflectionFinding(
                    "preference_candidate", event.text[:200], (event.id,), 0.35
                )
            )

    success_texts = [e for e in outcomes if e.metadata.get("outcome") == "success"]
    for cluster in _cluster(success_texts):
        if len(cluster) >= min_support:
            representative = sorted(cluster, key=lambda e: e.id)[0]
            findings.append(
                ReflectionFinding(
                    "skill_candidate",
                    f"repeatable workflow ({len(cluster)}x): {representative.text[:160]}",
                    tuple(sorted(e.id for e in cluster)),
                    _pattern_confidence(len(cluster)),
                )
            )

    ordered = sorted(observations, key=lambda e: e.id)
    for index, later in enumerate(ordered):
        lowered = later.text.lower()
        if not any(marker in lowered for marker in CONTRAST_MARKERS):
            continue
        later_tokens = set(tokenize(later.text))
        for earlier in ordered[:index]:
            if _overlap_size(later_tokens, set(tokenize(earlier.text))) >= 3:
                findings.append(
                    ReflectionFinding(
                        "contradiction",
                        f"possible contradiction: #{earlier.id} vs #{later.id}: {later.text[:160]}",
                        (earlier.id, later.id),
                        0.55,
                    )
                )
                break

    findings = sorted(findings, key=lambda f: (f.confidence, len(f.evidence_ids)), reverse=True)[:max_findings]
    recorded: list[int] = []
    for finding in findings:
        event = ledger.record(
            "reflection",
            f"[{finding.finding_type}] {finding.summary}",
            {
                "finding_type": finding.finding_type,
                "evidence_ids": list(finding.evidence_ids),
                "confidence": finding.confidence,
            },
        )
        recorded.append(event.id)
    return ReflectionReport(tuple(findings), tuple(recorded))
