"""Simulated affective rhythm: evidence-informed, interpretable, non-authorizing.

Not emotions, not consciousness — a computational behavioral state built ONLY
on the existing AgentState fields (energy, confidence, curiosity, stress,
warmth, directness, rhythm, interests). Updates derive from recorded
evidence (reflection findings, outcome streaks, novelty, session load), never
from a single raw success/failure. History stays in the ledger; affect is
current-state evaluation. AFFECT NEVER AUTHORIZES: no output of this module
reaches PolicyGate, tools, or infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass

from .memory import EventLedger
from .state import AgentState
from .textutil import clamp


@dataclass(frozen=True)
class AffectEvidence:
    reflection_types: tuple[str, ...] = ()
    recent_outcomes: tuple[str, ...] = ()  # oldest-first, success|failure|unknown
    new_curiosities: int = 0
    activities_this_session: int = 0
    contradictions_open: int = 0


@dataclass(frozen=True)
class AffectDelta:
    field: str
    before: float | str
    after: float | str
    reason: str


@dataclass(frozen=True)
class AttentionWeights:
    explore: float
    exploit: float
    rest: float


def collect_evidence(ledger: EventLedger, *, session_id: str = "", limit: int = 30,
                     curiosities=None) -> AffectEvidence:
    """Gather the evidence snapshot an affect update reasons over."""
    reflection_types: list[str] = []
    for event in ledger.events_of_kind("reflection", limit=limit):
        finding = event.metadata.get("finding_type", "")
        if finding:
            reflection_types.append(finding)
    outcomes = [e.metadata.get("outcome", "unknown")
                for e in ledger.events_of_kind("outcome", limit=10)][::-1]
    fresh = len(curiosities.open_curiosities(limit=50)) if curiosities is not None else 0
    activities = 0
    if session_id:
        from .experience import experiences_of_session

        activities = len(experiences_of_session(ledger, session_id, limit=100))
    return AffectEvidence(tuple(reflection_types), tuple(outcomes), fresh, activities, 0)


def update_affect(state: AgentState, evidence: AffectEvidence) -> list[AffectDelta]:
    """Apply evidence-informed adjustments to existing state fields.

    Rules (all bounded, all explained):
    - repeated failure streak (>=3) lowers confidence, raises stress;
    - success streak (>=3) raises confidence modestly, eases stress;
    - open questions/contradictions raise curiosity;
    - fresh curiosities raise curiosity and warmth slightly;
    - long sessions without pause (>6 activities) drain energy and slow rhythm;
    - lessons present steady confidence (small upward nudge).
    """
    deltas: list[AffectDelta] = []

    def adjust(name: str, new_value: float, reason: str) -> None:
        old = getattr(state, name)
        new_value = clamp(new_value)
        if new_value != old:
            setattr(state, name, new_value)
            deltas.append(AffectDelta(name, old, new_value, reason))

    outcomes = list(evidence.recent_outcomes)
    streak_fail = len(outcomes) >= 3 and all(o == "failure" for o in outcomes[-3:])
    streak_success = len(outcomes) >= 3 and all(o == "success" for o in outcomes[-3:])
    if streak_fail:
        adjust("confidence", state.confidence - 0.08, "three consecutive failures")
        adjust("stress", state.stress + 0.10, "three consecutive failures")
    elif streak_success:
        adjust("confidence", state.confidence + 0.05, "three consecutive successes")
        adjust("stress", state.stress - 0.05, "three consecutive successes")

    open_q = sum(1 for t in evidence.reflection_types if t in {"open_question", "contradiction"})
    if open_q:
        adjust("curiosity", state.curiosity + 0.02 * min(open_q, 5), f"{open_q} open questions/contradictions")
    if evidence.new_curiosities:
        adjust("curiosity", state.curiosity + 0.01 * min(evidence.new_curiosities, 5), "fresh curiosities")
        adjust("warmth", state.warmth + 0.01, "engagement with new questions")
    if "lesson" in evidence.reflection_types:
        adjust("confidence", state.confidence + 0.02, "lessons consolidated")
    if evidence.activities_this_session > 6:
        adjust("energy", state.energy - 0.05, "long session without pause")
        if state.rhythm == "steady":
            state.rhythm = "slow"
            deltas.append(AffectDelta("rhythm", "steady", "slow", "long session without pause"))
    elif evidence.activities_this_session == 0 and state.rhythm == "slow":
        state.rhythm = "steady"
        deltas.append(AffectDelta("rhythm", "slow", "steady", "rested session"))
    return deltas


def apply_affect(ledger: EventLedger, evidence: AffectEvidence) -> list[AffectDelta]:
    """Load persisted state, update from evidence, save with an audit cause."""
    state = AgentState.from_dict(ledger.load_state())
    deltas = update_affect(state, evidence)
    if deltas:
        anchor = ledger.record("affect", "; ".join(f"{d.field}: {d.before}->{d.after} ({d.reason})" for d in deltas),
                               {"deltas": [(d.field, d.before, d.after, d.reason) for d in deltas]})
        ledger.save_state(state.to_dict(), cause_event_id=anchor.id)
    return deltas


def attention_weights(state: AgentState) -> AttentionWeights:
    """Map current state to explore/exploit/rest tendencies (sum to 1)."""
    explore = 0.30 + 0.4 * state.curiosity + 0.2 * state.energy
    exploit = 0.30 + 0.4 * state.confidence + 0.1 * (1.0 - state.stress)
    rest = 0.10 + 0.5 * (1.0 - state.energy) + 0.3 * state.stress
    total = explore + exploit + rest
    return AttentionWeights(round(explore / total, 4), round(exploit / total, 4), round(rest / total, 4))


def selection_bias(weights: AttentionWeights) -> dict[str, float]:
    """Translate attention into activity-selection weight nudges (±0.1 max).

    Explore raises novelty/curiosity; exploit raises relevance/usefulness;
    rest damps everything toward waiting (returned as a wait_bias flag via
    negative usefulness). Only nudges — never overrides policy or budgets.
    """
    return {
        "novelty": round(0.1 * (weights.explore - 1 / 3) * 3, 4),
        "curiosity": round(0.1 * (weights.explore - 1 / 3) * 3, 4),
        "relevance": round(0.1 * (weights.exploit - 1 / 3) * 3, 4),
        "usefulness": round(0.1 * (weights.exploit - 1 / 3) * 3, 4),
    }


def expression_note(state: AgentState) -> str:
    """One-line presentation hint for avatar/chat. Descriptive, not prescriptive."""
    weights = attention_weights(state)
    dominant = max(("explore", weights.explore), ("exploit", weights.exploit), ("rest", weights.rest),
                   key=lambda item: item[1])[0]
    return (f"rhythm={state.rhythm} energy={state.energy:.2f} confidence={state.confidence:.2f} "
            f"attention={dominant}")
