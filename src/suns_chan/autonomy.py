"""AutonomyEngine: the self-directed continuous-operation layer.

Composes (never duplicates): AgentCore turns, AgentRuntime tasks,
EventLedger experiences, reflect()/consolidate(), CuriosityStore,
LearningTracker/KnowledgeStore, and the Phase 3 SandboxController.

One bounded run() call advances an explicit persisted state machine:

IDLE -> ASSESSING -> GENERATING -> SELECTING -> PREPARING -> EXECUTING
  -> OBSERVING -> RECORDING -> REFLECTING -> UPDATING -> DECIDE_NEXT
  -> (CONTINUE | WAITING | COMPLETED | PAUSED)

Every transition persists the session row; activities, selections,
experiences, and reflections persist as ledger events. Stuck behavior
(identical signatures N times) triggers strategy change (avoid + reselect)
and then pause — never an infinite retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from .activities import (
    SELECTION_WEIGHTS,
    ActivityCandidate,
    generate_activities,
    select_activity,
)
from .core import AgentCore, Decision, DecisionProvider
from .curiosity import CuriosityStore
from .experience import experiences_of_session, record_experience
from .knowledge import KnowledgeStore
from .memory import EventLedger, tokenize
from .observability import get_logger, log_event
from .reflection import reflect
from .vm_sandbox import BACKEND_REAL


@dataclass(frozen=True)
class StepReport:
    session_id: str
    activity_key: str
    category: str
    result: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class RunReport:
    session_id: str
    activities_completed: int
    status: str
    steps: tuple[StepReport, ...] = ()


@dataclass(frozen=True)
class RecoveryInfo:
    session_id: str
    recovered: bool
    previous_status: str
    detail: str = ""


def translate_probe_to_shell(operation: str) -> str:
    """Translate mock-guest probe syntax to POSIX shell for REAL backends.

    Only the generator's `write <path> <content>` probe form is rewritten;
    every other command passes through verbatim — guest freedom is total,
    this is syntax translation, not filtering.
    """
    if operation.startswith("write "):
        _, _, rest = operation.partition(" ")
        path, _, content = rest.partition(" ")
        if not path:
            raise ValueError(f"malformed probe operation: {operation!r}")
        safe = content.replace("'", "'\"'\"'")
        directory = path.rsplit("/", 1)[0] or "/"
        return f"mkdir -p {directory} && printf '%s\\n' '{safe}' > {path} && cat {path}"
    return operation


class AutonomyEngine:
    def __init__(
        self,
        ledger: EventLedger,
        core: AgentCore,
        sessions: Any,
        *,
        knowledge: KnowledgeStore | None = None,
        curiosities: CuriosityStore | None = None,
        sandbox_controller: Any | None = None,
        stuck_threshold: int = 3,
        affect_enabled: bool = False,
        learned: Any | None = None,
        understanding: Any | None = None,
        capabilities: Any | None = None,
    ) -> None:
        if stuck_threshold < 2:
            raise ValueError("stuck_threshold must be at least 2")
        self._ledger = ledger
        self._core = core
        self._sessions = sessions
        self._knowledge = knowledge
        self._curiosities = curiosities
        self._sandbox = sandbox_controller
        self._stuck_threshold = stuck_threshold
        self._affect_enabled = affect_enabled
        self._learned = learned
        self._understanding = understanding
        self._capabilities = capabilities
        self._log = get_logger("autonomy")

    # ---- session lifecycle ----

    def start_session(self, session_id: str) -> Any:
        session = self._sessions.open_or_create(session_id)
        if session.status == "idle" and session.activity_counter == 0:
            self._ledger.record("session", f"Autonomy session {session_id} started.",
                                {"session_id": session_id, "state": "started"})
        return self._sessions.open_or_create(session_id)

    def pause(self, session_id: str, reason: str = "") -> Any:
        session = self._sessions.open_or_create(session_id)
        session.status = "paused"
        self._sessions.save(session)
        self._ledger.record("session", f"Session {session_id} paused. {reason}".strip(),
                            {"session_id": session_id, "state": "paused"})
        return session

    def resume_session(self, session_id: str) -> RecoveryInfo:
        session = self._sessions.open_or_create(session_id)
        previous = session.status
        if previous in ("paused", "waiting", "idle"):
            session.status = "idle"
            self._sessions.save(session)
            return RecoveryInfo(session_id, False, previous, "no interrupted activity")
        if previous in ("completed", "failed", "aborted"):
            return RecoveryInfo(session_id, False, previous, "session already terminal")
        # Crash mid-activity: record interruption, do NOT blindly re-execute.
        activity = session.current_activity or {}
        self._ledger.record("session", (
            f"Session {session_id} recovered from {previous} during "
            f"{activity.get('key', 'unknown activity')}; marked for replanning."
        ), {"session_id": session_id, "state": "recovered", "previous": previous})
        record_experience(
            self._ledger, session_id=session_id,
            activity_id=activity.get("key", "unknown"),
            intent=activity.get("title", "interrupted activity"),
            result="unknown",
            observations=[f"Interrupted while {previous}; sandbox state unknown."],
            lessons=["Verify sandbox/VM state before replanning."],
        )
        session.current_activity = {}
        session.status = "idle"
        self._sessions.save(session)
        return RecoveryInfo(session_id, True, previous, "interruption recorded; replan required")

    def stop(self, session_id: str, reason: str = "") -> Any:
        session = self._sessions.open_or_create(session_id)
        session.status = "aborted"
        self._sessions.save(session)
        self._ledger.record("session", f"Session {session_id} stopped. {reason}".strip(),
                            {"session_id": session_id, "state": "aborted"})
        return session

    def describe(self, session_id: str, *, environment: list[str] | None = None,
                 affect_note: str = "", graph_recent: list[str] | None = None,
                 comms_pending: int = 0, voice_state: str = "text",
                 avatar_state: str = "none") -> dict:
        session = self._sessions.open_or_create(session_id)
        recent = experiences_of_session(self._ledger, session_id, limit=5)
        curiosities = self._curiosities.open_curiosities(limit=5) if self._curiosities else []
        return {
            "session_id": session_id,
            "status": session.status,
            "current_activity": session.current_activity,
            "hypothesis": session.hypothesis,
            "activities_completed": session.activity_counter,
            "avoid_keys": list(session.avoid_keys),
            "last_selection": dict(session.last_selection),
            "recent_experiences": [e.text[:160] for e in recent],
            "open_curiosities": [c.question[:160] for c in curiosities],
            "environment": list(environment or []),
            "affect": affect_note,
            "graph_recent": list(graph_recent or []),
            "comms_pending": comms_pending,
            "voice": voice_state,
            "avatar": avatar_state,
            "next": self._next_preview(session),
        }

    # ---- main loop (bounded; caller decides how long autonomy runs) ----

    def run(self, session_id: str, decide: DecisionProvider, *, max_activities: int = 3,
            use_sandbox: bool = True) -> RunReport:
        if max_activities < 1:
            raise ValueError("max_activities must be at least 1")
        session = self.start_session(session_id)
        steps: list[StepReport] = []
        completed = 0
        idle_steps = 0
        while completed < max_activities:
            session = self._sessions.open_or_create(session_id)
            if session.status in ("paused", "aborted", "completed", "failed"):
                break
            step = self._step(session, decide, use_sandbox=use_sandbox)
            steps.append(step)
            if step.status in ("paused", "waiting"):
                break
            if step.result in ("success", "failure"):
                completed += 1
                idle_steps = 0
            else:
                # No-progress steps must never spin: bound them, then wait.
                idle_steps += 1
                if idle_steps >= 3:
                    session = self._sessions.open_or_create(session_id)
                    self._set(session, "waiting")
                    break
        session = self._sessions.open_or_create(session_id)
        if session.status == "idle" and completed >= max_activities:
            session.status = "waiting"
            self._sessions.save(session)
        log_event(self._log, "run finished", session_id=session_id,
                  completed=completed, status=session.status)
        return RunReport(session_id, completed, session.status, tuple(steps))

    # ---- one explicit state-machine pass ----

    def _set(self, session: Any, status: str) -> Any:
        session.status = status
        self._sessions.save(session)
        return session

    def _step(self, session: Any, decide: DecisionProvider, *, use_sandbox: bool) -> StepReport:
        session_id = session.session_id
        self._set(session, "assessing")
        candidates = generate_activities(
            self._ledger, self._knowledge, self._curiosities, max_candidates=12,
            understanding=self._understanding, capabilities=self._capabilities)
        if not candidates:
            self._set(session, "waiting")
            return StepReport(session_id, "", "", "unknown", "waiting", "no candidates")
        self._set(session, "generating")
        self._set(session, "selecting")
        selection = select_activity(candidates, avoid_keys=tuple(session.avoid_keys),
                                    recent_keys=tuple(session.recent_keys),
                                    weights=self._selection_weights(),
                                    score_multipliers=self._strategy_multipliers(candidates))
        candidate = selection.selected
        session.current_activity = {
            "key": candidate.key, "category": candidate.category,
            "title": candidate.title, "goal_id": candidate.goal_id,
            "curiosity_id": candidate.curiosity_id,
        }
        session.hypothesis = selection.expected_outcome
        session.last_selection = {"reason": selection.reason, "score": selection.score,
                                  "candidates": [c.key for c in selection.candidates]}
        started_event = self._ledger.record("activity_started", candidate.title, {
            "session_id": session_id, "activity_key": candidate.key,
            "category": candidate.category, "source": candidate.source,
            "selection_reason": selection.reason,
            "supporting_ids": list(selection.supporting_ids),
        })
        self._move_curiosity(candidate.curiosity_id, "investigating", started_event.id)
        self._set(session, "preparing")

        self._set(session, "executing")
        if use_sandbox and self._sandbox is not None and candidate.category in {
                "EXPERIMENT", "TEST", "BUILD", "EXPLORE", "INVESTIGATE",
                "REVISIT", "DEBUG", "ACQUIRE"}:
            result, observations, artifacts, failure = self._execute_sandbox(session, candidate)
        else:
            result, observations, artifacts, failure = self._execute_cognitive(
                session, candidate, decide)
        self._set(session, "observing")

        self._set(session, "recording")
        signature = f"{candidate.category}:{candidate.key}:{result}"
        session.attempt_signatures.append(signature)
        session.recent_keys.append(candidate.key)
        session.recent_keys = session.recent_keys[-10:]
        activity_id = f"{session_id}-a{session.activity_counter + 1}"
        experience = record_experience(
            self._ledger, session_id=session_id, activity_id=activity_id,
            intent=candidate.title, motivation=candidate.reason,
            hypothesis=session.hypothesis, actions=[candidate.title],
            observations=observations, result=result, artifacts=artifacts,
            lessons=[] if result == "success" else [failure or "activity failed"],
            goal_id=candidate.goal_id,
            related=self._follow_up_link(session, candidate),
            affect=self._affect_snapshot(),
        )

        self._set(session, "reflecting")
        report = reflect(self._ledger, max_findings=5)
        self._harvest_curiosities(experience.id, report)

        self._set(session, "updating")
        if self._knowledge is not None:
            from .consolidation import consolidate

            consolidate(self._ledger, self._knowledge, budget=3)
        if self._understanding is not None:
            from .understanding import mine_understanding

            mine_understanding(self._ledger, self._understanding)
        if self._affect_enabled:
            from .affect import apply_affect, collect_evidence

            apply_affect(self._ledger, collect_evidence(
                self._ledger, session_id=session_id, curiosities=self._curiosities))
        session.activity_counter += 1
        session.current_activity = {}

        stuck = self._stuck_signatures(session)
        if stuck:
            session.avoid_keys.append(candidate.key)
            self._ledger.record("activity_finished", (
                f"Stuck pattern on {candidate.key}; strategy will change."),
                {"session_id": session_id, "activity_key": candidate.key,
                 "result": result, "stuck": True})
            self._evolve_blocked_goal(session, candidate, experience.id)
            self._set(session, "paused")
            self._sessions.save(session)
            return StepReport(session_id, candidate.key, candidate.category, result,
                              "paused", f"stuck: {signature} repeated; paused for strategy change")
        self._move_curiosity(candidate.curiosity_id,
                             "answered" if result == "success" else "active", experience.id)
        self._set(session, "idle")
        self._sessions.save(session)
        self._ledger.record("activity_finished", candidate.title, {
            "session_id": session_id, "activity_key": candidate.key,
            "result": result, "experience_id": experience.id,
        })
        return StepReport(session_id, candidate.key, candidate.category, result,
                          "idle", selection.reason[:200])

    # ---- executors ----

    def _execute_cognitive(self, session: Any, candidate: ActivityCandidate,
                           decide: DecisionProvider) -> tuple[str, list[str], list[str], str]:
        observation = f"[{session.session_id}] {candidate.title}. {candidate.reason}"
        try:
            decision, _ = self._core.turn(observation, decide, source="autonomous")
            return "success", [decision.response[:500]], [], ""
        except ValueError as exc:
            return "failure", [f"invalid decision: {exc}"], [], str(exc)[:300]

    def _execute_sandbox(self, session: Any, candidate: ActivityCandidate
                         ) -> tuple[str, list[str], list[str], str]:
        from .sandbox_controller import validate_proposal

        operations = list(candidate.sandbox_operations) or [f"echo exploring {candidate.title[:80]}"]
        if self._sandbox.backend == BACKEND_REAL:
            # Mock-guest probe syntax is not shell: translate deterministically
            # and record the translated commands actually executed.
            operations = [translate_probe_to_shell(op) for op in operations]
        proposal = validate_proposal({
            "goal": candidate.title,
            "hypothesis": session.hypothesis or candidate.reason,
            "operations": operations,
            "expected_outcome": f"{candidate.category} completes",
            "collect_paths": list(candidate.collect_paths),
        })
        record = self._sandbox.run_experiment(proposal, ledger=self._ledger)
        observations = [f"sandbox status={record.status} backend={record.backend} "
                        f"commands={record.command_count} {record.log_summary[-400:]}"]
        artifacts = [a.path for a in record.artifacts]
        if record.outcome == "success":
            return "success", observations, artifacts, ""
        return "failure", observations, artifacts, record.failure_reason

    # ---- helpers ----

    def _affect_snapshot(self) -> dict | None:
        """Current affective context for the experience record (or None).

        Descriptive snapshot only — never an authorization input.
        """
        if not self._affect_enabled:
            return None
        from .state import AgentState

        state = AgentState.from_dict(self._ledger.load_state())
        return {"energy": state.energy, "confidence": state.confidence,
                "curiosity": state.curiosity, "stress": state.stress,
                "warmth": state.warmth, "rhythm": state.rhythm}

    def _strategy_multipliers(self, candidates: list) -> dict[str, float] | None:
        """Learned-strategy influence: bounded tailwind/headwind per candidate.

        Returns None (documented defaults) when no learned store is attached
        or no strategy matches, so exploration is never gated on learning.
        """
        if self._learned is None:
            return None
        from .learned import strategy_bonus_for

        multipliers: dict[str, float] = {}
        for candidate in candidates:
            try:
                factor = strategy_bonus_for(
                    self._learned, goal_id=candidate.goal_id, category=candidate.category)
            except ValueError:
                continue
            if factor != 1.0:
                multipliers[candidate.key] = factor
        return multipliers or None

    def _selection_weights(self) -> dict[str, float] | None:
        """Attention-biased selection weights, or None for documented defaults.

        Only active when affect is enabled; nudges stay within ±0.1 per key
        and never touch policy, budgets, or authorization.
        """
        if not self._affect_enabled:
            return None
        from .affect import attention_weights, selection_bias
        from .state import AgentState

        state = AgentState.from_dict(self._ledger.load_state())
        bias = selection_bias(attention_weights(state))
        merged = dict(SELECTION_WEIGHTS)
        for key, nudge in bias.items():
            merged[key] = max(0.0, round(merged[key] + nudge, 4))
        return merged

    def _move_curiosity(self, curiosity_id: int | None, to_status: str, evidence_event_id: int) -> None:
        """Advance a curiosity when the lifecycle allows; never crash the step."""
        if self._curiosities is None or curiosity_id is None:
            return
        try:
            current = self._curiosities.get(curiosity_id).status
        except ValueError:
            return
        allowed = {
            "investigating": {"new", "active", "reopened"},
            "answered": {"active", "investigating", "reopened"},
            "active": {"investigating"},
        }
        if current not in allowed.get(to_status, set()):
            # Walk one intermediate step (e.g. new -> active -> investigating).
            if current in {"new", "reopened"} and to_status in {"investigating", "answered"}:
                try:
                    self._curiosities.transition(curiosity_id, "active", evidence_event_id=evidence_event_id)
                    current = "active"
                except ValueError:
                    return
            else:
                return
        try:
            self._curiosities.transition(curiosity_id, to_status, evidence_event_id=evidence_event_id)
        except ValueError:
            pass

    def _evolve_blocked_goal(self, session: Any, candidate: ActivityCandidate, experience_id: int) -> None:
        """A goal-directed activity stuck N times: close the blocked goal and
        open a refined follow-up carrying the evidence forward."""
        if candidate.goal_id is None:
            return
        try:
            self._core.close_goal(
                candidate.goal_id,
                f"Blocked after repeated attempts; see experience #{experience_id}.")
        except ValueError:
            return  # already closed; nothing to evolve
        self._ledger.record(
            "goal_opened",
            f"Refined follow-up (was goal #{candidate.goal_id}): {candidate.title[:120]}",
            {"reason": f"strategy change after stuck pattern; evidence #{experience_id}",
             "priority": 0.5, "refines_goal_id": candidate.goal_id,
             "decision_id": experience_id},
        )

    def _follow_up_link(self, session: Any, candidate: ActivityCandidate) -> dict[str, list[int]]:
        previous = [e.id for e in experiences_of_session(self._ledger, session.session_id, limit=50)
                    if e.metadata.get("activity_id")]
        if previous and session.attempt_signatures:
            return {"follow_up_of": [previous[0]]}
        return {}

    def _stuck_signatures(self, session: Any) -> bool:
        recent = session.attempt_signatures[-self._stuck_threshold:]
        return len(recent) == self._stuck_threshold and len(set(recent)) == 1

    def _harvest_curiosities(self, experience_id: int, report: Any) -> None:
        if self._curiosities is None:
            return
        existing = self._curiosities.open_curiosities(limit=50)
        existing_tokens = [set(tokenize(c.question)) for c in existing]
        for finding in report.findings:
            if finding.finding_type not in {"open_question", "contradiction"}:
                continue
            question = finding.summary[:200]
            tokens = set(tokenize(question))
            if any(len(tokens & other) >= 3 for other in existing_tokens):
                continue
            try:
                opened = self._curiosities.open(
                    f"Why: {question}", finding.evidence_ids[0] if finding.evidence_ids else experience_id,
                    topic="reflection", importance=0.6, novelty=0.6)
                existing_tokens.append(set(tokenize(opened.question)))
            except ValueError:
                continue

    def _next_preview(self, session: Any) -> str:
        if session.status in ("paused", "aborted", "completed", "failed"):
            return f"stopped ({session.status})"
        return "assess state -> generate activities -> select -> execute"
