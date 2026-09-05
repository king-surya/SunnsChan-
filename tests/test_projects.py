from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    AutonomyEngine,
    AgentCore,
    CuriosityStore,
    Decision,
    EventLedger,
    IdentitySeed,
    KnowledgeStore,
    PolicyGate,
    SessionStore,
    record_experience,
)
from suns_chan.context import affect_section, environment_section, graph_section
from suns_chan.projects import format_projects, list_projects


PROJECT = Path(__file__).resolve().parents[1]


def open_all(tmp: str):
    db = Path(tmp) / "m.db"
    return {
        "ledger": EventLedger(db),
        "store": KnowledgeStore(db),
        "curiosities": CuriosityStore(db),
        "sessions": SessionStore(db),
    }


def close_all(parts) -> None:
    parts["ledger"].close()
    parts["store"].close()
    parts["curiosities"].close()
    parts["sessions"].close()


class ProjectTests(unittest.TestCase):
    def test_active_project_links_records(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                ledger = parts["ledger"]
                goal = ledger.record("goal_opened", "Restore the staging server",
                                     {"reason": "ops", "priority": 0.8})
                exp = record_experience(ledger, session_id="s", activity_id="s-a1",
                                        intent="Check staging disk", result="success",
                                        goal_id=goal.id,
                                        unresolved_questions=["Why is disk full?"])
                origin = ledger.record("observation", "Staging server disk fills on Sundays.")
                cur = parts["curiosities"].open("Why does the staging server fill its disk?", origin.id, topic="staging")
                fact = ledger.record("observation", "Staging server disk is 90% full.")
                parts["store"].propose("Staging server disk is nearly full.", [fact.id], 0.7)
                views = list_projects(ledger, knowledge=parts["store"], curiosities=parts["curiosities"])
                self.assertEqual(len(views), 1)
                view = views[0]
                self.assertEqual(view.status, "active")
                self.assertEqual(view.goal_ids, (goal.id,))
                self.assertIn(exp.id, view.experience_ids)
                self.assertEqual(len(view.knowledge_ids), 1)
                self.assertIn(cur.id, view.curiosity_ids)
                self.assertIn("Why is disk full?", view.open_questions)
                text = format_projects(views)
                self.assertIn("active", text)
            finally:
                close_all(parts)

    def test_completed_project_and_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                ledger = parts["ledger"]
                goal = ledger.record("goal_opened", "Old task", {"reason": "x", "priority": 0.5})
                ledger.record("goal_closed", "done", {"goal_id": goal.id})
                views = list_projects(ledger)
                self.assertEqual(views[0].status, "completed")
                self.assertIn("completed", format_projects(views))
            finally:
                close_all(parts)

    def test_context_sections_bounded(self) -> None:
        section = environment_section([f"line {i} " + "x" * 100 for i in range(20)])
        self.assertLessEqual(len(section), 700)
        self.assertEqual(environment_section([]), "")
        self.assertIn("rhythm", affect_section("rhythm=steady energy=0.70"))
        self.assertEqual(affect_section("  "), "")
        self.assertEqual(graph_section("(no paths)"), "")
        self.assertIn("via E", graph_section("experience#1 -[confirms]-> knowledge#2 (via E5)"))

    def test_describe_extensions(self) -> None:
        with TemporaryDirectory() as tmp:
            parts = open_all(tmp)
            try:
                ledger = parts["ledger"]
                core = AgentCore(ledger, PolicyGate(),
                                 IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"))
                engine = AutonomyEngine(ledger, core, parts["sessions"])
                view = engine.describe("sess-x", environment=["vm.web running"],
                                       affect_note="rhythm=steady",
                                       graph_recent=["experience#1 links knowledge#2"],
                                       comms_pending=1, voice_state="mock",
                                       avatar_state="mock")
                self.assertEqual(view["environment"], ["vm.web running"])
                self.assertEqual(view["affect"], "rhythm=steady")
                self.assertEqual(view["comms_pending"], 1)
                self.assertEqual(view["voice"], "mock")
                # Defaults keep old callers working.
                plain = engine.describe("sess-x")
                self.assertEqual(plain["environment"], [])
            finally:
                close_all(parts)


if __name__ == "__main__":
    unittest.main()
