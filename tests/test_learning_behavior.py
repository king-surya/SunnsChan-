"""Phase 6 behavioral tests: learning must measurably change future behavior.

Not row-existence checks: every test asserts a changed decision, priority,
status, or explanation traceable to evidence.
"""

import ast
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    ActionRequest,
    ActionVerdict,
    EventLedger,
    KnowledgeStore,
    LearnedStore,
    PolicyGate,
    generate_activities,
    record_experience,
    select_activity,
    strategy_bonus_for,
)
from suns_chan.activities import ActivityCandidate
from suns_chan.learned import explain_learning, mine_preferences, mine_skills, mine_strategies


PROJECT = Path(__file__).resolve().parents[1]


def open_all(tmp: str):
    db = Path(tmp) / "m.db"
    return EventLedger(db), KnowledgeStore(db), LearnedStore(db)


def close_all(ledger, store, learned) -> None:
    ledger.close()
    store.close()
    learned.close()


def candidates_pair(*, relevance: float = 0.7, usefulness: float = 0.7) -> tuple[ActivityCandidate, ActivityCandidate]:
    matching = ActivityCandidate(key="goal-1", category="TEST", title="Checklist deploy",
                                 reason="goal work", source="GOAL", goal_id=7,
                                 relevance=relevance, usefulness=usefulness)
    other = ActivityCandidate(key="curiosity-2", category="EXPLORE", title="Wander logs",
                              reason="curious", source="CURIOSITY",
                              relevance=0.62, curiosity=0.62, usefulness=0.62,
                              learning=0.62, novelty=0.62)
    return matching, other


class StrategyBehaviorTests(unittest.TestCase):
    def _establish(self, ledger, learned, goal_id: int, outcome: str, count: int) -> None:
        ledger.record("activity_started", "probe", {"activity_key": "p0", "category": "TEST"})
        for i in range(count):
            record_experience(ledger, session_id="s", activity_id=f"s-{outcome}-{i}",
                              intent=f"Checklist deploy {outcome}", result=outcome,
                              goal_id=goal_id)
        mine_strategies(ledger, learned)

    def test_1_success_raises_priority(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                self._establish(ledger, learned, 7, "success", 4)
                matching, other = candidates_pair(relevance=0.7, usefulness=0.7)
                plain = select_activity([matching, other])
                self.assertEqual(plain.selected.key, other.key)  # loses without learning
                boosted = select_activity(
                    [matching, other],
                    score_multipliers={matching.key: strategy_bonus_for(
                        learned, goal_id=7, category="TEST")})
                self.assertEqual(boosted.selected.key, matching.key)
                self.assertIn("strategy x1.20", boosted.reason)
                self.assertGreaterEqual(boosted.score, plain.score)
            finally:
                close_all(ledger, store, learned)

    def test_2_failure_lowers_priority(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                self._establish(ledger, learned, 7, "failure", 5)
                matching, other = candidates_pair(relevance=0.9, usefulness=0.9)
                plain = select_activity([matching, other])
                self.assertEqual(plain.selected.key, matching.key)  # wins without learning
                # Contradicted strategy drags its candidate below the alternative.
                assert strategy_bonus_for(learned, goal_id=7, category="TEST") == 0.7
                result = select_activity(
                    [matching, other],
                    score_multipliers={matching.key: 0.7})
                self.assertEqual(result.selected.key, other.key)
            finally:
                close_all(ledger, store, learned)

    def test_3_contradiction_changes_strategy_status(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                self._establish(ledger, learned, 7, "success", 4)
                before = strategy_bonus_for(learned, goal_id=7, category="TEST")
                self.assertEqual(before, 1.2)
                for i in range(3):
                    record_experience(ledger, session_id="s", activity_id=f"s-f{i}",
                                      intent="Checklist deploy fails now", result="failure",
                                      goal_id=7)
                mine_strategies(ledger, learned)
                after = strategy_bonus_for(learned, goal_id=7, category="TEST")
                self.assertLess(after, before)
            finally:
                close_all(ledger, store, learned)


class SkillPreferenceBehaviorTests(unittest.TestCase):
    def test_4_preference_emerges_from_voluntary_selection(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                for i in range(3):
                    ledger.record("activity_started", f"Explore {i}",
                                  {"activity_key": f"e{i}", "category": "EXPLORE"})
                mine_preferences(ledger, learned)
                prefs = learned.by_kind("preference", statuses=("established",))
                self.assertEqual(len(prefs), 1)
                self.assertEqual(prefs[0].support_count, 3)
            finally:
                close_all(ledger, store, learned)

    def test_5_6_skill_progression_and_regression(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                for i in range(3):
                    ledger.record("activity_started", f"Probe {i}",
                                  {"activity_key": f"k{i}", "category": "INVESTIGATE"})
                    ledger.record("activity_finished", "ok",
                                  {"activity_key": f"k{i}", "result": "success"})
                from suns_chan.learned import mine_skills

                mine_skills(ledger, learned)
                skill = next(o for o in learned.by_kind("skill")
                             if o.subject == "skill:investigate")
                peak = skill.confidence
                self.assertEqual(skill.status, "established")
                for i in range(3):
                    ledger.record("activity_started", f"Bad {i}",
                                  {"activity_key": f"z{i}", "category": "INVESTIGATE"})
                    ledger.record("activity_finished", "bad",
                                  {"activity_key": f"z{i}", "result": "failure"})
                mine_skills(ledger, learned)
                regressed = next(o for o in learned.by_kind("skill")
                                 if o.subject == "skill:investigate")
                self.assertLess(regressed.confidence, peak)
                self.assertEqual(regressed.status, "uncertain")
            finally:
                close_all(ledger, store, learned)


class PersistenceBehaviorTests(unittest.TestCase):
    def test_7_8_9_learning_survives_restart(self) -> None:
        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "m.db")
            ledger, store, learned = EventLedger(db), KnowledgeStore(db), LearnedStore(db)
            try:
                event = ledger.record("outcome", "Checklist deploy succeeds.", {"outcome": "success"})
                for _ in range(2):
                    ledger.record("outcome", "Checklist deploy succeeds.", {"outcome": "success"})
                for e in ledger.events_of_kind("outcome"):
                    learned.observe("strategy", "strategy:general:test", e.id)
                obj_id = learned.by_kind("strategy")[0].id
            finally:
                ledger.close()
                store.close()
                learned.close()
            ledger2, store2, learned2 = EventLedger(db), KnowledgeStore(db), LearnedStore(db)
            try:
                obj = learned2.get(obj_id)
                self.assertEqual(obj.status, "established")
                self.assertEqual(strategy_bonus_for(learned2, goal_id=None, category="TEST"), 1.2)
                explanation = explain_learning(learned2, ledger2, obj_id)
                self.assertEqual(len(explanation.supporting), 3)
            finally:
                ledger2.close()
                store2.close()
                learned2.close()

    def test_10_11_raw_events_recoverable_and_unmodified(self) -> None:
        import hashlib

        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                event = ledger.record("outcome", "Original wording here.", {"outcome": "success"})
                digest = hashlib.sha256(event.text.encode()).hexdigest()
                from suns_chan import consolidate_learning

                for _ in range(2):
                    ledger.record("outcome", "Original wording here.", {"outcome": "success"})
                consolidate_learning(ledger, store, learned, budget=20)
                fetched = ledger.events_of_kind("outcome")
                self.assertEqual(len(fetched), 3)
                original = next(e for e in fetched if e.id == event.id)
                self.assertEqual(hashlib.sha256(original.text.encode()).hexdigest(), digest)
                self.assertEqual(original.text, "Original wording here.")
            finally:
                close_all(ledger, store, learned)


class AuthorizationBoundaryTests(unittest.TestCase):
    def test_12_policy_unaffected_by_learning(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                event = ledger.record("observation", "User loves shell access.")
                for _ in range(3):
                    learned.observe("preference", "prefers:shell", event.id)
                gate = PolicyGate()
                self.assertEqual(gate.evaluate(ActionRequest("x", "destructive", "x")), ActionVerdict.DENY)
                self.assertEqual(
                    gate.evaluate(ActionRequest("sh", "command", "run", scope="external")),
                    ActionVerdict.REQUIRE_APPROVAL)
            finally:
                close_all(ledger, store, learned)

    def test_13_sandbox_controller_has_no_learning_inputs(self) -> None:
        tree = ast.parse((PROJECT / "src" / "suns_chan" / "sandbox_controller.py").read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
        self.assertFalse(any("learned" in name or "learning" in name for name in imports))

    def test_14_comms_still_requires_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                from suns_chan import CommunicationProposal, MockChannel, PolicyGate, dispatch

                event = ledger.record("observation", "Operator trusts console updates.")
                for _ in range(3):
                    learned.observe("preference", "prefers:console", event.id)
                channel = MockChannel()
                result = dispatch(CommunicationProposal("console", "ops", "hi", "update"),
                                  PolicyGate(), None, channel, ledger)
                self.assertFalse(result.sent)
                self.assertEqual(channel.outbox, [])
            finally:
                close_all(ledger, store, learned)

    def test_15_explainability(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                ids = [ledger.record("outcome", f"Checklist works {i}.", {"outcome": "success"}).id
                       for i in range(3)]
                for event_id in ids:
                    learned.observe("strategy", "strategy:general:test", event_id)
                obj = learned.by_kind("strategy")[0]
                explanation = explain_learning(learned, ledger, obj.id)
                self.assertIn("strategy:general:test", explanation.what)
                self.assertGreater(explanation.confidence, 0.6)
                self.assertEqual(len(explanation.supporting), 3)
                self.assertIn("supporting", explanation.breakdown)
            finally:
                close_all(ledger, store, learned)


if __name__ == "__main__":
    unittest.main()
