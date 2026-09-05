from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    CuriosityStore,
    EventLedger,
    generate_activities,
    record_experience,
    related_experiences,
    select_activity,
)


def open_ledger(tmp: str) -> EventLedger:
    return EventLedger(Path(tmp) / "m.db")


class CuriosityTests(unittest.TestCase):
    def test_lifecycle_with_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                origin = ledger.record("observation", "Latency spikes at midnight.")
                store = CuriosityStore(Path(tmp) / "m.db")
                try:
                    item = store.open("Why does latency spike at midnight?", origin.id, topic="perf")
                    self.assertEqual(item.status, "new")
                    active = store.transition(item.id, "active", evidence_event_id=origin.id)
                    self.assertGreater(active.confidence, item.confidence)
                    investigating = store.transition(item.id, "investigating", evidence_event_id=origin.id)
                    self.assertEqual(investigating.status, "investigating")
                    answered = store.transition(item.id, "answered", evidence_event_id=origin.id)
                    self.assertEqual(answered.status, "answered")
                    self.assertEqual(store.open_curiosities(), [])
                    reopened = store.transition(item.id, "reopened", evidence_event_id=origin.id)
                    self.assertEqual(reopened.status, "reopened")
                    with self.assertRaises(ValueError):
                        store.transition(item.id, "new", evidence_event_id=origin.id)
                    with self.assertRaises(ValueError):
                        store.open("  ", origin.id)
                    with self.assertRaises(ValueError):
                        store.open("Why?", 9999)
                finally:
                    store.close()
            finally:
                ledger.close()


class ExperienceTests(unittest.TestCase):
    def test_rich_experience_and_relationships(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                first = record_experience(
                    ledger, session_id="s1", activity_id="s1-a1",
                    intent="Probe scheduler latency", hypothesis="Cron drifts.",
                    actions=["ran probe"], observations=["p99 2.1s"],
                    result="failure", lessons=["Check cron first."],
                    unresolved_questions=["Why only at midnight?"],
                )
                self.assertEqual(first.kind, "experience")
                second = record_experience(
                    ledger, session_id="s1", activity_id="s1-a2",
                    intent="Retry probe with checklist", result="success",
                    related={"follow_up_of": [first.id], "confirms": [first.id]},
                )
                resolved = related_experiences(ledger, second.id)
                self.assertEqual([e.id for e in resolved["follow_up_of"]], [first.id])
                with self.assertRaises(ValueError):
                    record_experience(ledger, session_id="s", activity_id="a",
                                      intent="x", related={"bogus": [1]})
            finally:
                ledger.close()


class ActivityTests(unittest.TestCase):
    def test_generation_from_curiosity_goal_and_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                store = CuriosityStore(Path(tmp) / "m.db")
                try:
                    origin = ledger.record("observation", "Disk fills every Sunday.")
                    store.open("Why does disk fill on Sundays?", origin.id, importance=0.8)
                    ledger.record("goal_opened", "Keep disk healthy", {"reason": "ops", "priority": 0.7})
                    ledger.record("outcome", "Cleanup script failed.", {"outcome": "failure"})
                    candidates = generate_activities(ledger, None, store)
                    sources = {c.source for c in candidates}
                    self.assertIn("CURIOSITY", sources)
                    self.assertIn("GOAL", sources)
                    self.assertIn("PREVIOUS_FAILURE", sources)
                    selection = select_activity(candidates)
                    self.assertIn(selection.selected.key, [c.key for c in candidates])
                    self.assertTrue(selection.reason)
                    self.assertIn(selection.selected.key, selection.reason)
                    avoided = select_activity(candidates, avoid_keys=(selection.selected.key,))
                    self.assertNotEqual(avoided.selected.key, selection.selected.key)
                    # Recency discount rotates: a standing winner yields to novelty.
                    first_key = selection.selected.key
                    rotated = select_activity(candidates, recent_keys=(first_key, first_key))
                    self.assertNotEqual(rotated.selected.key, first_key)
                    # Sole candidate still runs (discounted, not blocked) with a note.
                    solo = select_activity([candidates[0]], recent_keys=(candidates[0].key,))
                    self.assertEqual(solo.selected.key, candidates[0].key)
                    self.assertIn("recency-discounted", solo.reason)
                    with self.assertRaises(ValueError):
                        select_activity([])
                finally:
                    store.close()
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
