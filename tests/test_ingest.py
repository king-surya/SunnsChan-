import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    EventLedger,
    KnowledgeStore,
    MockApiSource,
    MockWebSource,
    acquire,
    acquire_api,
    evaluate_claim,
)


def open_pair(tmp: str) -> tuple[EventLedger, KnowledgeStore]:
    db = Path(tmp) / "m.db"
    return EventLedger(db), KnowledgeStore(db)


class IngestTests(unittest.TestCase):
    def test_evaluate_claim_caps_external_confidence(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, knowledge = open_pair(tmp)
            try:
                source = ledger.record("source", "seed", {"backend": "REAL"})
                knowledge.propose("Operators reconcile custom resources.", [source.id], 0.6,
                                  tags=["external"])
                evaluation = evaluate_claim(
                    "Operators reconcile custom resources.", knowledge,
                    source_reliability=0.9)
                self.assertLessEqual(evaluation.confidence, 0.69)
                self.assertTrue(evaluation.corroborated_ids)
            finally:
                ledger.close()
                knowledge.close()

    def test_acquire_api_records_structured_knowledge(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, knowledge = open_pair(tmp)
            api = MockApiSource(records={
                "status": [{"title": "Service status", "text": "all systems nominal today"}],
            })
            try:
                result = acquire_api(api, "status", ledger, knowledge)
                self.assertEqual(result.status, "stored")
                self.assertTrue(result.knowledge_ids)
                self.assertTrue(knowledge.recall("systems nominal"))
            finally:
                ledger.close()
                knowledge.close()

    def test_acquire_caches_fresh_source(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, knowledge = open_pair(tmp)
            web = MockWebSource(pages={"https://d.example/x": "Content that is long enough to keep."})
            try:
                first = acquire(web, "https://d.example/x", ledger, knowledge)
                self.assertEqual(first.status, "stored")
                second = acquire(web, "https://d.example/x", ledger, knowledge)
                self.assertEqual(second.status, "cached")
                self.assertTrue(second.cached)
                self.assertEqual(second.source_event_id, first.source_event_id)
            finally:
                ledger.close()
                knowledge.close()

    def test_acquire_failure_records_event_and_degrades(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, knowledge = open_pair(tmp)
            web = MockWebSource(pages={})
            try:
                result = acquire(web, "https://d.example/missing", ledger, knowledge)
                self.assertEqual(result.status, "failed")
                self.assertTrue(ledger.events_of_kind("source_failed"))
                self.assertEqual(knowledge.visible(), [])
            finally:
                ledger.close()
                knowledge.close()


if __name__ == "__main__":
    unittest.main()
