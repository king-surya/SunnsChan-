"""API boundary: schemas + route table. No web framework dependency yet."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatRequest:
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("message must be non-empty")


@dataclass(frozen=True)
class ChatResponse:
    reply: str
    task_id: str


@dataclass(frozen=True)
class TaskRequest:
    title: str
    max_steps: int = 4


@dataclass(frozen=True)
class MemoryQuery:
    query: str
    limit: int = 8


ROUTES = (
    "POST /chat",
    "POST /tasks",
    "GET /tasks",
    "GET /memory",
    "GET /environment",
    "GET /health",
    "WS /stream",
)


def route_table() -> dict[str, str]:
    return {
        "POST /chat": "submit a chat message, returns ChatResponse",
        "POST /tasks": "create a multi-step task",
        "GET /tasks": "list tasks (future persistent store)",
        "GET /memory": "query memory via MemoryQuery",
        "GET /environment": "current EnvState snapshot",
        "GET /health": "liveness probe",
        "WS /stream": "stream responses, task progress, env events (future)",
    }
