"""End-to-end Phase 7: accumulated context + new information + experience
-> changed future reasoning, not merely a grown database.

Deterministic (mocks, no network, no LLM): the scenario below walks the
mission's 14-step human-like loop and asserts each meaningful transition.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    CuriosityStore,
    EventLedger,
    KnowledgeStore,
    MockWebSource,
    UnderstandingStore,
    acquire,
    build_index,
    build_knowledge_section,
    build_user_understanding_section,
    consolidate,
    mine_understanding,
    record_experience,
    reflect,
)


class Phase7EndToEndTests(unittest.TestCase):
    def test_full_loop_changes_future_reasoning(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            knowledge = KnowledgeStore(db)
            understanding = UnderstandingStore(db)
            curiosities = CuriosityStore(db)
            try:
                # 1. Suns Chan already knows a little about Surya.
                seed = ledger.record("observation", "Surya is building a homelab.")
                understanding.note("project", "Surya is building a homelab.", [seed.id])

                # 2. Surya discusses a specific part of the project.
                project_talk = ledger.record(
                    "observation", "Surya says the homelab needs a Kubernetes operator.")

                # 3-4. A knowledge gap appears: no knowledge yet about operators.
                self.assertEqual(knowledge.recall("kubernetes operator"), [])

                # 5-6. External knowledge is acquired (mock web source).
                web = MockWebSource(pages={
                    "https://docs.example/operator": (
                        "Kubernetes operators manage custom resources and reconcile state.\n"
                        "Operators extend the control plane with domain knowledge."
                    ),
                })
                result = acquire(web, "https://docs.example/operator", ledger, knowledge,
                                curiosities=curiosities)
                self.assertEqual(result.status, "stored")
                self.assertTrue(result.knowledge_ids)

                # 7. The claim is external + BELIEF-capped, not fact.
                record = knowledge.get(result.knowledge_ids[0])
                self.assertLessEqual(record.confidence, 0.69)
                self.assertIn("external", record.tags)
                # Provenance is preserved.
                self.assertTrue(knowledge.provenance(record.id))
                self.assertEqual(knowledge.provenance(record.id)[0]["url"],
                                 "https://docs.example/operator")

                # 8-9. An experiment connects knowledge to lived experience.
                experiment = record_experience(
                    ledger, session_id="s1", activity_id="a1",
                    intent="Prototype a Kubernetes operator",
                    hypothesis="Operators reconcile state",
                    actions=["deploy operator"], observations=["controller reconciled"],
                    result="success", lessons=["operator pattern worked"],
                    knowledge_ids=[record.id])
                index = build_index(ledger, knowledge=knowledge, curiosities=curiosities)
                paths = index.query("experience", str(experiment.id), depth=2)
                self.assertTrue(any("confirms" in p.relations for p in paths))

                # 10. Reflection + consolidation + understanding update.
                reflect(ledger)
                consolidate(ledger, knowledge, budget=5)
                mined = mine_understanding(ledger, understanding)
                self.assertTrue(mined)  # project discussion became understanding

                # 11-14. Future reasoning draws on the accumulated context.
                context_text = build_knowledge_section(knowledge, "kubernetes operator")
                self.assertIn("Relevant knowledge:", context_text)
                self.assertIn("operator", context_text)

                understanding_text = build_user_understanding_section(
                    understanding, "homelab kubernetes")
                self.assertIn("What I know about you:", understanding_text)
                self.assertIn("homelab", understanding_text)

                # Understanding evolved from this interaction, not just facts stored.
                statements = {r.statement for r in understanding.visible()}
                self.assertTrue(any("homelab" in s for s in statements))
            finally:
                ledger.close()
                knowledge.close()
                understanding.close()
                curiosities.close()

    def test_restart_persistence(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            knowledge = KnowledgeStore(db)
            understanding = UnderstandingStore(db)
            ev = ledger.record("observation", "Surya prefers rust.")
            understanding.note("preference", "Surya prefers rust.", [ev.id])
            web = MockWebSource(pages={"https://d.example/r": "Rust has no garbage collector."})
            acquire(web, "https://d.example/r", ledger, knowledge)
            ledger.close()
            knowledge.close()
            understanding.close()

            # Reopen: everything must survive.
            ledger2 = EventLedger(db)
            knowledge2 = KnowledgeStore(db)
            understanding2 = UnderstandingStore(db)
            try:
                self.assertEqual(len(understanding2.visible()), 1)
                self.assertEqual(understanding2.visible()[0].statement, "Surya prefers rust.")
                self.assertTrue(knowledge2.recall("rust"))
                self.assertTrue(knowledge2.provenance(knowledge2.recall("rust")[0].id))
            finally:
                ledger2.close()
                knowledge2.close()
                understanding2.close()

    def test_offline_degrades_without_destroying_cognition(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            knowledge = KnowledgeStore(db)
            understanding = UnderstandingStore(db)
            try:
                ev = ledger.record("observation", "Surya likes driving.")
                understanding.note("preference", "Surya likes driving.", [ev.id])

                class Down(MockWebSource):
                    def fetch(self, url):
                        raise RuntimeError("network down")

                result = acquire(Down(), "https://x.example/y", ledger, knowledge)
                self.assertEqual(result.status, "failed")
                # Local understanding still answers.
                self.assertTrue(build_user_understanding_section(understanding, "driving"))
            finally:
                ledger.close()
                knowledge.close()
                understanding.close()


if __name__ == "__main__":
    unittest.main()
