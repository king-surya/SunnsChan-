"""Internal event model + in-memory bus. No tight coupling between subsystems."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable


@dataclass(frozen=True)
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("event kind must be non-empty")


# Typed constructors for the lifecycle in the spec.
def user_message_received(text: str) -> Event:
    return Event("UserMessageReceived", {"text": text})


def task_created(title: str) -> Event:
    return Event("TaskCreated", {"title": title})


def task_started(task_id: str) -> Event:
    return Event("TaskStarted", {"task_id": task_id})


def tool_requested(name: str, task_id: str = "") -> Event:
    return Event("ToolRequested", {"tool": name, "task_id": task_id})


def tool_executed(name: str, ok: bool, task_id: str = "") -> Event:
    return Event("ToolExecuted", {"tool": name, "ok": ok, "task_id": task_id})


def tool_failed(name: str, error: str, task_id: str = "") -> Event:
    return Event("ToolFailed", {"tool": name, "error": error, "task_id": task_id})


def environment_changed(summary: str) -> Event:
    return Event("EnvironmentChanged", {"summary": summary})


def memory_created(kind: str, event_id: int) -> Event:
    return Event("MemoryCreated", {"memory_kind": kind, "event_id": event_id})


def learning_detected(summary: str) -> Event:
    return Event("LearningDetected", {"summary": summary})


def agent_finished(task_id: str, outcome: str) -> Event:
    if outcome not in {"success", "failure", "unknown"}:
        raise ValueError("outcome must be success, failure, or unknown")
    return Event("AgentFinished", {"task_id": task_id, "outcome": outcome})


Subscriber = Callable[[Event], None]


class EventBus:
    """Synchronous in-memory bus. Subscribers must not raise; failures are collected."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = {}
        self._history: list[Event] = []

    def subscribe(self, kind: str, handler: Subscriber) -> None:
        self._subscribers.setdefault(kind, []).append(handler)

    def publish(self, event: Event) -> list[str]:
        """Publish; returns subscriber error strings (never raises)."""
        self._history.append(event)
        errors: list[str] = []
        for handler in self._subscribers.get(event.kind, []) + self._subscribers.get("*", []):
            try:
                handler(event)
            except Exception as exc:  # never break the agent loop on a listener
                errors.append(str(exc))
        return errors

    def history(self, kind: str | None = None) -> list[Event]:
        if kind is None:
            return list(self._history)
        return [e for e in self._history if e.kind == kind]
