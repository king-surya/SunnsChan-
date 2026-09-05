"""Knowledge ingest: external documents -> evaluated, provenanced knowledge.

Pipeline: fetch -> cache check -> chunk -> evaluate (corroboration,
contradiction, source reliability) -> propose/affirm/contradict ->
curiosity for novel terms. Retrieved information is NEVER treated as truth:
new external claims cap at BELIEF confidence and carry their source event,
backend label, and evaluation reasons. Failures degrade to auditable events;
local cognition continues untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .comms import scrub_secrets
from .knowledge import KnowledgeStore
from .memory import EventLedger, tokenize
from .reflection import CONTRAST_MARKERS
from .sources import ApiSource, FetchedDocument, SourceAdapter


EXTERNAL_CONFIDENCE_CAP = 0.69  # external claims start below FACT; experience may affirm later


@dataclass(frozen=True)
class ClaimEvaluation:
    confidence: float
    reasons: tuple[str, ...]
    corroborated_ids: tuple[int, ...] = ()
    contradicted_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class IngestResult:
    status: str  # stored | cached | failed | rejected
    knowledge_ids: tuple[int, ...] = ()
    source_event_id: int | None = None
    evaluation_notes: tuple[str, ...] = ()
    cached: bool = False


def _overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def evaluate_claim(text: str, knowledge: KnowledgeStore, *,
                   source_reliability: float = 0.5) -> ClaimEvaluation:
    """Score one candidate claim against existing knowledge.

    Base 0.45 (external = uncertain by default). Corroboration adds up to
    +0.15; contradictions subtract 0.15 and are recorded; source reliability
    contributes ±0.05. Hard cap keeps fresh external claims out of FACT.
    """
    if not 0.0 <= source_reliability <= 1.0:
        raise ValueError("source_reliability must be between 0 and 1")
    tokens = set(tokenize(text))
    corroborated: list[int] = []
    contradicted: list[int] = []
    lowered = text.lower()
    has_contrast = any(marker in lowered for marker in CONTRAST_MARKERS)
    for record in knowledge.recall(text, limit=10):
        ratio = _overlap_ratio(tokens, set(tokenize(record.statement)))
        if ratio >= 0.5:
            corroborated.append(record.id)
        elif has_contrast and ratio >= 0.3:
            contradicted.append(record.id)
    confidence = 0.45 + 0.05 * min(len(corroborated), 3)
    reasons = [f"base 0.45 (external, unverified)"]
    if corroborated:
        reasons.append(f"corroborated by {len(corroborated)} existing claim(s)")
    if contradicted:
        confidence -= 0.15
        reasons.append(f"contrasts with {len(contradicted)} existing claim(s)")
    confidence += 0.05 if source_reliability >= 0.7 else (-0.05 if source_reliability <= 0.3 else 0.0)
    reasons.append(f"source reliability {source_reliability:.2f}")
    confidence = round(max(0.2, min(EXTERNAL_CONFIDENCE_CAP, confidence)), 4)
    return ClaimEvaluation(confidence, tuple(reasons), tuple(corroborated), tuple(contradicted))


def chunk_text(text: str, *, min_chars: int = 40, max_chunks: int = 5) -> list[str]:
    """Split into substantive chunks; drop stubs. Deterministic."""
    parts = [p.strip() for p in text.replace("\r", "").split("\n") if p.strip()]
    chunks = [p for p in parts if len(p) >= min_chars][:max_chunks]
    if not chunks and text.strip():
        chunks = [text.strip()[:2000]]
    return chunks


def knowledge_for_event(store: KnowledgeStore, event_id: int) -> list:
    """All records (any status) sourced from an event. Reverse provenance."""
    return [r for r in store.history(limit=1000) if event_id in r.source_ids]


def find_fresh_source(ledger: EventLedger, url: str, *, max_age_days: int = 30,
                      now=None) -> int | None:
    """Return a fresh prior source event id for this URL, if any."""
    from datetime import timedelta

    moment = now or datetime.now(UTC)
    for event in ledger.events_of_kind("source", limit=100):
        if event.metadata.get("url") != url:
            continue
        retrieved = event.metadata.get("retrieved_at", "")
        try:
            age = moment - datetime.fromisoformat(retrieved)
        except ValueError:
            continue
        if age <= timedelta(days=max_age_days):
            return event.id
    return None


def acquire(adapter: SourceAdapter, url: str, ledger: EventLedger, knowledge: KnowledgeStore, *,
            curiosities=None, max_age_days: int = 30, source_reliability: float = 0.5,
            tags_extra: tuple[str, ...] = ()) -> IngestResult:
    """Fetch a URL and ingest it as evaluated, provenanced knowledge."""
    cached_id = find_fresh_source(ledger, url, max_age_days=max_age_days)
    if cached_id is not None:
        existing = [r.id for r in knowledge_for_event(knowledge, cached_id)
                    if r.status in {"active", "uncertain", "contradicted"}]
        return IngestResult("cached", tuple(existing), cached_id,
                            ("fresh source event reused; no refetch",), True)
    try:
        doc = adapter.fetch(url)
    except (ValueError, RuntimeError) as exc:
        event = ledger.record("source_failed", f"Fetch failed for {url}: {exc}",
                              {"url": url, "error": str(exc)[:200]})
        return IngestResult("failed", (), event.id, (f"fetch failed: {exc}",))
    return _ingest_document(doc, url, ledger, knowledge, curiosities=curiosities,
                            source_reliability=source_reliability, tags_extra=tags_extra)


def acquire_api(api: ApiSource, query: str, ledger: EventLedger, knowledge: KnowledgeStore, *,
                curiosities=None, source_reliability: float = 0.5) -> IngestResult:
    """Ingest structured API records as one document per record."""
    try:
        records = api.query(query)
    except (ValueError, RuntimeError) as exc:
        event = ledger.record("source_failed", f"API query failed for {query!r}: {exc}",
                              {"query": query, "error": str(exc)[:200]})
        return IngestResult("failed", (), event.id, (f"api failed: {exc}",))
    if not records:
        return IngestResult("rejected", (), None, ("api returned no records",))
    from .sources import FetchedDocument

    ids: list[int] = []
    notes: list[str] = []
    source_event: int | None = None
    for index, item in enumerate(records[:5]):
        title = str(item.get("title", "") or "")
        body = str(item.get("text", "") or item.get("snippet", "") or "")
        statement = f"{title}: {body}".strip(": ")[:2000]
        if len(statement) < 20:
            continue
        doc = FetchedDocument(f"api:{api.kind}:{query}#{index}", "api", statement,
                              title=title, backend=getattr(api, "backend", "MOCK"))
        result = _ingest_document(doc, doc.url, ledger, knowledge, curiosities=None,
                                  source_reliability=source_reliability)
        ids.extend(result.knowledge_ids)
        notes.extend(result.evaluation_notes)
        source_event = source_event or result.source_event_id
    if curiosities is not None and ids:
        _open_novel_curiosities(ledger, curiosities, [str(r.get("text", "")) for r in records[:5]])
    return IngestResult("stored" if ids else "rejected", tuple(ids), source_event, tuple(notes))


def _ingest_document(doc: FetchedDocument, url: str, ledger: EventLedger, knowledge: KnowledgeStore, *,
                     curiosities=None, source_reliability: float = 0.5,
                     tags_extra: tuple[str, ...] = ()) -> IngestResult:
    source_event = ledger.record("source", f"Retrieved {url} [{doc.source_type}/{doc.backend}]", {
        "url": url, "source_type": doc.source_type, "backend": doc.backend,
        "retrieved_at": doc.retrieved_at.isoformat(), "truncated": doc.truncated,
    })
    chunks = chunk_text(doc.text)
    if not chunks:
        return IngestResult("rejected", (source_event.id,), source_event.id, ("no substantive content",))
    concept_tags = sorted(set(tokenize(" ".join(chunks))))[:3]
    tags = ["external", doc.source_type] + concept_tags + [t for t in tags_extra]
    ids: list[int] = []
    notes: list[str] = []
    for chunk in chunks:
        statement = scrub_secrets(chunk[:2000])
        if len(statement.strip()) < 20:
            continue
        evaluation = evaluate_claim(statement, knowledge, source_reliability=source_reliability)
        notes.append(f"{evaluation.confidence:.2f}: {'; '.join(evaluation.reasons)}")
        if evaluation.contradicted_ids and evaluation.confidence >= 0.4:
            new = knowledge.contradict(
                evaluation.contradicted_ids[0], statement, [source_event.id],
                evaluation.confidence, reason=f"external contradiction from {url}",
                tags=tags)
            ids.append(new.id)
        elif evaluation.corroborated_ids:
            affirmed = knowledge.affirm(evaluation.corroborated_ids[0], source_event.id)
            ids.append(affirmed.id)
        else:
            claim = knowledge.propose(statement, [source_event.id], evaluation.confidence,
                                      reason=f"external source {url} [{doc.backend}]",
                                      tags=tags)
            ids.append(claim.id)
    if curiosities is not None and chunks:
        _open_novel_curiosities(ledger, curiosities, chunks, knowledge=knowledge)
    return IngestResult("stored" if ids else "rejected", tuple(ids), source_event.id, tuple(notes))


def _open_novel_curiosities(ledger: EventLedger, curiosities, chunks: list[str], knowledge=None) -> None:
    """Novel salient terms become questions — bounded, deduplicated."""
    known: set[str] = set()
    if knowledge is not None:
        for record in knowledge.visible(limit=200):
            known.update(tokenize(record.statement))
    opened = 0
    for chunk in chunks:
        if opened >= 3:
            break
        for token in sorted(set(tokenize(chunk))):
            if len(token) < 5 or token in known:
                continue
            existing = " ".join(c.question for c in curiosities.open_curiosities(limit=50)).lower()
            if token in existing:
                continue
            source = ledger.record("observation", f"Encountered new concept: {token}.",
                                   {"origin": "ingest"})
            curiosities.open(f"What is {token} and how does it relate to current work?",
                             source.id, topic=token, importance=0.5, novelty=0.7)
            known.add(token)
            opened += 1
            if opened >= 3:
                break


def refresh_temporal(store: KnowledgeStore, *, max_age_days: int = 90,
                     now=None) -> list[int]:
    """Mark stale external claims outdated. History kept; belief flagged.

    Only claims tagged external with no recent affirmation age out; lived
    experience claims are never touched by this sweep.
    """
    from datetime import timedelta

    moment = now or datetime.now(UTC)
    marked: list[int] = []
    for record in store.visible(limit=500):
        if "external" not in record.tags or record.status != "active":
            continue
        if moment - record.updated_at <= timedelta(days=max_age_days):
            continue
        store.mark_outdated(record.id, f"no corroboration for over {max_age_days} days")
        marked.append(record.id)
    return marked
