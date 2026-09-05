from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import EventLedger, KnowledgeStore, LearningTracker, consolidate, reflect


def open_pair(tmp: str) -> tuple[EventLedger, KnowledgeStore]:
    db = Path(tmp) / "m.db"
    return EventLedger(db), KnowledgeStore(db)


class ReflectionTests(unittest.TestCase):
    def test_failure_pattern_needs_support(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                ledger.record("outcome", "Sandbox install failed: incompatible dependency.", {"outcome": "failure"})
                report = reflect(ledger)
                self.assertEqual(report.findings, ())  # single observation: no pattern
                ledger.record("outcome", "Sandbox install failed: incompatible dependency again.", {"outcome": "failure"})
                report = reflect(ledger)
                kinds = [f.finding_type for f in report.findings]
                self.assertIn("failure_pattern", kinds)
                pattern = next(f for f in report.findings if f.finding_type == "failure_pattern")
                self.assertEqual(len(pattern.evidence_ids), 2)
                self.assertTrue(all(isinstance(i, int) for i in report.recorded_event_ids))
                self.assertEqual(len(ledger.events_of_kind("reflection")), len(report.findings))
            finally:
                ledger.close()
                store.close()

    def test_open_question_and_preference_and_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                ledger.record("observation", "Which host runs the backup service?")
                ledger.record("observation", "Surya prefers coffee in the morning.")
                ledger.record("observation", "The backup service runs on host atlas.")
                ledger.record("observation", "Contradiction: backups moved from atlas to host vega.")
                report = reflect(ledger)
                kinds = {f.finding_type for f in report.findings}
                self.assertIn("open_question", kinds)
                self.assertIn("preference_candidate", kinds)
                self.assertIn("contradiction", kinds)
                contradiction = next(f for f in report.findings if f.finding_type == "contradiction")
                self.assertEqual(len(contradiction.evidence_ids), 2)
            finally:
                ledger.close()
                store.close()

    def test_reflection_is_bounded(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                for i in range(5):
                    ledger.record("learning", f"Lesson number {i} about budgets.")
                report = reflect(ledger, max_findings=2)
                self.assertLessEqual(len(report.findings), 2)
                with self.assertRaises(ValueError):
                    reflect(ledger, min_support=1)
            finally:
                ledger.close()
                store.close()


class LearningTests(unittest.TestCase):
    def test_single_preference_stays_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                event = ledger.record("observation", "Surya prefers coffee.")
                tracker = LearningTracker(store)
                state = tracker.observe_preference("drink", "Surya prefers coffee.", event.id)
                self.assertEqual(state.status, "candidate")
                self.assertEqual(state.occurrences, 1)
                self.assertEqual(store.active(), [])  # nothing adopted
            finally:
                ledger.close()
                store.close()

    def test_repeated_preference_gets_adopted(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                tracker = LearningTracker(store, threshold=3)
                for text in ("Surya prefers coffee.", "Coffee again please.", "Coffee every morning."):
                    event = ledger.record("observation", text)
                    state = tracker.observe_preference("drink", "Surya prefers coffee.", event.id)
                self.assertEqual(state.status, "adopted")
                self.assertEqual(state.occurrences, 3)
                self.assertEqual(len(store.active()), 1)
                self.assertEqual(store.active()[0].confidence, 0.75)
            finally:
                ledger.close()
                store.close()

    def test_correction_supersedes_immediately(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                event = ledger.record("observation", "Backups run on atlas.")
                old = store.propose("Backups run on atlas.", [event.id], 0.7)
                fix = ledger.record("observation", "Correction: backups run on vega.")
                tracker = LearningTracker(store)
                new = tracker.observe_correction(old.id, "Backups run on vega.", fix.id)
                self.assertEqual(store.get(old.id).status, "superseded")
                self.assertEqual(new.source_ids, (fix.id,))
            finally:
                ledger.close()
                store.close()

    def test_reflection_findings_consolidate_to_knowledge(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                ledger.record("outcome", "Deploy worked using the checklist.", {"outcome": "success"})
                ledger.record("outcome", "Deploy worked using the checklist twice.", {"outcome": "success"})
                report = reflect(ledger)
                self.assertTrue(report.findings)
                result = consolidate(ledger, store, budget=10)
                self.assertGreater(result.created, 0)
                self.assertTrue(store.covered_source_ids() >= set(report.recorded_event_ids[:1]))
            finally:
                ledger.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
