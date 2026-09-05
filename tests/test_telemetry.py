import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    EventLedger,
    RawTelemetry,
    ScriptedTelemetrySource,
    TelemetryCollector,
    TelemetryState,
    UnderstandingStore,
    classify_importance,
    mine_telemetry_understanding,
    normalize,
    promote_telemetry,
    record_telemetry_experience,
    summarize_recent_telemetry,
)


def ev(category, event_type, message, severity="info", metadata=None, when=None):
    return RawTelemetry(0, when or datetime.now(UTC), "main-server", category,
                        event_type, message, severity, metadata or {})


class NormalizeTests(unittest.TestCase):
    def test_normalize_and_importance(self) -> None:
        raw = ev("service", "restarted", "nginx restarted", "info",
                 {"subject": "nginx"})
        n = normalize(raw)
        self.assertEqual(n.event_type, "restarted")
        self.assertEqual(n.subject, "nginx")
        self.assertEqual(n.importance, "important")
        self.assertEqual(n.provenance["category"], "service")

    def test_classify_importance_levels(self) -> None:
        self.assertEqual(classify_importance("service", "failed"), "critical")
        self.assertEqual(classify_importance("configuration", "changed"), "important")
        self.assertEqual(classify_importance("service", "started"), "normal")
        self.assertEqual(classify_importance("application", "debug"), "low")
        self.assertEqual(classify_importance("x", "y", severity="critical"), "critical")

    def test_redaction_scrubs_secrets(self) -> None:
        raw = ev("application", "error", "deploy failed token=secret123 password=hunter2",
                 "error", {"api_key": "sk-abc"})
        n = normalize(raw)
        self.assertNotIn("secret123", n.message)
        self.assertNotIn("hunter2", n.message)
        self.assertNotIn("sk-abc", n.metadata.get("api_key", ""))


class CollectorTests(unittest.TestCase):
    def _setup(self, tmp):
        db = Path(tmp) / "m.db"
        return EventLedger(db), TelemetryState(db)

    def test_incremental_read_and_cursor_persistence(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, state = self._setup(tmp)
            source = ScriptedTelemetrySource("main-server", script=[
                [ev("service", "started", "nginx started")],
                [ev("service", "stopped", "nginx stopped")],
            ])
            collector = TelemetryCollector(source, state)
            try:
                r1 = collector.collect(ledger)
                self.assertEqual(r1.new_events, 1)
                self.assertEqual(r1.significant, 1)
                r2 = collector.collect(ledger)
                self.assertEqual(r2.new_events, 1)
                # No new script step: poll again returns no events.
                r3 = collector.collect(ledger)
                self.assertEqual(r3.new_events, 0)
                self.assertEqual(len(ledger.events_of_kind("telemetry")), 2)
            finally:
                ledger.close()
                state.close()

    def test_restart_does_not_replay_history(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            state = TelemetryState(db)
            source = ScriptedTelemetrySource("main-server", script=[
                [ev("service", "started", "nginx started")],
                [ev("service", "stopped", "nginx stopped")],
            ])
            collector = TelemetryCollector(source, state)
            collector.collect(ledger)
            ledger.close()
            state.close()

            # Reopen state with a fresh source that replays the same events.
            ledger2 = EventLedger(db)
            state2 = TelemetryState(db)
            source2 = ScriptedTelemetrySource("main-server", script=[
                [ev("service", "started", "nginx started")],
            ])
            collector2 = TelemetryCollector(source2, state2)
            try:
                r = collector2.collect(ledger2)
                # Cursor already past the replayed offset -> no new events.
                self.assertEqual(r.new_events, 0)
                self.assertEqual(len(ledger2.events_of_kind("telemetry")), 1)
            finally:
                ledger2.close()
                state2.close()

    def test_duplicate_prevention(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, state = self._setup(tmp)
            when = datetime.now(UTC)
            source = ScriptedTelemetrySource("main-server", script=[
                [ev("service", "started", "nginx started", when=when),
                 ev("service", "started", "nginx started", when=when)],  # duplicate
            ])
            collector = TelemetryCollector(source, state)
            try:
                r = collector.collect(ledger)
                self.assertEqual(r.deduplicated, 1)
                self.assertEqual(r.significant, 1)
                self.assertEqual(len(ledger.events_of_kind("telemetry")), 1)
            finally:
                ledger.close()
                state.close()

    def test_relevance_filtering_skips_low(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, state = self._setup(tmp)
            source = ScriptedTelemetrySource("main-server", script=[
                [ev("application", "debug", "routine log line", "info"),
                 ev("service", "failed", "db crashed", "error")],
            ])
            collector = TelemetryCollector(source, state, importance_threshold="normal")
            try:
                r = collector.collect(ledger)
                self.assertEqual(r.significant, 1)  # only the critical event
                self.assertEqual(r.low_skipped, 1)
                telemetry = ledger.events_of_kind("telemetry")
                self.assertEqual(len(telemetry), 1)
                self.assertEqual(telemetry[0].metadata["importance"], "critical")
            finally:
                ledger.close()
                state.close()

    def test_disconnect_and_reconnect(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, state = self._setup(tmp)
            source = ScriptedTelemetrySource("main-server", script=[
                [ev("service", "started", "nginx started")],
            ])
            collector = TelemetryCollector(source, state)
            try:
                source.disconnect()
                r = collector.collect(ledger)
                self.assertEqual(r.status, "disconnected")
                self.assertIn("unavailable", r.error)
                source.reconnect()
                r = collector.collect(ledger)
                self.assertEqual(r.status, "ok")
                self.assertEqual(r.significant, 1)
            finally:
                ledger.close()
                state.close()


class IntegrationTests(unittest.TestCase):
    def test_experience_and_understanding_integration(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            state = TelemetryState(db)
            understanding = UnderstandingStore(db)
            source = ScriptedTelemetrySource("main-server", script=[
                [ev("authentication", "login", "user session started", "info"),
                 ev("configuration", "changed", "nginx config changed", "info",
                    {"subject": "nginx"}),
                 ev("service", "restarted", "nginx restarted", "info", {"subject": "nginx"}),
                 ev("service", "started", "nginx healthy", "info", {"subject": "nginx"})],
            ])
            collector = TelemetryCollector(source, state)
            try:
                r = collector.collect(ledger)
                self.assertGreaterEqual(r.significant, 3)
                # Promote to experience + observation understanding.
                experience_id, touched = promote_telemetry(ledger, understanding)
                self.assertTrue(experience_id)
                self.assertTrue(ledger.events_of_kind("experience"))
                # Understanding records observations (never fact).
                observations = [rec for rec in understanding.visible() if rec.kind == "observation"]
                self.assertTrue(observations)
                self.assertIn("telemetry", observations[0].tags)
                self.assertLessEqual(observations[0].confidence, 0.45)
                # Idempotent: promoting again produces no new experience.
                second_exp, _ = promote_telemetry(ledger, understanding)
                self.assertEqual(second_exp, 0)
                # Summaries are bounded and human-readable.
                lines = summarize_recent_telemetry(ledger)
                self.assertTrue(lines)
                self.assertTrue(any("nginx" in line for line in lines))
            finally:
                ledger.close()
                state.close()
                understanding.close()

    def test_observation_not_fact(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            understanding = UnderstandingStore(db)
            e = ledger.record("telemetry", "[important] main-server service.restarted: nginx restart",
                              {"category": "service", "event_type": "restarted",
                               "importance": "important", "subject": "nginx",
                               "source": "main-server"})
            touched = mine_telemetry_understanding(ledger, understanding)
            self.assertTrue(touched)
            self.assertEqual(touched[0].kind, "observation")
            self.assertNotEqual(touched[0].kind, "fact")
            ledger.close()
            understanding.close()


if __name__ == "__main__":
    unittest.main()
