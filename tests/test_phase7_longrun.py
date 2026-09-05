"""Long-run simulation: repeated interactions must produce evolving, bounded
understanding — no profile dumping, no duplicate facts, no stale certainty.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    EventLedger,
    KnowledgeStore,
    UnderstandingStore,
    build_knowledge_section,
    build_user_understanding_section,
    mine_understanding,
    refresh_temporal,
)


class Phase7LongRunTests(unittest.TestCase):
    def test_evolving_understanding_over_many_interactions(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            knowledge = KnowledgeStore(db)
            understanding = UnderstandingStore(db)
            try:
                # Changing interest: coffee -> tea over time.
                for _ in range(3):
                    ledger.record("observation", "Surya prefers coffee.")
                mine_understanding(ledger, understanding)
                coffee = [r for r in understanding.visible() if "coffee" in r.statement]
                self.assertEqual(len(coffee), 1)
                self.assertEqual(len(coffee[0].source_ids), 3)  # affirmed, not duplicated

                for _ in range(2):
                    ledger.record("observation", "Surya switched from coffee to tea.")
                mine_understanding(ledger, understanding)

                history = understanding.history()
                coffee_records = [r for r in history if "coffee" in r.statement]
                tea_records = [r for r in history if "tea" in r.statement]
                self.assertTrue(coffee_records)
                self.assertTrue(tea_records)
                # Old preference no longer active.
                active = understanding.active()
                self.assertTrue(any("tea" in r.statement for r in active))
                self.assertFalse(any("prefers coffee" in r.statement for r in active))

                # Distinct topics do not pollute one another's retrieval.
                for i in range(5):
                    ledger.record("observation", f"Surya is working on homelab topic {i}.")
                mine_understanding(ledger, understanding)

                # Bounded context: retrieval returns only what is relevant.
                section = build_user_understanding_section(understanding, "tea", limit=3)
                self.assertLessEqual(section.count("\n"), 4)  # header + up to 3 lines
                self.assertNotIn("homelab topic 4", section)

            finally:
                ledger.close()
                knowledge.close()
                understanding.close()

    def test_contradictory_external_knowledge_preserved(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            knowledge = KnowledgeStore(db)
            try:
                a = ledger.record("source", "Retrieved A [web/REAL]",
                                  {"url": "a", "source_type": "web", "backend": "REAL",
                                   "retrieved_at": "2026-01-01T00:00:00+00:00"})
                b = ledger.record("source", "Retrieved B [web/REAL]",
                                  {"url": "b", "source_type": "web", "backend": "REAL",
                                   "retrieved_at": "2026-01-01T00:00:00+00:00"})
                first = knowledge.propose("Operators require a service account.", [a.id], 0.6,
                                          tags=["external", "web"])
                second = knowledge.contradict(first.id, "Operators do not require a service account.",
                                              [b.id], 0.6, reason="conflicting source",
                                              tags=["external", "web"])
                # Both sides remain explainable; nothing silently overwritten.
                self.assertEqual(knowledge.get(first.id).status, "contradicted")
                self.assertEqual(second.status, "active")
                explanation = knowledge.explain(first.id)
                self.assertTrue(explanation.contradicted_by)
            finally:
                ledger.close()
                knowledge.close()

    def test_stale_external_knowledge_marked_outdated(self) -> None:
        from datetime import UTC, datetime

        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            knowledge = KnowledgeStore(db)
            try:
                source = ledger.record("source", "Retrieved [web/REAL]",
                                       {"url": "x", "source_type": "web", "backend": "REAL",
                                        "retrieved_at": "2025-01-01T00:00:00+00:00"})
                record = knowledge.propose("The service is in beta.", [source.id], 0.6,
                                           tags=["external", "web"])
                marked = refresh_temporal(knowledge, max_age_days=30,
                                          now=datetime(2027, 1, 1, tzinfo=UTC))
                self.assertIn(record.id, marked)
                self.assertEqual(knowledge.get(record.id).status, "outdated")
                # Outdated stays in visible history, not deleted.
                self.assertIn(record.id, [r.id for r in knowledge.visible()])
            finally:
                ledger.close()
                knowledge.close()

    def test_semantic_retrieval_via_embedder(self) -> None:
        from suns_chan import HashingEmbedder

        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            knowledge = KnowledgeStore(db)
            try:
                source = ledger.record("source", "Retrieved [web/REAL]",
                                       {"url": "x", "source_type": "web", "backend": "REAL",
                                        "retrieved_at": "2026-01-01T00:00:00+00:00"})
                knowledge.propose("DNS resolution requires a resolver.", [source.id], 0.6,
                                  tags=["external"])
                embedder = HashingEmbedder()
                hits = knowledge.recall("domain name system lookup", embedder=embedder)
                self.assertTrue(hits)
            finally:
                ledger.close()
                knowledge.close()


if __name__ == "__main__":
    unittest.main()
