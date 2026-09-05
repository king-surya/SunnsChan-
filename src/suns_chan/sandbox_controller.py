"""Sandbox controller: the ONLY path from agent decisions to VM infrastructure.

The LLM never touches a provider. It produces ExperimentProposal data;
schema validation, approval gating, lifecycle, resource budgets, artifact
hashing, audit, and ledger recording all happen here. The controller holds
no model prompts and no credentials — providers own those, redacted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import time
from typing import Any, Callable

from .memory import EventLedger
from .observability import get_logger, log_event
from .vm_sandbox import (
    ArtifactRecord,
    BACKEND_REAL,
    NETWORK_MODES,
    SandboxProvider,
    VMResources,
    VMSpec,
)

TERMINAL_STATUSES = ("completed", "failed", "timeout", "rolled_back", "recreated", "cancelled")


@dataclass(frozen=True)
class ExperimentProposal:
    goal: str
    hypothesis: str
    operations: tuple[str, ...]
    expected_outcome: str
    time_budget_secs: int = 600
    network_mode: str = "isolated"
    vm_profile: str = "default"
    collect_paths: tuple[str, ...] = ()


@dataclass
class ExperimentRecord:
    experiment_id: str
    sandbox_id: str
    goal: str
    hypothesis: str
    status: str
    backend: str
    network_mode: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    command_count: int = 0
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    outcome: str = "unknown"  # success | failure | unknown
    failure_reason: str = ""
    rolled_back: bool = False
    log_summary: str = ""


def validate_proposal(data: dict[str, Any]) -> ExperimentProposal:
    """Schema validation for model-supplied data. Rejects malformed proposals."""
    if not isinstance(data, dict):
        raise ValueError("experiment proposal must be an object")
    for key in ("goal", "hypothesis", "expected_outcome"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"proposal.{key} must be a non-empty string")
    operations = data.get("operations")
    if not isinstance(operations, list) or not operations or any(
        not isinstance(op, str) or not op.strip() for op in operations
    ):
        raise ValueError("proposal.operations must be a non-empty list of command strings")
    budget = data.get("time_budget_secs", 600)
    if isinstance(budget, bool) or not isinstance(budget, int) or not 60 <= budget <= 86400:
        raise ValueError("proposal.time_budget_secs must be an integer between 60 and 86400")
    network_mode = data.get("network_mode", "isolated")
    if network_mode not in NETWORK_MODES:
        raise ValueError(f"unknown network mode: {network_mode!r}")
    collect_paths = data.get("collect_paths", [])
    if not isinstance(collect_paths, list) or any(not isinstance(p, str) for p in collect_paths):
        raise ValueError("proposal.collect_paths must be a list of strings")
    return ExperimentProposal(
        goal=data["goal"].strip(),
        hypothesis=data["hypothesis"].strip(),
        operations=tuple(op for op in operations),
        expected_outcome=data["expected_outcome"].strip(),
        time_budget_secs=budget,
        network_mode=network_mode,
        vm_profile=str(data.get("vm_profile", "default")),
        collect_paths=tuple(collect_paths),
    )


class SandboxController:
    def __init__(
        self,
        provider: SandboxProvider,
        *,
        template: str,
        default_resources: VMResources | None = None,
        approver: Callable[[ExperimentProposal], bool] | None = None,
        audit_sink: Callable[[dict], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._provider = provider
        self._template = template
        self._resources = default_resources or VMResources()
        self._approver = approver
        self._audit_sink = audit_sink or (lambda entry: None)
        self._clock = clock or time.monotonic
        self._log = get_logger("sandbox")
        self._cancelled: set[str] = set()
        self._counter = 0

    @property
    def backend(self) -> str:
        return self._provider.backend

    def cancel(self, experiment_id: str) -> None:
        self._cancelled.add(experiment_id)

    def _audit(self, action: str, sandbox_id: str, detail: str) -> None:
        entry = {"action": action, "sandbox_id": sandbox_id, "detail": detail,
                 "backend": self.backend, "at": datetime.now(UTC).isoformat()}
        self._audit_sink(entry)
        log_event(self._log, f"sandbox.{action}", sandbox_id=sandbox_id, detail=detail[:300])

    def run_experiment(
        self,
        proposal: ExperimentProposal,
        *,
        ledger: EventLedger | None = None,
        restore_after: bool = True,
        keep_vm: bool = False,
    ) -> ExperimentRecord:
        self._counter += 1
        experiment_id = f"exp-{self._counter:04d}"
        if self._provider.backend == BACKEND_REAL and self._approver is None:
            raise RuntimeError("real infrastructure requires an approval callback")
        if self._approver is not None and not self._approver(proposal):
            return ExperimentRecord(experiment_id, "", proposal.goal, proposal.hypothesis,
                                    "cancelled", self.backend, proposal.network_mode,
                                    outcome="unknown", failure_reason="approval denied")
        record = ExperimentRecord(experiment_id, "", proposal.goal, proposal.hypothesis,
                                  "proposed", self.backend, proposal.network_mode)
        deadline = self._clock() + proposal.time_budget_secs
        sandbox_id = ""
        try:
            record.status = "validated"
            sandbox_id = self._provider.create(VMSpec(
                name=experiment_id, template=self._template,
                resources=self._resources, network_mode=proposal.network_mode))
            record.sandbox_id = sandbox_id
            self._audit("create", sandbox_id, f"goal={proposal.goal[:120]} net={proposal.network_mode}")
            self._provider.start(sandbox_id)
            self._provider.wait_ready(sandbox_id, timeout_secs=min(180.0, proposal.time_budget_secs))
            self._provider.snapshot(sandbox_id, "baseline")
            record.status = "sandbox_prepared"
            record.started_at = datetime.now(UTC)

            record.status = "running"
            history: list[str] = []
            for command in proposal.operations:
                if experiment_id in self._cancelled:
                    record.status = "cancelled"
                    record.failure_reason = "cancelled by operator"
                    break
                remaining = deadline - self._clock()
                if remaining <= 0:
                    record.status = "timeout"
                    record.failure_reason = f"time budget exhausted after {record.command_count} commands"
                    break
                result = self._provider.execute(sandbox_id, command, timeout_secs=min(remaining, 300.0))
                record.command_count += 1
                history.append(f"$ {command}\n(exit {result.exit_code}) {result.stdout[-400:]}")
                if self._provider.status(sandbox_id) == "corrupted":
                    record.status = "corrupted"
                    record.failure_reason = f"guest corrupted itself at command: {command[:120]}"
                    break
                if result.exit_code != 0:
                    record.status = "failed"
                    record.failure_reason = f"command exited {result.exit_code}: {command[:120]}"
                    break
            record.log_summary = "\n".join(history)[-2000:]

            if record.status == "running":
                record.status = "completed"
                record.outcome = "success"
            elif record.status in {"failed", "timeout", "corrupted", "cancelled"}:
                record.outcome = "failure"
            record.status = "observing" if record.status == "completed" else record.status

            if record.status == "observing":
                try:
                    record.artifacts = self._provider.collect(sandbox_id, experiment_id, list(proposal.collect_paths))
                except ValueError as exc:
                    record.status = "failed"
                    record.outcome = "failure"
                    record.failure_reason = f"artifact collection failed: {exc}"
                else:
                    record.status = "completed"

            if record.status in {"failed", "corrupted"} and restore_after:
                try:
                    self._provider.restore(sandbox_id, "baseline")
                    record.rolled_back = True
                    record.status = "rolled_back"
                    self._audit("rollback", sandbox_id, f"experiment={experiment_id}")
                except (ValueError, RuntimeError) as exc:
                    self._audit("recreate", sandbox_id, f"restore failed ({exc}); recreating")
                    self._provider.destroy(sandbox_id)
                    sandbox_id = self._provider.create(VMSpec(
                        name=f"{experiment_id}-retry", template=self._template,
                        resources=self._resources, network_mode=proposal.network_mode))
                    self._provider.start(sandbox_id)
                    record.sandbox_id = sandbox_id
                    record.status = "recreated"
                    record.rolled_back = True
        except (RuntimeError, ValueError) as exc:
            record.status = "failed"
            record.outcome = "failure"
            record.failure_reason = str(exc)[:300]
        finally:
            record.ended_at = datetime.now(UTC)
            if sandbox_id and not keep_vm and record.status not in {"recreated"}:
                try:
                    self._audit("destroy", sandbox_id, f"experiment={experiment_id} status={record.status}")
                    self._provider.destroy(sandbox_id)
                except (ValueError, RuntimeError):
                    pass
            if ledger is not None:
                self._record_outcome(ledger, proposal, record)
        return record

    def _record_outcome(self, ledger: EventLedger, proposal: ExperimentProposal, record: ExperimentRecord) -> None:
        summary = (
            f"Experiment {record.experiment_id} [{record.status}] in {record.backend} sandbox: "
            f"{proposal.hypothesis} -> {record.outcome}. "
            f"{record.command_count} commands, {len(record.artifacts)} artifacts. "
            f"{record.failure_reason}"
        ).strip()
        experiment_event = ledger.record("experiment", summary, {
            "experiment_id": record.experiment_id,
            "sandbox_id": record.sandbox_id,
            "status": record.status,
            "backend": record.backend,
            "network_mode": record.network_mode,
            "artifacts": [a.path for a in record.artifacts],
            "artifact_hashes": [a.sha256 for a in record.artifacts],
            "rolled_back": record.rolled_back,
            "log_summary": record.log_summary[-1000:],
        })
        ledger.record("outcome", summary, {
            "outcome": record.outcome,
            "experiment_id": record.experiment_id,
            "cause_event_id": experiment_event.id,
        })
        if record.outcome == "failure":
            # Failed experiments become reflection candidates even without
            # repetition: a single failure is a lesson, not yet a pattern.
            ledger.record("learning", (
                f"Lesson from {record.experiment_id}: {record.failure_reason} "
                f"(hypothesis was: {proposal.hypothesis})"
            ), {
                "source_experiment_id": record.experiment_id,
                "source_outcome_id": experiment_event.id,
            })
