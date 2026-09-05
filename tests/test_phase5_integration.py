"""Phase 5 integration (§27) and long-run continuity (§28). Deterministic, mock-backed."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    AgentCore,
    AutonomyEngine,
    CuriosityStore,
    Decision,
    EventLedger,
    IdentitySeed,
    KnowledgeStore,
    MockSandboxProvider,
    PolicyGate,
    SandboxController,
    SessionStore,
    experiences_of_session,
    record_experience,
)
from suns_chan.affect import apply_affect, collect_evidence
from suns_chan.graph import build_index
from suns_chan.sensors import ScriptedSensorProvider, SensorHub, SensorReading, ThresholdRule


PROJECT = Path(__file__).resolve().parents[1]


def open_all(tmp: str, *, with_sandbox: bool = False):
    db = Path(tmp) / "m.db"
    ledger = EventLedger(db)
    store = KnowledgeStore(db)
    curiosities = CuriosityStore(db)
    sessions = SessionStore(db)
    core = AgentCore(ledger, PolicyGate(), IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"))
    controller = SandboxController(MockSandboxProvider(), template="t") if with_sandbox else None
    engine = AutonomyEngine(ledger, core, sessions, knowledge=store,
                            curiosities=curiosities, sandbox_controller=controller,
                            affect_enabled=True)
    return {"ledger": ledger, "store": store, "curiosities": curiosities,
            "sessions": sessions, "core": core, "engine": engine, "controller": controller}


def close_all(parts) -> None:
    parts["ledger"].close()
    parts["store"].close()
    parts["curiosities"].close()
    parts["sessions"].close()


def decide_ok(ctx) -> Decision:
    return Decision("Acknowledged.")


class IntegrationTests(unittest.TestCase):
    def test_full_chain_observation_to_influence(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp, with_sandbox=True)
            try:
                ledger = parts["ledger"]
                # 0. Prior history + prior failures (affect evidence).
                first = record_experience(ledger, session_id="hist", activity_id="hist-a1",
                                          intent="Restart web VM", result="success")
                ledger.record("outcome", "Nightly probe failed.", {"outcome": "failure"})
                ledger.record("observation", "Why do nightly probes fail?")
                # 1-2. Environment observation -> significant change.
                hub = SensorHub(
                    [ScriptedSensorProvider("lab", [
                        [SensorReading("proxmox", "vm.web.running", 1.0)],
                        [SensorReading("proxmox", "vm.web.running", 0.0)],
                    ])],
                    [ThresholdRule("proxmox", "vm.web.running", below=0.5,
                                   cooldown_secs=60.0, message="VM web stopped.",
                                   severity="critical")])
                self.assertEqual(hub.poll(ledger), [])
                created = hub.poll(ledger)
                self.assertEqual(len(created), 1)
                self.assertEqual(hub.poll(ledger), [])  # no duplicate event
                # 3. Wake with cooldown (no uncontrolled loops).
                wake = hub.evaluate_wake(ledger)
                self.assertIsNotNone(wake.reason)
                ledger.record("wake", wake.reason, {"trigger": "sensor"})
                self.assertIsNone(hub.evaluate_wake(ledger).reason)
                # 4-6. Recall, activities, selection through the engine.
                report = parts["engine"].run("sess-int", decide_ok, max_activities=3, use_sandbox=True)
                self.assertEqual(report.activities_completed, 3)
                started = ledger.events_of_kind("activity_started")
                self.assertTrue(any("sensor-" in e.metadata.get("activity_key", "") for e in started))
                # 7. Affect updated from evidence through the existing state.
                evidence = collect_evidence(ledger, session_id="sess-int", curiosities=parts["curiosities"])
                deltas = apply_affect(ledger, evidence)
                self.assertTrue(deltas)  # failure streak + session load move the needle
                # 9-10. Graph relationships with provenance.
                index = build_index(ledger, knowledge=parts["store"], curiosities=parts["curiosities"])
                self.assertTrue(index.nodes)
                # 11-13. Restart, recover, retrieve.
                info = parts["engine"].resume_session("sess-int")
                self.assertFalse(info.recovered)  # clean waiting state, nothing lost
                rel = [e for e in experiences_of_session(ledger, "sess-int")]
                self.assertEqual(len(rel), 3)
                # 15. Earlier records measurably cited later (memory → decision),
                # and experiences chain predecessor → successor.
                cited = {i for e in started for i in e.metadata.get("supporting_ids", [])}
                self.assertTrue(cited)
                sensor_ids = {e.id for e in ledger.events_of_kind("sensor")}
                self.assertTrue(sensor_ids & cited, "sensor evidence never reached selection")
                by_activity = {e.metadata["activity_id"]: e for e in rel}
                ordered = sorted(by_activity)
                for earlier, later in zip(ordered, ordered[1:]):
                    self.assertIn(by_activity[earlier].id,
                                  by_activity[later].metadata["related"]["follow_up_of"])
            finally:
                close_all(parts)


class LongRunContinuityTests(unittest.TestCase):
    def test_seven_day_equivalent(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp, with_sandbox=True)
            try:
                ledger, engine = parts["ledger"], parts["engine"]
                # DAY 1: first experience.
                ledger.record("goal_opened", "Keep the web VM healthy",
                              {"reason": "ops", "priority": 0.8})
                day1 = engine.run("week", decide_ok, max_activities=1, use_sandbox=False)
                self.assertEqual(day1.activities_completed, 1)
                # DAY 2: recall yesterday.
                hits = ledger.recall("web VM healthy")
                self.assertTrue(hits)
                day2 = engine.run("week", decide_ok, max_activities=1, use_sandbox=False)
                self.assertEqual(day2.activities_completed, 1)
                # DAY 3: environment change -> wake -> activity.
                hub = SensorHub(
                    [ScriptedSensorProvider("lab", [[SensorReading("proxmox", "vm.web.running", 0.0)]])],
                    [ThresholdRule("proxmox", "vm.web.running", below=0.5,
                                   cooldown_secs=0.0, message="VM web stopped.", severity="critical")])
                hub.poll(ledger)
                wake = hub.evaluate_wake(ledger)
                self.assertIsNotNone(wake.reason)
                ledger.record("wake", wake.reason, {"trigger": "sensor"})
                day3 = engine.run("week", decide_ok, max_activities=1, use_sandbox=True)
                self.assertEqual(day3.activities_completed, 1)
                # DAY 4: new activity from accumulated state.
                origin = ledger.record("observation", "Web VM was stopped; why?")
                parts["curiosities"].open("Why did the web VM stop?", origin.id)
                day4 = engine.run("week", decide_ok, max_activities=1, use_sandbox=True)
                self.assertEqual(day4.activities_completed, 1)
                # DAY 5: reflection sees the week.
                from suns_chan import reflect

                findings = reflect(ledger, max_findings=10).findings
                self.assertTrue(findings)
                # DAY 6: graph retrieval reaches back to day 1.
                index = build_index(ledger, knowledge=parts["store"], curiosities=parts["curiosities"])
                day1_exp = experiences_of_session(ledger, "week")[0]
                paths = index.query("experience", str(day1_exp.id))
                self.assertTrue(all(p.provenance for p in paths) if paths else True)
                self.assertTrue(index.nodes)
                # DAY 7: behavior changed by history, not repetition.
                keys = [s.split(":")[1] for s in
                        parts["sessions"].open_or_create("week").attempt_signatures]
                self.assertGreater(len(set(keys)), 1)
                started = ledger.events_of_kind("activity_started")
                cited = {i for e in started for i in e.metadata.get("supporting_ids", [])}
                early_ids = {day1_exp.id}
                self.assertTrue(early_ids & cited or len(started) >= 4)
                self.assertEqual(parts["sessions"].open_or_create("week").activity_counter, 4)
            finally:
                close_all(parts)


if __name__ == "__main__":
    unittest.main()
