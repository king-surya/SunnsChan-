"""AutonomyEngine integration: self-direction, memory influence, persistence,
recovery, sandbox e2e, stuck handling, pause/resume — all on mock backends."""

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
    translate_probe_to_shell,
)


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
                            curiosities=curiosities, sandbox_controller=controller)
    parts = {"ledger": ledger, "store": store, "curiosities": curiosities,
             "sessions": sessions, "core": core, "engine": engine,
             "controller": controller}
    return parts


def close_all(parts) -> None:
    parts["ledger"].close()
    parts["store"].close()
    parts["curiosities"].close()
    parts["sessions"].close()


def decide_ok(ctx) -> Decision:
    return Decision("Acknowledged; will do.")


class SelfDirectedTests(unittest.TestCase):
    def test_curiosity_generates_activity_and_experience(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                origin = parts["ledger"].record("observation", "Disk fills every Sunday.")
                parts["curiosities"].open("Why does disk fill on Sundays?", origin.id, importance=0.8)
                report = parts["engine"].run("sess-1", decide_ok, max_activities=1, use_sandbox=False)
                self.assertEqual(report.activities_completed, 1)
                experiences = experiences_of_session(parts["ledger"], "sess-1")
                self.assertEqual(len(experiences), 1)
                self.assertIn("disk fill", (experiences[0].metadata["intent"]
                              + str(experiences[0].metadata["motivation"])).lower())
                view = parts["engine"].describe("sess-1")
                self.assertTrue(view["last_selection"]["reason"])
                self.assertIn("curiosity", view["last_selection"]["reason"].lower())
            finally:
                close_all(parts)

    def test_failed_activity_preserved_with_reflection(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                parts["ledger"].record("goal_opened", "Probe the scheduler", {"reason": "ops", "priority": 0.6})

                def bad_decide(ctx) -> Decision:
                    raise ValueError("model blew up")

                report = parts["engine"].run("sess-f", bad_decide, max_activities=1, use_sandbox=False)
                self.assertEqual(report.activities_completed, 1)
                experiences = experiences_of_session(parts["ledger"], "sess-f")
                self.assertEqual(experiences[0].metadata["result"], "failure")
                self.assertTrue(experiences[0].metadata["lessons"])
            finally:
                close_all(parts)

    def test_memory_influences_selection(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                failure = parts["ledger"].record(
                    "outcome", "Deploy failed: checklist skipped.", {"outcome": "failure"})
                report = parts["engine"].run("sess-m", decide_ok, max_activities=1, use_sandbox=False)
                self.assertEqual(report.steps[0].category, "REVISIT")
                started = parts["ledger"].events_of_kind("activity_started")
                self.assertIn(failure.id, started[0].metadata["supporting_ids"])
            finally:
                close_all(parts)

    def test_unexpected_result_creates_curiosity(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                parts["ledger"].record("goal_opened", "Watch latency", {"reason": "ops", "priority": 0.5})
                parts["ledger"].record("observation", "Why did latency spike at midnight?")
                parts["engine"].run("sess-c", decide_ok, max_activities=1, use_sandbox=False)
                open_items = parts["curiosities"].open_curiosities()
                self.assertTrue(open_items)
                self.assertTrue(any("latency" in c.question.lower() for c in open_items))
            finally:
                close_all(parts)


class RobustnessTests(unittest.TestCase):
    def test_stuck_triggers_pause_with_strategy_change(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp, with_sandbox=True)
            try:
                # A goal stays open across activities, so the same candidate
                # recurs until the stuck guard fires (curiosities resolve).
                parts["ledger"].record(
                    "goal_opened", "Keep disk healthy", {"reason": "ops", "priority": 0.7})
                report = parts["engine"].run("sess-s", decide_ok, max_activities=5, use_sandbox=False)
                session = parts["sessions"].open_or_create("sess-s")
                self.assertEqual(session.status, "paused")
                self.assertEqual(len(session.avoid_keys), 1)
                stuck_events = [e for e in parts["ledger"].events_of_kind("activity_finished")
                                if e.metadata.get("stuck")]
                self.assertTrue(stuck_events)
                self.assertEqual(report.status, "paused")
            finally:
                close_all(parts)

    def test_pause_resume_and_stop(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                engine = parts["engine"]
                origin = parts["ledger"].record("observation", "Seed.")
                parts["curiosities"].open("What is this seed?", origin.id)
                engine.run("sess-p", decide_ok, max_activities=1, use_sandbox=False)
                engine.pause("sess-p", "operator break")
                self.assertEqual(parts["sessions"].open_or_create("sess-p").status, "paused")
                second = engine.run("sess-p", decide_ok, max_activities=2, use_sandbox=False)
                self.assertEqual(second.activities_completed, 0)  # stays paused
                info = engine.resume_session("sess-p")
                self.assertFalse(info.recovered)
                idle = engine.run("sess-p", decide_ok, max_activities=1, use_sandbox=False)
                self.assertEqual(idle.activities_completed, 0)  # no open work: waits, never spins
                self.assertEqual(idle.status, "waiting")
                origin2 = parts["ledger"].record("observation", "Second seed.")
                parts["curiosities"].open("What grows second?", origin2.id)
                third = engine.run("sess-p", decide_ok, max_activities=1, use_sandbox=False)
                self.assertEqual(third.activities_completed, 1)
                engine.stop("sess-p", "done for today")
                self.assertEqual(parts["sessions"].open_or_create("sess-p").status, "aborted")
            finally:
                close_all(parts)

    def test_crash_recovery_replans_without_reexecution(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                engine = parts["engine"]
                session = engine.start_session("sess-crash")
                session.status = "executing"
                session.current_activity = {"key": "exp-1", "title": "Probe disks"}
                parts["sessions"].save(session)
                engine2 = AutonomyEngine(parts["ledger"], parts["core"], parts["sessions"])
                info = engine2.resume_session("sess-crash")
                self.assertTrue(info.recovered)
                self.assertEqual(info.previous_status, "executing")
                recovered = parts["sessions"].open_or_create("sess-crash")
                self.assertEqual(recovered.status, "idle")
                self.assertEqual(recovered.current_activity, {})
                interruptions = [e for e in experiences_of_session(parts["ledger"], "sess-crash")]
                self.assertTrue(interruptions)
            finally:
                close_all(parts)

    def test_restart_preserves_experience(self) -> None:
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "m.db")
            parts = open_all(tmp)
            try:
                origin = parts["ledger"].record("observation", "Seed memory.")
                parts["curiosities"].open("What grows from seed?", origin.id)
                parts["engine"].run("sess-r", decide_ok, max_activities=1, use_sandbox=False)
                counter = parts["sessions"].open_or_create("sess-r").activity_counter
                self.assertEqual(counter, 1)
            finally:
                close_all(parts)
            # Full restart: new objects over the same file.
            parts2 = open_all(tmp)
            try:
                experiences = experiences_of_session(parts2["ledger"], "sess-r")
                self.assertEqual(len(experiences), 1)
                session = parts2["sessions"].open_or_create("sess-r")
                self.assertEqual(session.activity_counter, 1)
                origin2 = parts2["ledger"].record("observation", "Second seed.")
                parts2["curiosities"].open("What grows second?", origin2.id)
                parts2["engine"].run("sess-r", decide_ok, max_activities=1, use_sandbox=False)
                self.assertEqual(parts2["sessions"].open_or_create("sess-r").activity_counter, 2)
            finally:
                close_all(parts2)


class SandboxAutonomyTests(unittest.TestCase):
    def test_autonomous_sandbox_experiment_end_to_end(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp, with_sandbox=True)
            try:
                origin = parts["ledger"].record("observation", "Need a lab note.")
                parts["curiosities"].open("Can the sandbox keep a note?", origin.id)
                report = parts["engine"].run("sess-vm", decide_ok, max_activities=1, use_sandbox=True)
                self.assertEqual(report.activities_completed, 1)
                kinds = {e.kind for e in parts["ledger"].recall("sandbox")}
                self.assertIn("experiment", kinds)
                # VM disposable, experience persistent.
                self.assertEqual(parts["controller"]._provider._vms, {})
                self.assertTrue(experiences_of_session(parts["ledger"], "sess-vm"))
            finally:
                close_all(parts)

    def test_guest_freedom_and_host_boundary(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp, with_sandbox=True)
            try:
                before = set(Path(PROJECT).iterdir())
                origin = parts["ledger"].record("observation", "Freedom check.")
                curiosity = parts["curiosities"].open("What can the guest do?", origin.id)
                from suns_chan import ActivityCandidate

                engine = parts["engine"]
                # Drive one step with an explicit freedom-probing candidate set.
                import suns_chan.activities as activities_mod

                real_generate = activities_mod.generate_activities
                probe = ActivityCandidate(
                    key="probe-freedom", category="EXPERIMENT", title="Probe guest freedom",
                    reason="verify no artificial blocks", source="CURIOSITY",
                    inspiration_ids=(origin.id,), curiosity_id=curiosity.id,
                    sandbox_operations=["apt install gcc", "rm -rf /srv", "systemctl restart x"],
                )
                activities_mod.generate_activities = lambda *a, **k: [probe]
                try:
                    report = engine.run("sess-free", decide_ok, max_activities=1, use_sandbox=True)
                finally:
                    activities_mod.generate_activities = real_generate
                self.assertEqual(report.activities_completed, 1)
                self.assertEqual(set(Path(PROJECT).iterdir()), before)
            finally:
                close_all(parts)


class RepairVerificationTests(unittest.TestCase):
    def test_curiosity_advances_through_lifecycle(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp, with_sandbox=True)
            try:
                origin = parts["ledger"].record("observation", "Disk fills every Sunday.")
                item = parts["curiosities"].open("Why does disk fill on Sundays?", origin.id)
                parts["engine"].run("sess-cur", decide_ok, max_activities=1, use_sandbox=True)
                self.assertEqual(parts["curiosities"].get(item.id).status, "answered")
            finally:
                close_all(parts)

    def test_sandbox_probe_collects_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp, with_sandbox=True)
            try:
                origin = parts["ledger"].record("observation", "Need a probe.")
                parts["curiosities"].open("Can the sandbox keep a note?", origin.id)
                parts["engine"].run("sess-probe", decide_ok, max_activities=1, use_sandbox=True)
                experiences = experiences_of_session(parts["ledger"], "sess-probe")
                self.assertTrue(experiences[0].metadata["artifacts"])
                self.assertTrue(experiences[0].metadata["artifacts"][0].startswith("/out/probe-"))
            finally:
                close_all(parts)

    def test_stuck_goal_evolves_into_refined_followup(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                parts["ledger"].record(
                    "goal_opened", "Keep disk healthy", {"reason": "ops", "priority": 0.7})
                goal_id = parts["ledger"].events_of_kind("goal_opened")[0].id
                parts["engine"].run("sess-goal", decide_ok, max_activities=5, use_sandbox=False)
                closed = {e.metadata.get("goal_id") for e in parts["ledger"].events_of_kind("goal_closed")}
                self.assertIn(goal_id, closed)
                refined = [e for e in parts["ledger"].events_of_kind("goal_opened")
                           if e.metadata.get("refines_goal_id") == goal_id]
                self.assertEqual(len(refined), 1)
            finally:
                close_all(parts)

    def test_probe_translation_to_shell(self) -> None:
        shell = translate_probe_to_shell("write /out/note.txt it's fine")
        self.assertIn("mkdir -p /out", shell)
        self.assertIn("cat /out/note.txt", shell)
        self.assertIn("it'\"'\"'s fine", shell)
        self.assertEqual(translate_probe_to_shell("echo hi"), "echo hi")
        self.assertEqual(translate_probe_to_shell("apt install gcc"), "apt install gcc")
        with self.assertRaises(ValueError):
            translate_probe_to_shell("write ")


class LongRunSimulationTests(unittest.TestCase):
    def test_multi_cycle_memory_shapes_later_activities(self) -> None:
        """§30: Activity → Experience → Reflection → Memory → New Activity,
        with later selections provably referencing earlier evidence."""
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp, with_sandbox=True)
            try:
                failure = parts["ledger"].record(
                    "outcome", "Deploy failed: checklist skipped.", {"outcome": "failure"})
                parts["ledger"].record(
                    "goal_opened", "Keep deploys healthy", {"reason": "ops", "priority": 0.7})
                origin = parts["ledger"].record("observation", "Staging behaves oddly.")
                parts["curiosities"].open("Why does staging behave oddly?", origin.id)
                engine = parts["engine"]

                def bad_decide(ctx) -> Decision:
                    raise ValueError("simulated model failure")

                first = engine.run("sess-long", bad_decide, max_activities=1, use_sandbox=False)
                second = engine.run("sess-long", decide_ok, max_activities=5, use_sandbox=True)
                self.assertEqual(first.activities_completed + second.activities_completed, 6)
                experiences = experiences_of_session(parts["ledger"], "sess-long")
                self.assertEqual(len(experiences), 6)
                # Variety: not the same task on repeat.
                keys = [s.split(":")[1] for s in
                        parts["sessions"].open_or_create("sess-long").attempt_signatures]
                self.assertGreater(len(set(keys)), 1)
                # Follow-up chain links every experience to its predecessor.
                by_activity = {e.metadata["activity_id"]: e for e in experiences}
                ordered = sorted(by_activity)
                for earlier, later in zip(ordered, ordered[1:]):
                    self.assertIn(by_activity[earlier].id,
                                  by_activity[later].metadata["related"]["follow_up_of"])
                # Later selections cite earlier evidence (memory → decision).
                started = parts["ledger"].events_of_kind("activity_started")
                cited = {i for e in started for i in e.metadata.get("supporting_ids", [])}
                self.assertIn(failure.id, cited)
                self.assertTrue(any(e.id in cited for e in experiences),
                                "no later selection referenced an earlier experience")
                # Rotation happened instead of rigid priority.
                reasons = [e.metadata.get("selection_reason", "") for e in started]
                self.assertTrue(any("recency-discounted" in r for r in reasons))
                # Knowledge accumulated from the cycles.
                self.assertTrue(parts["store"].visible())
            finally:
                close_all(parts)


if __name__ == "__main__":
    unittest.main()
