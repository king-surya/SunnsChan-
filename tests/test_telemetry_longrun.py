"""Long-run telemetry simulation: duplicates, noise, disconnects, restarts —
no duplicate experiences, no unbounded growth, no retry loops."""
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    EventLedger,
    RawTelemetry,
    ScriptedTelemetrySource,
    TelemetryCollector,
    TelemetryState,
    UnderstandingStore,
    promote_telemetry,
)


def ev(category, event_type, message, severity="info", when=None):
    return RawTelemetry(0, when or datetime.now(UTC), "main-server", category,
                        event_type, message, severity)


class TelemetryLongRunTests(unittest.TestCase):
    def test_many_events_stay_bounded(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            state = TelemetryState(db)
            understanding = UnderstandingStore(db)
            # 60 events: noise (low), important, critical, and duplicates.
            batches = []
            for i in range(20):
                batch = [
                    ev("application", "debug", f"noise line {i}"),  # low
                    ev("service", "started", f"svc {i} started"),   # normal
                    ev("configuration", "changed", f"config {i} changed"),  # important
                ]
                if i % 5 == 0:
                    when = datetime.now(UTC)
                    batch.append(ev("service", "failed", "db crashed", "error", when=when))
                    batch.append(ev("service", "failed", "db crashed", "error", when=when))  # dup
                batches.append(batch)
            source = ScriptedTelemetrySource("main-server", script=batches)
            collector = TelemetryCollector(source, state, importance_threshold="normal")
            try:
                for _ in range(len(batches)):
                    collector.collect(ledger)
                telemetry = ledger.events_of_kind("telemetry", limit=10000)
                # 4 distinct crash times (i=0,5,10,15); in-batch duplicates deduped.
                crashes = [e for e in telemetry if e.metadata.get("event_type") == "failed"]
                self.assertEqual(len(crashes), 4)
                # Low-noise events never reached cognition.
                self.assertFalse(any("noise line" in e.text for e in telemetry))
                # Experience promotion is idempotent and bounded.
                exp1, _ = promote_telemetry(ledger, understanding)
                self.assertTrue(exp1)
                experiences = ledger.events_of_kind("experience")
                exp_count = len(experiences)
                exp2, _ = promote_telemetry(ledger, understanding)
                self.assertEqual(exp2, 0)
                self.assertEqual(len(ledger.events_of_kind("experience")), exp_count)
            finally:
                ledger.close()
                state.close()
                understanding.close()

    def test_disconnect_reconnect_no_loop_and_survive_restart(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            state = TelemetryState(db)
            source = ScriptedTelemetrySource("main-server", script=[
                [ev("service", "started", "nginx started")],
                [ev("service", "stopped", "nginx stopped")],
            ])
            collector = TelemetryCollector(source, state)
            try:
                source.disconnect()
                for _ in range(3):  # repeated polls while down must not loop/error
                    report = collector.collect(ledger)
                    self.assertEqual(report.status, "disconnected")
                source.reconnect()
                report = collector.collect(ledger)
                self.assertEqual(report.status, "ok")
                self.assertEqual(report.significant, 1)
            finally:
                ledger.close()
                state.close()

            # Restart: cursor survives; only remaining script step is read.
            ledger2 = EventLedger(db)
            state2 = TelemetryState(db)
            source2 = ScriptedTelemetrySource("main-server", script=[
                [ev("service", "started", "nginx started")],
                [ev("service", "stopped", "nginx stopped")],
            ])
            collector2 = TelemetryCollector(source2, state2)
            try:
                r1 = collector2.collect(ledger2)
                self.assertEqual(r1.new_events, 0)  # already consumed first batch
                r2 = collector2.collect(ledger2)
                self.assertEqual(r2.new_events, 1)  # only the remaining batch
                self.assertEqual(len(ledger2.events_of_kind("telemetry")), 2)
            finally:
                ledger2.close()
                state2.close()


if __name__ == "__main__":
    unittest.main()
