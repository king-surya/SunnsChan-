"""Multi-step agent runtime. Each step drives AgentCore.turn(), so every
observation, decision, policy verdict, goal and state update is persisted
in the event ledger — the runtime adds budgeting, tool dispatch and tracing.

PERCEIVE -> UNDERSTAND -> LOAD CONTEXT -> RETRIEVE MEMORY -> REASON -> PLAN
  -> CHECK PERMISSIONS -> EXECUTE ACTION -> OBSERVE RESULT -> EVALUATE
  -> REFLECT -> UPDATE MEMORY -> LEARN -> RESPOND

Reasoning (DecisionProvider) is injected. Tools run only via ToolRegistry
after the PolicyGate verdict that AgentCore already computed.
No dangerous host execution exists in this phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .core import AgentCore, DecisionProvider
from .environment import EnvProvider
from .events import EventBus, agent_finished, task_created, task_started, tool_executed, tool_failed, tool_requested
from .observability import TaskTrace, get_logger, log_event
from .policy import PolicyGate
from .tools import ToolRegistry


class Stage(str, Enum):
    PERCEIVE = "perceive"
    UNDERSTAND = "understand"
    LOAD_CONTEXT = "load_context"
    RETRIEVE_MEMORY = "retrieve_memory"
    REASON = "reason"
    PLAN = "plan"
    CHECK_PERMISSIONS = "check_permissions"
    EXECUTE_ACTION = "execute_action"
    OBSERVE_RESULT = "observe_result"
    EVALUATE = "evaluate"
    REFLECT = "reflect"
    UPDATE_MEMORY = "update_memory"
    LEARN = "learn"
    RESPOND = "respond"


@dataclass
class Task:
    title: str
    task_id: str = ""
    max_steps: int = 4


@dataclass
class StepResult:
    stage: Stage
    detail: str = ""


@dataclass
class TaskResult:
    task_id: str
    outcome: str  # success | failure | unknown
    response: str
    steps: list[StepResult] = field(default_factory=list)


class AgentRuntime:
    def __init__(
        self,
        core: AgentCore,
        policy: PolicyGate,
        tools: ToolRegistry,
        env: EnvProvider,
        bus: EventBus | None = None,
    ) -> None:
        self._core = core
        self._policy = policy
        self._tools = tools
        self._env = env
        self._bus = bus or EventBus()
        self._log = get_logger("runtime")

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def core(self) -> AgentCore:
        return self._core

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    def run_task(self, task: Task, decide: DecisionProvider) -> TaskResult:
        if not task.title.strip():
            raise ValueError("task title must be non-empty")
        if task.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        task_id = task.task_id or f"task-{abs(hash(task.title)) % 10_000_000}"
        trace = TaskTrace(task=task.title)
        steps: list[StepResult] = []
        self._bus.publish(task_created(task.title))
        self._bus.publish(task_started(task_id))

        def record(stage: Stage, detail: str) -> None:
            steps.append(StepResult(stage, detail))
            trace.step(stage.value, detail)
            log_event(self._log, f"stage.{stage.value}", task_id=task_id, detail=detail[:300])

        # PERCEIVE + UNDERSTAND + LOAD CONTEXT (env is read-only; memory owns writes)
        observation = self._env.observe()
        record(Stage.PERCEIVE, observation.summary)
        record(Stage.UNDERSTAND, task.title)
        record(Stage.LOAD_CONTEXT, f"env={self._env.name}")
        record(Stage.RETRIEVE_MEMORY, task.title)

        last_response = ""
        outcome = "unknown"
        for step in range(task.max_steps):
            record(Stage.REASON, f"step {step}")
            step_observation = task.title if step else f"{task.title} [env: {observation.summary}]"
            try:
                # Durable step: ledger records observation + decision + verdict,
                # persists goals/state, and evaluates the policy.
                decision, verdict = self._core.turn(step_observation, decide, source="task")
            except ValueError as exc:
                # core.turn already persisted the observation; never act on it.
                last_response = f"Invalid decision: {exc}"
                outcome = "failure"
                record(Stage.RESPOND, last_response[:300])
                break
            record(Stage.PLAN, f"goals={len(decision.goals)} interests={list(decision.interest_tags)}")
            if decision.action is None:
                last_response = decision.response
                outcome = "success"
                record(Stage.RESPOND, decision.response[:300])
                break
            record(Stage.CHECK_PERMISSIONS, f"{decision.action.name}={verdict.value if verdict else 'none'}")
            if verdict is None or verdict.value != "allow":
                last_response = f"Blocked by policy ({verdict.value if verdict else 'none'}): {decision.action.name}"
                outcome = "failure"
                record(Stage.RESPOND, last_response)
                break
            # EXECUTE ACTION: only registry tools; ActionRequest names map to tool names.
            self._bus.publish(tool_requested(decision.action.name, task_id))
            tool_result = self._tools.execute(decision.action.name, {"text": decision.action.description}, approved=True)
            record(Stage.EXECUTE_ACTION, f"{decision.action.name} ok={tool_result.ok}")
            record(Stage.OBSERVE_RESULT, (tool_result.output or tool_result.error)[:300])
            if tool_result.ok:
                self._bus.publish(tool_executed(decision.action.name, True, task_id))
                last_response = tool_result.output
                outcome = "success"
            else:
                self._bus.publish(tool_failed(decision.action.name, tool_result.error, task_id))
                last_response = tool_result.error
                outcome = "failure"
            record(Stage.EVALUATE, outcome)
            record(Stage.REFLECT, outcome)
            record(Stage.UPDATE_MEMORY, outcome)
            record(Stage.LEARN, outcome)
            if outcome == "success":
                record(Stage.RESPOND, last_response[:300])
                break
        self._bus.publish(agent_finished(task_id, outcome))
        return TaskResult(task_id=task_id, outcome=outcome, response=last_response, steps=steps)
