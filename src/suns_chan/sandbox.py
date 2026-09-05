"""Capability descriptors for a sandbox. This module intentionally executes nothing."""

from __future__ import annotations

from dataclasses import dataclass

from .policy import ActionRequest


SANDBOX_ACTIONS = frozenset({"sandbox.write", "sandbox.command", "sandbox.install", "sandbox.network"})


@dataclass(frozen=True)
class SandboxRequest:
    action: str
    description: str
    sandbox_id: str
    estimated_cost: int = 1

    def to_action_request(self) -> ActionRequest:
        if not self.sandbox_id.strip():
            raise ValueError("sandbox_id is required")
        if self.action not in SANDBOX_ACTIONS:
            raise ValueError(f"unsupported sandbox action: {self.action}")
        risk = "install" if self.action == "sandbox.install" else self.action.removeprefix("sandbox.")
        return ActionRequest(
            name=self.action,
            risk=risk,
            description=self.description,
            scope="sandbox",
            estimated_cost=self.estimated_cost,
        )
