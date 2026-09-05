"""Tool common interface: Validate -> Security -> Approval -> Execute -> Audit.

Tool implementations never control the Agent; the runtime drives execution.
Dangerous host execution is NOT provided here. Shell/Proxmox/Docker are
future categories that must each arrive with their own policy + audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


RISK_LEVELS = ("read_only", "low_risk", "moderate_risk", "high_risk", "destructive", "write", "command", "network", "message", "install")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    version: str = "0.1.0"
    required_permission: str = "tools.use"
    risk_level: str = "read_only"
    timeout_secs: float = 30.0
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool name must be non-empty")
        if self.risk_level not in RISK_LEVELS:
            raise ValueError(f"unknown risk level: {self.risk_level}")


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def success(cls, output: str, artifacts: dict[str, str] | None = None) -> "ToolResult":
        return cls(ok=True, output=output, artifacts=artifacts or {})

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        if not error.strip():
            raise ValueError("failure needs an error message")
        return cls(ok=False, error=error)


class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec:
        ...

    def validate(self, args: dict[str, Any]) -> None:
        ...

    def run(self, args: dict[str, Any]) -> ToolResult:
        ...


class ToolRegistry:
    """Owns specs + dispatch. Security/approval/audit are injected, not hardcoded."""

    def __init__(
        self,
        security_check: Callable[[str, str], bool] | None = None,
        approver: Callable[[str, dict[str, Any]], bool] | None = None,
        auditor: Callable[[str, dict[str, Any], ToolResult], None] | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._security_check = security_check or (lambda name, risk: True)
        self._approver = approver
        self._auditor = auditor or (lambda name, args, result: None)

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"tool already registered: {tool.spec.name}")
        self._tools[tool.spec.name] = tool

    def list_specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def execute(self, name: str, args: dict[str, Any], *, approved: bool = False) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(f"unknown tool: {name}")
        try:
            tool.validate(args)
        except Exception as exc:
            result = ToolResult.failure(f"invalid input: {exc}")
            self._auditor(name, args, result)
            return result
        if not self._security_check(name, tool.spec.risk_level):
            result = ToolResult.failure(f"denied by security policy: {name}")
            self._auditor(name, args, result)
            return result
        if tool.spec.risk_level != "read_only" and not approved:
            if self._approver is None or not self._approver(name, args):
                result = ToolResult.failure(f"requires approval: {name}")
                self._auditor(name, args, result)
                return result
        try:
            result = tool.run(args)
        except Exception as exc:
            result = ToolResult.failure(f"tool raised: {exc}")
        self._auditor(name, args, result)
        return result


# --- Built-in safe tools (read-only; no host side effects) ---

@dataclass
class EchoTool:
    _spec: ToolSpec = field(
        default_factory=lambda: ToolSpec(
            name="echo", description="Deterministic echo for tests/dev.", risk_level="read_only"
        )
    )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def validate(self, args: dict[str, Any]) -> None:
        if "text" not in args or not isinstance(args["text"], str):
            raise ValueError("'text: str' is required")

    def run(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult.success(str(args["text"]))


@dataclass
class ClockTool:
    _spec: ToolSpec = field(
        default_factory=lambda: ToolSpec(
            name="clock", description="Return current UTC time (read-only).", risk_level="read_only"
        )
    )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def validate(self, args: dict[str, Any]) -> None:
        pass

    def run(self, args: dict[str, Any]) -> ToolResult:
        from datetime import UTC, datetime

        return ToolResult.success(datetime.now(UTC).isoformat())


def default_registry(
    auditor: Callable[[str, dict[str, Any], ToolResult], None] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry(auditor=auditor)
    registry.register(EchoTool())
    registry.register(ClockTool())
    return registry
