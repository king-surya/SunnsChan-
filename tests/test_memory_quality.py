from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import EventLedger, KnowledgeStore, consolidate


def open_ledger_and_store(temporary: str) -> tuple[EventLedger, KnowledgeStore]:
    db = Path(temporary) / "memory.db"
    ledger = EventLedger(db)
    store = KnowledgeStore(db)
    return ledger, store


class MemoryQualityTests(unittest.TestCase):
    def test_recall_handles_stemming_and_ranks_relevance(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger, store = open_ledger_and_store(temporary)
            ledger.record("observation", "nginx service stopped after memory pressure")
            ledger.record("observation", "calendar reminder was sent")
            events = ledger.recall("Why did nginx stop?")
            self.assertIn("nginx", events[0].text)
            ledger.close()
            store.close()

    def test_recall_falls_back_to_newest_on_empty_query(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger, store = open_ledger_and_store(temporary)
            first = ledger.record("observation", "first event about gardening")
            second = ledger.record("observation", "second event about gardening")
            events = ledger.recall("hi")
            self.assertEqual([e.id for e in events], [second.id, first.id])
            ledger.close()
            store.close()

    def test_knowledge_requires_source_traceability(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger, store = open_ledger_and_store(temporary)
            event = ledger.record("learning", "Check simulator constraints before installing.")
            record = store.propose("Check constraints before installing.", [event.id], 0.7)
            self.assertEqual(record.source_ids, (event.id,))
            self.assertEqual(store.get(record.id).statement, record.statement)
            self.assertEqual(len(store.active()), 1)
            ledger.close()
            store.close()

    def test_false_lessons_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger, store = open_ledger_and_store(temporary)
            event = ledger.record("outcome", "The install succeeded.")
            with self.assertRaises(ValueError):
                store.propose("", [event.id], 0.6)
            with self.assertRaises(ValueError):
                store.propose("A claim with no evidence.", [], 0.6)
            with self.assertRaises(ValueError):
                store.propose("Overconfident claim.", [event.id], 1.5)
            with self.assertRaises(ValueError):
                store.propose("Ghost source.", [9999], 0.6)
            self.assertEqual(store.active(), [])
            ledger.close()
            store.close()

    def test_stale_fact_supersession_keeps_history(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger, store = open_ledger_and_store(temporary)
            old_event = ledger.record("observation", "Surya prefers tea in the morning.")
            new_event = ledger.record("observation", "Surya switched to coffee in the morning.")
            old = store.propose("Surya prefers tea in the morning.", [old_event.id], 0.6)
            new = store.supersede(
                old.id, "Surya prefers coffee in the morning.", [new_event.id], 0.7
            )
            self.assertEqual(new.supersedes_id, old.id)
            self.assertEqual(store.get(old.id).status, "superseded")
            active_statements = [r.statement for r in store.active()]
            self.assertIn("Surya prefers coffee in the morning.", active_statements)
            self.assertNotIn("Surya prefers tea in the morning.", active_statements)
            # Raw events are untouched: the old observation still exists.
            self.assertEqual(len(ledger.events_of_kind("observation")), 2)
            ledger.close()
            store.close()

    def test_reject_keeps_audit_trail(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger, store = open_ledger_and_store(temporary)
            event = ledger.record("learning", "A lesson that turns out wrong.")
            record = store.propose("Wrong lesson.", [event.id], 0.5)
            rejected = store.reject(record.id, "Contradicted by later outcome.")
            self.assertEqual(rejected.status, "rejected")
            self.assertEqual(store.active(), [])
            ledger.close()
            store.close()

    def test_consolidation_is_budgeted_and_idempotent(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger, store = open_ledger_and_store(temporary)
            first = ledger.record("learning", "First lesson about sandbox budgets.")
            second = ledger.record("outcome", "Second lesson about drivers.", {"outcome": "failure"})
            result = consolidate(ledger, store, budget=1)
            self.assertEqual(result.created, 1)
            self.assertEqual(result.skipped_budget, 1)
            result2 = consolidate(ledger, store, budget=10)
            # One event still uncovered, so one more record; then stable.
            self.assertEqual(result2.created, 1)
            result3 = consolidate(ledger, store, budget=10)
            self.assertEqual(result3.created, 0)
            covered = store.covered_source_ids()
            self.assertIn(first.id, covered)
            self.assertIn(second.id, covered)
            ledger.close()
            store.close()

    def test_knowledge_recall_finds_relevant_claim(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger, store = open_ledger_and_store(temporary)
            event = ledger.record("learning", "Driving simulators need constraint checks.")
            store.propose("Driving simulators need constraint checks.", [event.id], 0.6)
            other = ledger.record("learning", "Cooking rendang takes patience.")
            store.propose("Cooking rendang takes patience.", [other.id], 0.6)
            hits = store.recall("driving simulator constraints")
            self.assertIn("Driving", hits[0].statement)
            ledger.close()
            store.close()


if __name__ == "__main__":
    unittest.main()
