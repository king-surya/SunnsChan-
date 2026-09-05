"""End-to-end main-server observability: fixture server -> telemetry -> normalize
-> relevance -> experience -> understanding -> query, with a real file source."""
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    EventLedger,
    FileTelemetrySource,
    RawTelemetry,
    ScriptedTelemetrySource,
    TelemetryCollector,
    TelemetryState,
    UnderstandingStore,
    promote_telemetry,
    summarize_recent_telemetry,
    telemetry_section,
)


def ev(category, event_type, message, severity="info", metadata=None):
    return RawTelemetry(0, datetime.now(UTC), "main-server", category,
                        event_type, message, severity, metadata or {})


class TelemetryE2ETests(unittest.TestCase):
    def test_activity_sequence_to_context_and_understanding(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            state = TelemetryState(db)
            understanding = UnderstandingStore(db)
            source = ScriptedTelemetrySource("main-server", script=[
                [ev("authentication", "login", "user session started")],
                [ev("configuration", "changed", "nginx config changed", metadata={"subject": "nginx"})],
                [ev("service", "restarted", "nginx restarted", metadata={"subject": "nginx"})],
                [ev("service", "started", "nginx healthy", metadata={"subject": "nginx"})],
            ])
            collector = TelemetryCollector(source, state)
            try:
                for _ in range(4):
                    collector.collect(ledger)
                telemetry = ledger.events_of_kind("telemetry")
                self.assertEqual(len(telemetry), 4)
                # Promote -> experience + observation understanding.
                exp_id, touched = promote_telemetry(ledger, understanding)
                self.assertTrue(exp_id)
                self.assertTrue(touched)
                # Bounded context section (never raw-log dump).
                section = telemetry_section(summarize_recent_telemetry(ledger))
                self.assertIn("Recent server activity:", section)
                self.assertIn("nginx", section)
                self.assertNotIn("user session started", section.split("configuration")[0][-40:])
                # Suns Chan can now answer "what happened" from understanding.
                observations = [r.statement for r in understanding.visible()
                                if r.kind == "observation"]
                self.assertTrue(any("nginx" in s for s in observations))
                # Temporal ordering preserved (login happened before restart).
                login = next(e for e in telemetry if e.metadata["event_type"] == "login")
                restart = next(e for e in telemetry if e.metadata["event_type"] == "restarted")
                self.assertLess(login.id, restart.id)
            finally:
                ledger.close()
                state.close()
                understanding.close()

    def test_file_source_incremental_and_restart(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            log = Path(tmp) / "server.log"
            log.write_text(json.dumps({
                "timestamp": "2026-01-01T00:00:00+00:00",
                "category": "service", "event_type": "started",
                "message": "nginx started", "severity": "info",
            }) + "\n", encoding="utf-8")

            ledger = EventLedger(db)
            state = TelemetryState(db)
            source = FileTelemetrySource("main-server", path=log)
            collector = TelemetryCollector(source, state)
            try:
                r1 = collector.collect(ledger)
                self.assertEqual(r1.significant, 1)
                # Append a second line; only the new line is read.
                with log.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "timestamp": "2026-01-01T00:01:00+00:00",
                        "category": "service", "event_type": "failed",
                        "message": "db crashed password=secret", "severity": "error",
                    }) + "\n")
                r2 = collector.collect(ledger)
                self.assertEqual(r2.new_events, 1)
                events = ledger.events_of_kind("telemetry")
                self.assertEqual(len(events), 2)
                self.assertEqual(events[0].metadata["importance"], "critical")
                # Secrets redacted even in the file path.
                self.assertNotIn("secret", events[0].text)
            finally:
                ledger.close()
                state.close()

            # Restart: cursor persists, no replay.
            ledger2 = EventLedger(db)
            state2 = TelemetryState(db)
            source2 = FileTelemetrySource("main-server", path=log)
            collector2 = TelemetryCollector(source2, state2)
            try:
                r = collector2.collect(ledger2)
                self.assertEqual(r.new_events, 0)
                self.assertEqual(len(ledger2.events_of_kind("telemetry")), 2)
            finally:
                ledger2.close()
                state2.close()


if __name__ == "__main__":
    unittest.main()
