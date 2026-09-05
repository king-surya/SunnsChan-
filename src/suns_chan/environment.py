"""Environment abstraction: Current State + Observations + Events. Read-only.

The environment never reasons and never writes to memory directly;
the agent runtime copies observations into the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class EnvState:
    hosts: tuple[str, ...] = ()
    services: dict[str, str] = field(default_factory=dict)
    sensors: dict[str, str] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class EnvObservation:
    summary: str
    details: dict[str, str] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class EnvEvent:
    kind: str  # e.g. ServiceDown, SensorReading
    summary: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class EnvProvider(Protocol):
    name: str

    def snapshot(self) -> EnvState:
        ...

    def observe(self) -> EnvObservation:
        ...

    def poll_events(self) -> list[EnvEvent]:
        ...


@dataclass
class FakeEnvironment:
    """Deterministic stand-in for tests/dev. No real host access."""

    name: str = "fake"
    _state: EnvState = field(default_factory=lambda: EnvState(hosts=(), services={}, sensors={}))

    def snapshot(self) -> EnvState:
        return self._state

    def observe(self) -> EnvObservation:
        return EnvObservation(summary="fake environment nominal", details={})

    def poll_events(self) -> list[EnvEvent]:
        return []
