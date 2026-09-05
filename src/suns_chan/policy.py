"""Policy before execution: autonomy must have a visible boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class ActionVerdict(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class ActionRequest:
    name: str
    risk: str
    description: str
    scope: str = "external"
    estimated_cost: int = 1


class PolicyGate:
    """Small default policy; executors must honor its return value."""

    def __init__(self, approver: Callable[[ActionRequest], bool] | None = None) -> None:
        self._approver = approver

    def evaluate(self, request: ActionRequest) -> ActionVerdict:
        if request.estimated_cost < 1 or request.estimated_cost > 100:
            return ActionVerdict.DENY
        if request.risk == "destructive":
            return ActionVerdict.DENY
        if request.risk == "read_only":
            return ActionVerdict.ALLOW
        if request.risk not in {"write", "command", "network", "message", "install"}:
            return ActionVerdict.DENY
        if request.scope == "sandbox" and request.name in {
            "sandbox.write",
            "sandbox.command",
            "sandbox.install",
            "sandbox.network",
        }:
            return ActionVerdict.ALLOW
        if self._approver and self._approver(request):
            return ActionVerdict.ALLOW
        return ActionVerdict.REQUIRE_APPROVAL
