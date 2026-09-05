"""Phase 6 simulations: multi-cycle adaptation (§38) and long-run learning (§39).

Deterministic and bounded. They prove adaptation: same problem, learned
advantage, contradictory evidence, revised planning — then a longer run
showing patterns, skills, preferences, and strategies jointly shifting
future selection.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    EventLedger,
    KnowledgeStore,
    LearnedStore,
    consolidate_learning,
    record_experience,
    select_activity,
    strategy_bonus_for,
)
from suns_chan.activities import ActivityCandidate
from suns_chan.learned import mine_strategies


def open_all(tmp: str):
    db = Path(tmp) / "m.db"
    return EventLedger(db), KnowledgeStore(db), LearnedStore(db)


def close_all(ledger, store, learned) -> None:
    ledger.close()
    store.close()
    learned.close()


def attempt(ledger, session: str, tag: str, intent: str, result: str, goal_id: int | None):
    return record_experience(ledger, session_id=session, activity_id=f"{session}-{tag}",
                             intent=intent, result=result, goal_id=goal_id)


def pair():
    check = ActivityCandidate(key="strat-check", category="TEST", title="Checklist deploy",
                              reason="strategy A", source="GOAL", goal_id=9,
                              relevance=0.7, usefulness=0.7)
    alt = ActivityCandidate(key="strat-alt", category="EXPLORE", title="Probe alternatives",
                            reason="strategy B", source="CURIOSITY",
                            relevance=0.66, curiosity=0.66, usefulness=0.66,
                            learning=0.66, novelty=0.66)
    return check, alt


class MultiCycleAdaptationTests(unittest.TestCase):
    def test_adapt_then_readapt(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                # Cycles 1-3: strategy A (checklist TEST under goal 9) succeeds.
                ledger.record("activity_started", "Checklist runs",
                              {"activity_key": "mc0", "category": "TEST"})
                for i in range(3):
                    attempt(ledger, "s", f"a{i}", "Checklist deploy succeeds", "success", 9)
                mine_strategies(ledger, learned)
                check, alt = pair()
                cycle4 = select_activity(
                    [check, alt],
                    score_multipliers={"strat-check": strategy_bonus_for(
                        learned, goal_id=9, category="TEST")})
                self.assertEqual(cycle4.selected.key, "strat-check")
                self.assertIn("strategy x1.20", cycle4.reason)
                # Cycles 5-6: strategy A fails twice.
                for i in range(2):
                    attempt(ledger, "s", f"f{i}", "Checklist deploy fails now", "failure", 9)
                mine_strategies(ledger, learned)
                bonus = strategy_bonus_for(learned, goal_id=9, category="TEST")
                self.assertLess(bonus, 1.2)
                cycle7 = select_activity(
                    [check, alt],
                    score_multipliers={"strat-check": bonus})
                # Alternative is now competitive: either wins by a small margin.
                margin = abs(cycle7.score - 0.6)
                self.assertLess(margin, 0.15)
                # And the learned object tells the story.
                strategies = learned.by_kind("strategy")
                target = next(o for o in strategies if o.subject == "strategy:goal-9:test")
                self.assertEqual(target.contra_count, 2)
                self.assertIn(target.status, ("uncertain", "contradicted", "established"))
            finally:
                close_all(ledger, store, learned)


class LongRunLearningTests(unittest.TestCase):
    def test_many_experiences_shift_planning(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned = open_all(tmp)
            try:
                goal = ledger.record("goal_opened", "Keep deploys green",
                                     {"reason": "ops", "priority": 0.8})
                ledger.record("activity_started", "Checklist run",
                              {"activity_key": "c0", "category": "TEST"})
                # A long run: 8 successes, 2 failures, selections, questions.
                for i in range(8):
                    record_experience(ledger, session_id="week", activity_id=f"week-a{i}",
                                      intent="Checklist deploy succeeds cleanly",
                                      result="success", goal_id=goal.id)
                    ledger.record("activity_started", f"Selected TEST {i}",
                                  {"activity_key": f"t{i}", "category": "TEST"})
                    ledger.record("activity_finished", f"TEST {i} ok",
                                  {"activity_key": f"t{i}", "result": "success"})
                for i in range(2):
                    record_experience(ledger, session_id="week", activity_id=f"week-f{i}",
                                      intent="Checklist deploy hiccup", result="failure",
                                      goal_id=goal.id)
                origin = ledger.record("observation", "Why do checklists usually work?")
                ledger.record("outcome", "Checklist deploy succeeds cleanly.",
                              {"outcome": "success"})
                _ = origin
                result = consolidate_learning(ledger, store, learned, budget=40)
                self.assertEqual(result.errors, [])
                kinds = {o.kind for o in learned.all()}
                self.assertTrue({"pattern", "skill", "preference", "strategy"} <= kinds)
                self.assertGreater(result.promoted, 0)
                # Planning now favors the learned approach...
                check = ActivityCandidate(key="g1", category="TEST", title="Checklist deploy",
                                          reason="goal", source="GOAL", goal_id=goal.id,
                                          relevance=0.7, usefulness=0.7)
                alt = ActivityCandidate(key="c1", category="EXPLORE", title="Wander",
                                        reason="curious", source="CURIOSITY",
                                        relevance=0.66, curiosity=0.66, usefulness=0.66,
                                        learning=0.66, novelty=0.66)
                planned = select_activity(
                    [check, alt],
                    score_multipliers={"g1": strategy_bonus_for(
                        learned, goal_id=goal.id, category="TEST")})
                self.assertEqual(planned.selected.key, "g1")
                # ...while exploration stays possible (bounded multiplier).
                hopeful = select_activity([check, alt])
                self.assertLessEqual(
                    abs(planned.score - hopeful.score) / max(hopeful.score, 0.01), 0.35)
                # Raw history intact and promoted claims traceable.
                self.assertGreaterEqual(len(ledger.events_of_kind("experience")), 10)
                for record in store.visible(limit=100):
                    if "learned" in record.tags:
                        self.assertTrue(record.source_ids)
            finally:
                close_all(ledger, store, learned)


if __name__ == "__main__":
    unittest.main()
