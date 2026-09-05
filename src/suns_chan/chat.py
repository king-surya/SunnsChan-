"""Terminal chat boundary: TurnContext -> LLM -> validated Decision.

This module owns prompt construction and inspection formatting only.
Persistence stays in AgentCore/EventLedger; authority stays with
PolicyGate/ToolRegistry. Provider failures surface as visible error
text, never as fabricated tool results.
"""

from __future__ import annotations

from .context import build_knowledge_section, build_user_understanding_section
from .core import Decision, TurnContext
from .knowledge import KnowledgeStore
from .llm import LLMMessage, LLMProvider, LLMRequest
from .memory import EventLedger
from .schemas import DecisionValidationError, parse_decision_json


def _fmt_reliability(value: float | None) -> str:
    return "?" if value is None else f"{value:.2f}"


def build_system_prompt(context: TurnContext) -> str:
    identity = context.identity
    lines = [
        f"You are {identity.name}. {identity.role}",
        f"Traits: {', '.join(identity.traits)}.",
        f"Style: {identity.expression_style}",
        f"Honesty boundary: {identity.honesty_boundary}",
        f"Dynamic state: curiosity={context.state.curiosity} confidence={context.state.confidence} "
        f"stress={context.state.stress} energy={context.state.energy} rhythm={context.state.rhythm}.",
    ]
    if context.state.interests:
        top = sorted(context.state.interests.items(), key=lambda kv: kv[1], reverse=True)[:5]
        lines.append("Interests: " + ", ".join(f"{k}({v})" for k, v in top) + ".")
    if context.active_goals:
        lines.append("Active goals: " + "; ".join(g.text for g in context.active_goals[:5]) + ".")
    lines.append(
        "Reply with a single JSON object matching the decision schema: "
        '{"response": str, "action": {name, risk, description, scope?, estimated_cost?} | null, '
        '"goals": [{title, reason?, priority?}], "interest_tags": [str], '
        '"confidence": 0..1, "expected_outcome": str}. '
        "Propose an action only when the user asked for something doable; otherwise null."
    )
    return "\n".join(lines)


def build_user_prompt(context: TurnContext, knowledge_section: str = "",
                      understanding_section: str = "") -> str:
    lines = [f"Observation: {context.observation}"]
    if context.recalled_events:
        lines.append("Recalled memory (kind: text):")
        for event in context.recalled_events[:8]:
            lines.append(f"- [{event.kind}] {event.text[:200]}")
    if understanding_section.strip():
        lines.append(understanding_section[:1200])
    if knowledge_section.strip():
        lines.append(knowledge_section[:1500])
    return "\n".join(lines)


def build_chat_decide(provider: LLMProvider, *, knowledge_store: KnowledgeStore | None = None,
                      understanding_store=None):
    """Adapt an LLMProvider to the DecisionProvider contract."""

    def decide(context: TurnContext) -> Decision:
        section = ""
        if knowledge_store is not None:
            section = build_knowledge_section(knowledge_store, context.observation)
        understanding_section = ""
        if understanding_store is not None:
            understanding_section = build_user_understanding_section(
                understanding_store, context.observation)
        request = LLMRequest(
            messages=(
                LLMMessage("system", build_system_prompt(context)),
                LLMMessage("user", build_user_prompt(context, section, understanding_section)),
            ),
            json_mode=True,
        )
        try:
            response = provider.generate(request)
        except Exception as exc:  # provider failure is visible, never fabricated
            return Decision(response=f"[provider error: {exc}]", confidence=0.1)
        try:
            return parse_decision_json(response.text)
        except DecisionValidationError as exc:
            raise DecisionValidationError(f"model returned invalid decision: {exc}") from exc

    return decide


def inspect_memory(ledger: EventLedger, query: str, limit: int = 8) -> str:
    events = ledger.recall(query, limit=limit)
    if not events:
        return "(no matching events)"
    return "\n".join(f"#{e.id} [{e.kind}] {e.text[:160]}" for e in events)


def inspect_audit(ledger: EventLedger, limit: int = 10) -> str:
    decisions = ledger.events_of_kind("decision", limit=limit)
    invalid = ledger.events_of_kind("invalid_decision", limit=limit)
    lines = [f"decisions={len(decisions)} invalid={len(invalid)}"]
    for event in decisions[:limit]:
        meta = event.metadata
        lines.append(
            f"#{event.id} action={meta.get('action')} verdict={meta.get('verdict')} "
            f"conf={meta.get('confidence')}: {event.text[:120]}"
        )
    for event in invalid[:limit]:
        lines.append(f"#{event.id} INVALID: {event.text[:120]}")
    return "\n".join(lines)


def inspect_state(ledger: EventLedger) -> str:
    state = ledger.load_state()
    if not state:
        return "(no state snapshot yet)"
    keys = ("curiosity", "confidence", "stress", "energy", "warmth", "directness", "rhythm")
    lines = [f"{k}={state.get(k)}" for k in keys]
    interests = state.get("interests", {}) or {}
    top = sorted(interests.items(), key=lambda kv: kv[1], reverse=True)[:8]
    lines.append("interests=" + (", ".join(f"{k}({v})" for k, v in top) or "(none)"))
    return "\n".join(lines)


def inspect_goals(ledger: EventLedger) -> str:
    opened = {e.id: e for e in ledger.events_of_kind("goal_opened", limit=100)}
    closed_ids = {e.metadata.get("goal_id") for e in ledger.events_of_kind("goal_closed", limit=100)}
    active = [e for i, e in opened.items() if i not in closed_ids]
    if not active:
        return "(no active goals)"
    return "\n".join(f"#{e.id} {e.text[:140]} (priority={e.metadata.get('priority')})" for e in active)


def handle_command(
    ledger: EventLedger, line: str, *, store: KnowledgeStore | None = None,
    extras: dict | None = None, learned=None, understanding=None,
    capabilities=None,
) -> tuple[bool, str]:
    """Chat slash-commands. Returns (handled, output).

    `extras` carries optional Phase 5 surfaces without hard dependencies:
    env_lines (list[str]), affect_note (str), graph_search (callable),
    comms_lines (list[str]). `learned` attaches the Phase 6 learned store
    for /learning, /patterns, /skills, /preferences, /strategies, /why.
    `understanding` attaches the Phase 7 personal-understanding store for
    /understanding <query>. `capabilities` attaches the capability registry
    for /capabilities, /capability <name>, /agents, /acquisitions.
    """
    extra = extras or {}
    text = line.strip()
    if text.startswith("/recall "):
        return True, inspect_memory(ledger, text[len("/recall "):].strip())
    if text == "/telemetry":
        events = ledger.events_of_kind("telemetry", limit=20)
        if not events:
            return True, "(no telemetry events observed yet)"
        from .telemetry import summarize_recent_telemetry

        lines = summarize_recent_telemetry(ledger, limit=10)
        header = f"telemetry events={len(ledger.events_of_kind('telemetry', limit=1000))} "
        header += f"(showing recent {len(lines)})"
        return True, header + "\n" + "\n".join(lines)
    if text == "/capabilities":
        if capabilities is None:
            return True, "(capability registry not attached)"
        available = capabilities.available()
        if not available:
            return True, "(no available capabilities yet)"
        return True, "\n".join(
            f"#{c.id} [{c.kind}/{c.status} v{c.version or '?'}] {c.name} — "
            f"{c.description[:80]} (reliability={_fmt_reliability(capabilities.reliability(c.id))})"
            for c in available)
    if text.startswith("/capability "):
        if capabilities is None:
            return True, "(capability registry not attached)"
        name = text[len("/capability "):].strip()
        matches = capabilities.by_name(name)
        if not matches:
            return True, f"(no capability named {name!r})"
        c = matches[0]
        usages = capabilities.usage(c.id, limit=5)
        usage_lines = "\n".join(
            f"  [{u.result}] {u.task[:80]} ({u.note[:60]})" for u in usages) or "  (none)"
        return True, "\n".join([
            f"#{c.id} {c.name} v{c.version or '?'} [{c.kind}/{c.status}]",
            f"  description: {c.description}",
            f"  source: {c.source_type or '?'} {c.source_url or c.origin or ''}",
            f"  install: {c.install_method or '?'} | invoke: {c.invoke or '?'}",
            f"  location: {c.install_location or '?'} | checksum: {c.checksum[:16] or '?'}",
            f"  validated: {c.last_validated or '?'} | reliability={_fmt_reliability(capabilities.reliability(c.id))}",
            "  usage history:", usage_lines,
        ])
    if text == "/agents":
        if capabilities is None:
            return True, "(capability registry not attached)"
        agents = [c for c in capabilities.all() if c.kind == "agent"]
        if not agents:
            return True, "(no agents acquired)"
        return True, "\n".join(
            f"#{c.id} [{c.status} v{c.version or '?'}] {c.name} — {c.description[:80]}"
            for c in agents)
    if text == "/acquisitions":
        if capabilities is None:
            return True, "(capability registry not attached)"
        records = capabilities.all(limit=30)
        if not records:
            return True, "(no acquisitions recorded)"
        return True, "\n".join(
            f"#{c.id} [{c.status}] {c.name} v{c.version or '?'} from {c.source_type or c.origin or '?'}"
            for c in records)
    if text.startswith("/understanding "):
        if understanding is None:
            return True, "(understanding store not attached)"
        query = text[len("/understanding "):].strip()
        hits = understanding.relevant(query, limit=5)
        if not hits:
            return True, "(no relevant understanding yet)"
        from .context import understanding_label

        return True, "\n".join(
            f"#{h.id} [{understanding_label(h)} {h.confidence:.2f}] {h.statement[:160]} "
            f"(sources: " + ", ".join(str(i) for i in h.source_ids) + ")"
            for h in hits
        )
    if text == "/environment":
        lines = extra.get("env_lines") or []
        if not lines:
            recent = ledger.events_of_kind("sensor", limit=5)
            if recent:
                lines = [f"[{e.metadata.get('source')}/{e.metadata.get('metric')}] {e.text[:140]}" for e in recent]
        return True, "\n".join(lines) if lines else "(no sensors configured)"
    if text == "/affect":
        note = extra.get("affect_note")
        if not note:
            from .affect import expression_note
            from .state import AgentState

            note = expression_note(AgentState.from_dict(ledger.load_state()))
        return True, note
    if text.startswith("/graph "):
        query_text = text[len("/graph "):].strip()
        if not query_text:
            return True, "usage: /graph <topic>"
        search = extra.get("graph_search")
        if search is not None:
            return True, search(query_text)
        from .graph import build_index, format_paths
        from .memory import tokenize

        tokens = tokenize(query_text)
        if not tokens:
            return True, "(no searchable terms)"
        index = build_index(ledger, knowledge=store)
        return True, format_paths(index.query("topic", tokens[0]))
    if text == "/communications":
        lines = extra.get("comms_lines")
        if lines is None:
            events = ledger.events_of_kind("communication", limit=10)
            lines = [f"[{'sent' if e.metadata.get('sent') else 'blocked'}:{e.metadata.get('channel')}] "
                     f"to {e.metadata.get('recipient')}: {e.text[:120]}" for e in events]
        return True, "\n".join(lines) if lines else "(no communications)"
    if text.startswith("/knowledge "):
        if store is None:
            return True, "(knowledge store not attached)"
        query = text[len("/knowledge "):].strip()
        hits = store.recall(query, limit=5)
        if not hits:
            return True, "(no matching knowledge)"
        return True, "\n".join(
            f"#{h.id} [{h.status} {h.confidence:.2f}] {h.statement[:160]} (sources: "
            + ", ".join(str(i) for i in h.source_ids) + ")"
            for h in hits
        )
    if text.startswith("/sources "):
        if store is None:
            return True, "(knowledge store not attached)"
        try:
            record_id = int(text[len("/sources "):].strip())
        except ValueError:
            return True, "usage: /sources <knowledge_id>"
        try:
            explanation = store.explain(record_id)
        except ValueError as exc:
            return True, str(exc)
        lines = [
            f"#{explanation.record_id} [{explanation.status} {explanation.confidence:.2f}] {explanation.statement}",
            "supporting:",
            *(f"  E{s.event_id} [{s.kind}] {s.text[:140]}" for s in explanation.supporting),
            "contradicted by:",
            *(f"  E{s.event_id} [{s.kind}] {s.text[:140]}" for s in explanation.contradicted_by),
        ]
        return True, "\n".join(lines)
    if text == "/reflect":
        from .reflection import reflect

        report = reflect(ledger)
        if not report.findings:
            return True, "(no findings: need at least 2 similar outcomes, questions, or contrast markers)"
        return True, "\n".join(f"[{f.finding_type} {f.confidence:.2f}] {f.summary[:140]}" for f in report.findings)
    if text.startswith("/consolidate"):
        if store is None:
            return True, "(knowledge store not attached)"
        from .consolidation import consolidate

        parts = text.split()
        try:
            budget = int(parts[1]) if len(parts) > 1 else 10
        except ValueError:
            return True, "usage: /consolidate [budget]"
        result = consolidate(ledger, store, budget=budget)
        return True, f"created={result.created} covered={result.skipped_covered} budget_skipped={result.skipped_budget}"
    if text == "/audit":
        return True, inspect_audit(ledger)
    if text == "/state":
        return True, inspect_state(ledger)
    if text == "/goals":
        return True, inspect_goals(ledger)
    if text in {"/learning", "/patterns", "/skills", "/preferences", "/strategies"}:
        if learned is None:
            return True, "(learned store not attached)"
        kind = {"learning": None, "patterns": "pattern", "skills": "skill",
                "preferences": "preference", "strategies": "strategy"}[text[1:]]
        objects = learned.all(limit=50) if kind is None else learned.by_kind(kind, limit=50)
        if not objects:
            return True, "(no learned objects yet: run consolidation first)"
        return True, "\n".join(
            f"#{o.id} [{o.kind}/{o.status} {o.confidence:.2f}] {o.subject[:120]} "
            f"(+{o.support_count}/-{o.contra_count})" for o in objects[:15])
    if text.startswith("/why "):
        if learned is None:
            return True, "(learned store not attached)"
        try:
            learned_id = int(text[len("/why "):].strip())
        except ValueError:
            return True, "usage: /why <learned_id>"
        from .learned import explain_learning

        try:
            explanation = explain_learning(learned, ledger, learned_id)
        except ValueError as exc:
            return True, str(exc)
        lines = [
            f"What: {explanation.what}",
            f"Confidence: {explanation.confidence:.2f} ({explanation.breakdown})",
            f"Status: {explanation.status} | Last observed: {explanation.last_observed}",
            "Supporting:",
            *(f"  E{eid} [{kind}] {snippet[:120]}" for eid, kind, snippet in explanation.supporting),
            "Contradictions:",
            *(f"  E{eid} [{kind}] {snippet[:120]}" for eid, kind, snippet in explanation.contradictions),
            f"Conditions: {explanation.conditions}",
        ]
        return True, "\n".join(lines)
    if text in {"/quit", "/exit"}:
        return True, "__quit__"
    return False, ""
