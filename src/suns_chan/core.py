"""The core turn: context in, model decision, policy verdict, durable event out."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .identity import IdentitySeed
from .memory import EventLedger, MemoryEvent
from .policy import ActionRequest, ActionVerdict, PolicyGate
from .state import AgentState


@dataclass(frozen=True)
class TurnContext:
    observation: str
    recalled_events: list[MemoryEvent]
    identity: IdentitySeed
    state: AgentState
    active_goals: list[MemoryEvent]


@dataclass(frozen=True)
class Decision:
    response: str
    action: ActionRequest | None = None
    goals: tuple["GoalProposal", ...] = ()
    interest_tags: tuple[str, ...] = ()
    confidence: float = 0.5
    expected_outcome: str = ""


@dataclass(frozen=True)
class GoalProposal:
    title: str
    reason: str
    priority: float = 0.5


DecisionProvider = Callable[[TurnContext], Decision]


class AgentCore:
    def __init__(self, ledger: EventLedger, policy: PolicyGate, identity: IdentitySeed) -> None:
        self._ledger = ledger
        self._policy = policy
        self._identity = identity

    def turn(self, observation: str, decide: DecisionProvider, *, source: str = "user") -> tuple[Decision, ActionVerdict | None]:
        observation_event = self._ledger.record("observation", observation, {"source": source})
        state = AgentState.from_dict(self._ledger.load_state())
        context = TurnContext(
            observation=observation,
            recalled_events=self._ledger.recall(observation),
            identity=self._identity,
            state=state,
            active_goals=self._active_goals(),
        )
        decision: Decision
        try:
            decision = decide(context)
        except ValueError as exc:
            # Invalid model/provider output becomes a persisted event, never an action.
            self._ledger.record(
                "invalid_decision", str(exc)[:500] or "invalid decision",
                {"observation_id": observation_event.id},
            )
            raise
        if isinstance(decision.confidence, bool) or not isinstance(decision.confidence, (int, float)):
            raise ValueError("decision confidence must be a number between 0 and 1")
        if not 0.0 <= float(decision.confidence) <= 1.0:
            raise ValueError("decision confidence must be between 0 and 1")
        verdict = self._policy.evaluate(decision.action) if decision.action else None
        state.express_interest(decision.interest_tags)
        decision_event = self._ledger.record(
            "decision",
            decision.response,
            {
                "observation_id": observation_event.id,
                "action": decision.action.name if decision.action else None,
                "verdict": verdict.value if verdict else None,
                "interest_tags": list(decision.interest_tags),
                "confidence": round(float(decision.confidence), 4),
                "expected_outcome": decision.expected_outcome,
            },
        )
        for goal in decision.goals:
            if not goal.title.strip() or not 0.0 <= goal.priority <= 1.0:
                raise ValueError("goals need a title and priority between 0 and 1")
            self._ledger.record(
                "goal_opened",
                goal.title,
                {"reason": goal.reason, "priority": goal.priority, "decision_id": decision_event.id},
            )
        self._ledger.save_state(state.to_dict(), cause_event_id=decision_event.id)
        return decision, verdict

    def autonomous_turn(self, reason: str, decide: DecisionProvider) -> tuple[Decision, ActionVerdict | None]:
        self._ledger.record("wake", reason, {"source": "autonomous"})
        return self.turn(reason, decide, source="autonomous")

    def record_outcome(
        self, decision_id: int, outcome: str, lesson: str, *, interest_tags: Iterable[str] = ()
    ) -> MemoryEvent:
        if outcome not in {"success", "failure", "unknown"}:
            raise ValueError("outcome must be success, failure, or unknown")
        outcome_event = self._ledger.record(
            "outcome", lesson, {"decision_id": decision_id, "outcome": outcome, "interest_tags": list(interest_tags)}
        )
        if outcome == "failure":
            self._ledger.record("learning", lesson, {"source_outcome_id": outcome_event.id, "decision_id": decision_id})
        state = AgentState.from_dict(self._ledger.load_state())
        state.learn_from_outcome(outcome, interest_tags)
        self._ledger.save_state(state.to_dict(), cause_event_id=outcome_event.id)
        return outcome_event

    def close_goal(self, goal_id: int, reason: str) -> MemoryEvent:
        active_ids = {event.id for event in self._active_goals()}
        if goal_id not in active_ids:
            raise ValueError("only an active goal can be closed")
        return self._ledger.record("goal_closed", reason, {"goal_id": goal_id})

    def _active_goals(self) -> list[MemoryEvent]:
        closed = {event.metadata.get("goal_id") for event in self._ledger.events_of_kind("goal_closed")}
        return [event for event in self._ledger.events_of_kind("goal_opened") if event.id not in closed]
