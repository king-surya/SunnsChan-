import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import EventLedger, UnderstandingStore, mine_understanding


def open_pair(tmp: str) -> tuple[EventLedger, UnderstandingStore]:
    db = Path(tmp) / "m.db"
    return EventLedger(db), UnderstandingStore(db)


class UnderstandingStoreTests(unittest.TestCase):
    def test_kinds_are_distinct_and_capped(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                event = ledger.record("observation", "Surya stated X.")
                fact = store.note("fact", "Surya stated X.", [event.id], 0.9)
                inference = store.note("inference", "Surya probably likes Y.", [event.id], 0.9)
                self.assertEqual(fact.kind, "fact")
                # Inference can never be promoted into fact confidence range.
                self.assertLessEqual(inference.confidence, 0.55)
                self.assertLess(inference.confidence, fact.confidence)
            finally:
                ledger.close()
                store.close()

    def test_revision_preserves_history(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                first = ledger.record("observation", "Surya prefers coffee.")
                old = store.note("preference", "Surya prefers coffee.", [first.id])
                second = ledger.record("observation", "Surya moved to tea.")
                new = store.revise(old.id, "preference", "Surya prefers tea.", [second.id],
                                   reason="changed taste")
                self.assertEqual(store.get(old.id).status, "superseded")
                self.assertEqual(new.supersedes_id, old.id)
                self.assertEqual(new.statement, "Surya prefers tea.")
                # Raw events untouched.
                self.assertEqual(len(ledger.events_of_kind("observation")), 2)
                explanation = store.explain(new.id)
                self.assertEqual(explanation.supporting[0].event_id, second.id)
            finally:
                ledger.close()
                store.close()

    def test_contradiction_marks_both_sides_explainable(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                first = ledger.record("observation", "Surya works on project atlas.")
                old = store.note("fact", "Surya works on project atlas.", [first.id])
                second = ledger.record("observation", "Correction: Surya now works on project vega.")
                new = store.revise(old.id, "fact", "Surya now works on project vega.",
                                   [second.id], contradict=True)
                self.assertEqual(store.get(old.id).status, "contradicted")
                self.assertEqual(new.supersedes_id, old.id)
                old_explanation = store.explain(old.id)
                self.assertEqual(old_explanation.contradicted_by[0].event_id, second.id)
            finally:
                ledger.close()
                store.close()

    def test_contextual_retrieval_is_relevant_not_dump(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                a = ledger.record("observation", "Surya likes driving.")
                b = ledger.record("observation", "Surya likes cooking.")
                store.note("preference", "Surya likes driving.", [a.id], tags=["driving"])
                store.note("preference", "Surya likes cooking.", [b.id], tags=["cooking"])
                hits = store.relevant("driving", limit=5)
                self.assertEqual(hits[0].statement, "Surya likes driving.")
            finally:
                ledger.close()
                store.close()

    def test_persistence_across_reopen(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            store = UnderstandingStore(db)
            event = ledger.record("observation", "Surya prefers vim.")
            store.note("preference", "Surya prefers vim.", [event.id])
            ledger.close()
            store.close()

            reopened = UnderstandingStore(db)
            try:
                records = reopened.visible()
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].statement, "Surya prefers vim.")
                self.assertEqual(records[0].source_ids, (event.id,))
            finally:
                reopened.close()


class MineUnderstandingTests(unittest.TestCase):
    def test_preference_and_fact_and_repetition(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                ledger.record("observation", "Surya prefers tea.")
                ledger.record("observation", "Surya prefers tea.")
                ledger.record("observation", "Surya is building a homelab.")
                touched = mine_understanding(ledger, store)
                records = store.visible()
                statements = {r.statement for r in records}
                self.assertIn("Surya prefers tea.", statements)
                self.assertIn("Surya is building a homelab.", statements)
                # Repetition affirmed, not duplicated.
                pref = [r for r in records if r.statement == "Surya prefers tea."][0]
                self.assertEqual(len(pref.source_ids), 2)
                # Idempotent: re-mining does not double count.
                again = mine_understanding(ledger, store)
                self.assertEqual(len(again), 0)
            finally:
                ledger.close()
                store.close()

    def test_contradiction_revises_not_overwrites(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                ledger.record("observation", "Surya works on project atlas.")
                mine_understanding(ledger, store)
                ledger.record("observation", "Correction: Surya moved to project vega.")
                mine_understanding(ledger, store)
                records = store.history()
                statuses = {r.statement: r.status for r in records}
                self.assertIn("contradicted", statuses.values())
                active = [r for r in records if r.status == "active"]
                self.assertTrue(any("vega" in r.statement for r in active))
                # Both sides preserved in history.
                self.assertTrue(any("atlas" in r.statement for r in records))
            finally:
                ledger.close()
                store.close()

    def test_questions_are_not_facts(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                ledger.record("observation", "Does Surya like this?")
                mine_understanding(ledger, store)
                self.assertEqual(store.visible(), [])
            finally:
                ledger.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
