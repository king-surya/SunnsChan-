"""Context builder: relevant derived knowledge as labelled stanzas for LLM prompts.

Labels distinguish FACT / BELIEF / UNCERTAIN / CONTRADICTED / PREFERENCE /
SKILL so the model can weigh claims. Retrieval is bounded (top-k records,
character budget) — the full knowledge database is never dumped into prompts.
"""

from __future__ import annotations

from dataclasses import dataclass

from .knowledge import KnowledgeRecord, KnowledgeStore
from .understanding import UnderstandingRecord


@dataclass(frozen=True)
class KnowledgeStanza:
    label: str
    statement: str
    confidence: float
    record_id: int
    source_ids: tuple[int, ...]


def label_record(record: KnowledgeRecord) -> str:
    if "preference" in record.tags:
        return "PREFERENCE"
    if "skill" in record.tags:
        return "SKILL"
    if record.status == "uncertain":
        return "UNCERTAIN"
    if record.status == "contradicted":
        return "CONTRADICTED"
    if record.status == "outdated":
        return "OUTDATED"
    if record.confidence >= 0.7:
        return "FACT"
    return "BELIEF"


def build_knowledge_section(
    store: KnowledgeStore, query: str, *, limit: int = 3, char_budget: int = 1200
) -> str:
    """Ranked knowledge stanzas for `query`, capped at `char_budget` characters."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if char_budget < 1:
        raise ValueError("char_budget must be at least 1")
    lines: list[str] = []
    used = 0
    for record in store.recall(query, limit=limit):
        stanza = KnowledgeStanza(
            label_record(record), record.statement, record.confidence,
            record.id, record.source_ids,
        )
        sources = ", ".join(str(i) for i in stanza.source_ids)
        line = f"[{stanza.label} {stanza.confidence:.2f} #{stanza.record_id}] {stanza.statement} (sources: {sources})"
        if used + len(line) > char_budget:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return "Relevant knowledge:\n" + "\n".join(lines)


def understanding_label(record: UnderstandingRecord) -> str:
    """Human-readable label that keeps kinds distinct (never collapses
    inference/uncertainty into fact)."""
    return {
        "fact": "STATED",
        "observation": "OBSERVED",
        "preference": "PREFERENCE",
        "inference": "INFERRED",
        "uncertainty": "UNSURE",
        "interest": "INTEREST",
        "project": "PROJECT",
        "identity": "IDENTITY",
    }.get(record.kind, record.kind.upper())


def build_user_understanding_section(
    store, query: str, *, limit: int = 3, char_budget: int = 800
) -> str:
    """Bounded understanding stanzas for `query` — the "I know Surya" context,
    never a full-profile dump. Kinds are labelled so the model can weigh a
    STATED fact differently from an INFERRED guess."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if char_budget < 1:
        raise ValueError("char_budget must be at least 1")
    lines: list[str] = []
    used = 0
    for record in store.relevant(query, limit=limit):
        sources = ", ".join(str(i) for i in record.source_ids)
        line = (f"[{understanding_label(record)} {record.confidence:.2f} "
                f"#{record.id}] {record.statement} (sources: {sources})")
        if used + len(line) > char_budget:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return "What I know about you:\n" + "\n".join(lines)


def environment_section(summaries: list[str], *, char_budget: int = 600) -> str:
    """Bounded current-environment block. Summaries in, never raw telemetry."""
    if not summaries:
        return ""
    lines: list[str] = []
    used = 0
    for summary in summaries[:5]:
        line = f"- {summary[:160]}"
        if used + len(line) > char_budget:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return "Current environment:\n" + "\n".join(lines)


def telemetry_section(summaries: list[str], *, char_budget: int = 600) -> str:
    """Bounded recent main-server activity block (significant events only)."""
    if not summaries:
        return ""
    lines: list[str] = []
    used = 0
    for summary in summaries[:6]:
        line = f"- {summary[:160]}"
        if used + len(line) > char_budget:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return "Recent server activity:\n" + "\n".join(lines)


def affect_section(note: str) -> str:
    note = note.strip()
    return f"Affective state: {note[:200]}" if note else ""


def graph_section(paths_text: str, *, char_budget: int = 800) -> str:
    text = paths_text.strip()
    if not text or text == "(no paths)":
        return ""
    return "Related history:\n" + text[:char_budget]
