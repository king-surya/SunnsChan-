from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    EventLedger,
    KnowledgeStore,
    LearnedStore,
    consolidate_learning,
    consolidation_due,
    decay_stale,
    mine_patterns,
    mine_preferences,
    mine_skills,
    mine_strategies,
    record_experience,
    strategy_bonus_for,
)


def open_all(tmp: str):
    db = Path(tmp) / "m.db"
    return EventLedger(db), KnowledgeStore(db), LearnedStore(db)


def close_all(ledger, store, learned) -> None:
    ledger.close()
    store.close()
    learned.close()


class MinerTests(unittest.TestCase):
    def test_pattern_miner_with_minority_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                for i in range(3):
                    ledger.record("outcome", "Checklist deploy succeeds cleanly.",
                                  {"outcome": "success"})
                ledger.record("outcome", "Checklist deploy succeeds cleanly once more.",
                              {"outcome": "failure"})
                touched = mine_patterns(ledger, learned)
                patterns = learned.by_kind("pattern", statuses=("established",))
                self.assertEqual(len(patterns), 1)
                self.assertEqual(patterns[0].support_count, 3)
                self.assertEqual(patterns[0].contra_count, 1)
                self.assertEqual(patterns[0].conditions["outcome"], "success")
                self.assertTrue(touched)
            finally:
                close_all(ledger, store, learned)

    def test_skill_miner_tracks_success_rate(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                for i in range(3):
                    ledger.record("activity_started", f"Probe {i}",
                                  {"activity_key": f"k{i}", "category": "INVESTIGATE"})
                    ledger.record("activity_finished", f"Probe {i} done",
                                  {"activity_key": f"k{i}", "result": "success"})
                ledger.record("activity_started", "Probe X", {"activity_key": "kx", "category": "INVESTIGATE"})
                ledger.record("activity_finished", "Probe X failed", {"activity_key": "kx", "result": "failure"})
                mine_skills(ledger, learned)
                skills = learned.by_kind("skill")
                parents = [s for s in skills if s.subject == "skill:investigate"]
                self.assertEqual(len(parents), 1)
                self.assertEqual(parents[0].support_count, 3)
                self.assertEqual(parents[0].contra_count, 1)
                self.assertEqual(parents[0].status, "established")
                children = [s for s in skills if s.subject.startswith("skill:investigate:")]
                self.assertTrue(children)  # sub-skills emerge alongside the parent
            finally:
                close_all(ledger, store, learned)

    def test_preference_miner_and_stuck_revision(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                for i in range(3):
                    ledger.record("activity_started", f"Build {i}",
                                  {"activity_key": f"b{i}", "category": "BUILD"})
                mine_preferences(ledger, learned)
                prefs = learned.by_kind("preference")
                self.assertEqual(len(prefs), 1)
                self.assertEqual(prefs[0].status, "established")
                ledger.record("activity_finished", "stuck on build",
                              {"activity_key": "b2", "result": "failure", "stuck": True})
                mine_preferences(ledger, learned)
                prefs = learned.by_kind("preference")
                self.assertEqual(prefs[0].contra_count, 1)
            finally:
                close_all(ledger, store, learned)

    def test_strategy_miner_contextual(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                goal = ledger.record("goal_opened", "Fix deploy", {"reason": "ops", "priority": 0.8})
                ledger.record("activity_started", "Try checklist", {"activity_key": "a1", "category": "TEST"})
                for i in range(3):
                    record_experience(ledger, session_id="s", activity_id=f"s-a{i}",
                                      intent="Checklist deploy attempt", result="success",
                                      goal_id=goal.id)
                record_experience(ledger, session_id="s", activity_id="s-free",
                                  intent="Free exploration works", result="success")
                mine_strategies(ledger, learned)
                strategies = learned.by_kind("strategy")
                subjects = {s.subject for s in strategies}
                self.assertTrue(any(f"goal-{goal.id}" in s for s in subjects))
                general = [s for s in strategies if s.subject.startswith("strategy:general")]
                self.assertTrue(general)
            finally:
                close_all(ledger, store, learned)

    def test_strategy_bonus_bounded(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                self.assertEqual(strategy_bonus_for(learned, goal_id=None, category="TEST"), 1.0)
                goal = ledger.record("goal_opened", "Fix deploy", {"reason": "x", "priority": 0.5})
                ledger.record("activity_started", "probe", {"activity_key": "p0", "category": "TEST"})
                for i in range(4):
                    record_experience(ledger, session_id="s", activity_id=f"s-a{i}",
                                      intent="Checklist deploy works", result="success",
                                      goal_id=goal.id)
                mine_strategies(ledger, learned)
                bonus = strategy_bonus_for(learned, goal_id=goal.id, category="TEST")
                self.assertEqual(bonus, 1.2)
                self.assertLessEqual(bonus, 1.3)
            finally:
                close_all(ledger, store, learned)


class ConsolidationTests(unittest.TestCase):
    def test_batch_mines_and_promotes(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                for i in range(4):
                    ledger.record("outcome", "Checklist deploy succeeds cleanly.",
                                  {"outcome": "success"})
                result = consolidate_learning(ledger, store, learned, budget=20)
                self.assertGreater(result.patterns, 0)
                self.assertGreater(result.promoted, 0)
                self.assertEqual(result.errors, [])
                learned_objs = learned.by_kind("pattern", statuses=("established",))
                self.assertTrue(all(o.knowledge_id is not None for o in learned_objs))
                claims = [r for r in store.visible(limit=50) if "learned" in r.tags]
                self.assertTrue(claims)
                # Idempotent re-run: no duplicate knowledge.
                before = len(store.visible(limit=100))
                again = consolidate_learning(ledger, store, learned, budget=20)
                self.assertEqual(len(store.visible(limit=100)), before)
                self.assertEqual(again.promoted, 0)
            finally:
                close_all(ledger, store, learned)

    def test_contradicted_demotes_linked_knowledge(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                for i in range(3):
                    ledger.record("outcome", "Strategy X delivers.", {"outcome": "success"})
                consolidate_learning(ledger, store, learned, budget=20)
                for i in range(4):
                    ledger.record("outcome", "Strategy X delivers.", {"outcome": "failure"})
                result = consolidate_learning(ledger, store, learned, budget=30)
                self.assertGreater(result.demoted, 0)
                uncertain = store.by_status("uncertain")
                self.assertTrue(uncertain)
            finally:
                close_all(ledger, store, learned)

    def test_failure_safety_raw_intact(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                event = ledger.record("outcome", "token: hunter2 deployed.", {"outcome": "success"})
                for _ in range(2):
                    ledger.record("outcome", "token: hunter2 deployed.", {"outcome": "success"})
                consolidate_learning(ledger, store, learned, budget=20)
                # Raw event untouched; learned claim scrubbed.
                self.assertIn("hunter2", ledger.events_of_kind("outcome")[0].text)
                for record in store.visible(limit=50):
                    self.assertNotIn("hunter2", record.statement)
                self.assertEqual(event.text, "token: hunter2 deployed.")
            finally:
                close_all(ledger, store, learned)

    def test_consolidation_due_triggers(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                due, why = consolidation_due(ledger, learned)
                self.assertFalse(due)  # nothing to absorb
                for i in range(6):
                    ledger.record("outcome", "Checklist deploy succeeds.", {"outcome": "success"})
                due, why = consolidation_due(ledger, learned)
                self.assertTrue(due)
                self.assertIn("uncovered", why)
                consolidate_learning(ledger, store, learned, budget=30)
                due, _ = consolidation_due(ledger, learned)
                self.assertFalse(due)  # absorbed; no cron robot
            finally:
                close_all(ledger, store, learned)

    def test_decay_is_justified(self) -> None:
        from datetime import UTC, datetime

        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                for i in range(3):
                    e = ledger.record("outcome", "Old reliable method works.", {"outcome": "success"})
                    learned.observe("pattern", "pattern:success:reliable", e.id)
                obj = learned.by_kind("pattern", statuses=("established",))[0]
                future = datetime.now(UTC).replace(year=datetime.now(UTC).year + 1)
                decayed = decay_stale(learned, older_than_days=30, now=future)
                self.assertEqual(len(decayed), 1)
                self.assertLess(decayed[0].confidence, obj.confidence)
                with self.assertRaises(ValueError):
                    decay_stale(learned, older_than_days=0)
            finally:
                close_all(ledger, store, learned)


if __name__ == "__main__":
    unittest.main()
