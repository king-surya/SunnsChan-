from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import EventLedger
from suns_chan.sensors import (
    CallableSensorProvider,
    ScriptedSensorProvider,
    SensorHub,
    SensorReading,
    ThresholdRule,
    proxmox_vm_readings,
)


def open_ledger(tmp: str) -> EventLedger:
    return EventLedger(Path(tmp) / "m.db")


def reading(value: float, metric: str = "cpu.util", source: str = "homelab") -> SensorReading:
    return SensorReading(source, metric, value, "%")


class SensorTests(unittest.TestCase):
    def test_crossing_with_cooldown_and_dedup(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                provider = ScriptedSensorProvider("lab", [
                    [reading(31.0)], [reading(95.0)], [reading(96.0)], [reading(40.0)],
                ])
                ticks = [0.0]
                hub = SensorHub([provider], [ThresholdRule(
                    "homelab", "cpu.util", above=90.0, cooldown_secs=60.0,
                    message="CPU crossed 90%", severity="warning")],
                    clock=lambda: ticks[0])
                self.assertEqual(hub.poll(ledger), [])          # 31: quiet
                first = hub.poll(ledger)                        # 95: fires
                self.assertEqual(len(first), 1)
                self.assertEqual(hub.poll(ledger), [])          # 96: cooldown
                ticks[0] += 61.0
                self.assertEqual(hub.poll(ledger), [])          # 40: back to normal
                event = ledger.events_of_kind("sensor")[0]
                self.assertEqual(event.metadata["severity"], "warning")
                self.assertEqual(event.metadata["value"], 95.0)
            finally:
                ledger.close()

    def test_failed_provider_is_audited_not_fatal(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                def broken():
                    raise RuntimeError("nope")

                hub = SensorHub([CallableSensorProvider("bad", broken)], [])
                created = hub.poll(ledger)
                self.assertEqual(len(created), 1)
                self.assertIn("read failed", ledger.events_of_kind("sensor")[0].text)
            finally:
                ledger.close()

    def test_wake_decision_with_cooldown(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                ledger.record("sensor", "VM web stopped.", {"severity": "critical"})
                hub = SensorHub([], [])
                first = hub.evaluate_wake(ledger)
                self.assertIsNotNone(first.reason)
                self.assertIn("environment_change", first.reason)
                ledger.record("wake", "checking vm", {"trigger": "sensor"})
                self.assertIsNone(hub.evaluate_wake(ledger).reason)  # cooldown
                ledger.record("sensor", "Just info.", {"severity": "info"})
                info_hub = SensorHub([], [])
                # info-only newest still wakes on the older critical? newest is info:
                decision = info_hub.evaluate_wake(ledger)
                self.assertIsNone(decision.reason)  # newest not watched + wake cooldown
            finally:
                ledger.close()

    def test_proxmox_readings_are_status_only(self) -> None:
        from suns_chan import MockSandboxProvider, VMSpec

        provider = MockSandboxProvider()
        sandbox_id = provider.create(VMSpec("lab", "t"))
        readings = proxmox_vm_readings(provider, [sandbox_id, "ghost"])
        self.assertEqual([(r.metric, r.value) for r in readings],
                         [(f"vm.{sandbox_id}.running", 0.0), ("vm.ghost.running", 0.0)])
        provider.start(sandbox_id)
        readings = proxmox_vm_readings(provider, [sandbox_id])
        self.assertEqual(readings[0].value, 1.0)
        blob = str(readings)
        self.assertNotIn("token", blob.lower())

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            SensorReading(" ", "m", 1.0)
        with self.assertRaises(ValueError):
            ThresholdRule("s", "m")


if __name__ == "__main__":
    unittest.main()
