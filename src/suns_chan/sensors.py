"""Read-only homelab sensors. Observation only — no control capability.

Pipeline: SensorProvider.read() -> SensorReading -> SensorHub.poll() applies
threshold rules with per-rule cooldowns and emits ledger `sensor` events ONLY
for significant crossings. Raw high-frequency telemetry never reaches the
ledger, memory, or prompts. Providers: ScriptedSensorProvider (deterministic
tests), CallableSensorProvider (dependency-injected readings), and
ProxmoxVmSensor (VM running/stopped via an existing SandboxProvider —
credentials stay inside that provider, only 0/1 states come out).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from .memory import EventLedger


@dataclass(frozen=True)
class SensorReading:
    source: str   # e.g. "homelab", "proxmox"
    metric: str   # e.g. "vm.web.status", "cpu.util"
    value: float
    unit: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.metric.strip():
            raise ValueError("sensor source and metric must be non-empty")


class SensorProvider(Protocol):
    name: str

    def read(self) -> list[SensorReading]:
        """Return current readings. Must not touch the host beyond reading."""
        ...


@dataclass
class ScriptedSensorProvider:
    """Deterministic readings for tests/dev. Each read() advances the script."""

    name: str = "scripted"
    script: list[list[SensorReading]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._cursor = 0

    def read(self) -> list[SensorReading]:
        if not self.script:
            return []
        readings = self.script[min(self._cursor, len(self.script) - 1)]
        self._cursor += 1
        return list(readings)


@dataclass
class CallableSensorProvider:
    """Wraps an injected callable. The callable owns all backend specifics."""

    name: str
    reader: Callable[[], list[SensorReading]] = field(default=lambda: [])

    def read(self) -> list[SensorReading]:
        return list(self.reader())


@dataclass(frozen=True)
class ThresholdRule:
    source: str
    metric: str
    above: float | None = None
    below: float | None = None
    cooldown_secs: float = 300.0
    message: str = ""
    severity: str = "info"

    def __post_init__(self) -> None:
        if self.above is None and self.below is None:
            raise ValueError("a rule needs an above and/or below threshold")
        if self.cooldown_secs < 0:
            raise ValueError("cooldown must be >= 0")


@dataclass(frozen=True)
class WakeDecision:
    reason: str | None
    event_ids: tuple[int, ...] = ()


class SensorHub:
    """Polls providers, applies rules with cooldowns, records significant events."""

    def __init__(self, providers: list[SensorProvider] | None = None,
                 rules: list[ThresholdRule] | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        self._providers = list(providers or [])
        self._rules = list(rules or [])
        self._clock = clock or time.monotonic
        self._last_fired: dict[tuple[str, str, str], float] = {}
        self._last_values: dict[tuple[str, str], float] = {}

    @property
    def sources(self) -> list[str]:
        return [p.name for p in self._providers]

    def last_values(self) -> dict[tuple[str, str], float]:
        return dict(self._last_values)

    def poll(self, ledger: EventLedger) -> list[int]:
        """One poll cycle. Returns ids of newly recorded significant events."""
        now = self._clock()
        created: list[int] = []
        for provider in self._providers:
            try:
                readings = provider.read()
            except Exception as exc:
                event = ledger.record("sensor", f"Sensor {provider.name} read failed: {exc}",
                                      {"source": provider.name, "metric": "_error", "severity": "warning"})
                created.append(event.id)
                continue
            for reading in readings:
                self._last_values[(reading.source, reading.metric)] = reading.value
                for rule in self._rules:
                    if (rule.source, rule.metric) != (reading.source, reading.metric):
                        continue
                    crossed = (rule.above is not None and reading.value > rule.above) or \
                              (rule.below is not None and reading.value < rule.below)
                    if not crossed:
                        continue
                    key = (rule.source, rule.metric, rule.message or "crossing")
                    if now - self._last_fired.get(key, float("-inf")) < rule.cooldown_secs:
                        continue
                    self._last_fired[key] = now
                    text = rule.message or (
                        f"{reading.source}/{reading.metric} = {reading.value}{reading.unit}")
                    event = ledger.record("sensor", text, {
                        "source": reading.source, "metric": reading.metric,
                        "value": reading.value, "unit": reading.unit,
                        "severity": rule.severity,
                    })
                    created.append(event.id)
        return created

    def evaluate_wake(self, ledger: EventLedger, *, watch_severities: tuple[str, ...] = ("warning", "critical"),
                      cooldown_secs: float = 600.0, limit: int = 20) -> WakeDecision:
        """Decide whether recent sensor events justify an autonomous wake.

        Only watched severities, newest-first, and at most one wake per
        cooldown window (tracked via prior wake events in the ledger).
        """
        recent = ledger.events_of_kind("sensor", limit=limit)
        candidates = [e for e in recent if e.metadata.get("severity") in watch_severities]
        if not candidates:
            return WakeDecision(None)
        wakes = [e for e in ledger.events_of_kind("wake", limit=limit)
                 if e.metadata.get("trigger") == "sensor"]
        if wakes:
            last_wake_at = wakes[0].occurred_at
            newest = candidates[0].occurred_at
            if (newest - last_wake_at).total_seconds() < cooldown_secs:
                return WakeDecision(None)
        newest = candidates[0]
        return WakeDecision(f"environment_change: {newest.text[:160]}",
                            tuple(e.id for e in candidates[:5]))


def proxmox_vm_readings(provider: Any, sandbox_ids: list[str]) -> list[SensorReading]:
    """Read VM liveness (1 running / 0 otherwise) through an existing provider.

    Only status strings cross the boundary — never credentials, never guest data.
    """
    readings: list[SensorReading] = []
    for sandbox_id in sandbox_ids:
        try:
            running = 1.0 if provider.status(sandbox_id) == "running" else 0.0
        except (ValueError, RuntimeError):
            running = 0.0
        readings.append(SensorReading("proxmox", f"vm.{sandbox_id}.running", running))
    return readings
