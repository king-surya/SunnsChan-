import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    EventLedger,
    MockPublicSource,
    UnderstandingStore,
    discover_public,
    evaluate_identity,
)


class IdentityEvaluationTests(unittest.TestCase):
    def test_bare_name_is_not_certainty(self) -> None:
        evaluation = evaluate_identity({"name": "Surya", "title": "some unrelated blog"})
        self.assertFalse(evaluation.matched)
        self.assertEqual(evaluation.confidence, 0.0)
        self.assertEqual(evaluation.kind, "uncertainty")

    def test_username_match_is_strong(self) -> None:
        evaluation = evaluate_identity(
            {"name": "Surya", "username": "surya-dev", "title": "homelab automation"},
            known_usernames=("surya-dev",),
        )
        self.assertTrue(evaluation.matched)
        self.assertGreaterEqual(evaluation.confidence, 0.45)
        self.assertLessEqual(evaluation.confidence, 0.85)

    def test_project_overlap_raises_confidence(self) -> None:
        evaluation = evaluate_identity(
            {"name": "Surya", "title": "Suns Chan autonomous agent"},
            known_projects=("suns-chan",),
        )
        self.assertTrue(evaluation.matched)
        self.assertEqual(evaluation.kind, "inference")
        self.assertLess(evaluation.confidence, 0.6)


class DiscoverPublicTests(unittest.TestCase):
    def test_strong_match_recorded_with_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            store = UnderstandingStore(db)
            source = MockPublicSource(records={
                "surya homelab": [{
                    "username": "surya-dev",
                    "title": "Homelab automation scripts",
                    "description": "Proxmox and networking",
                    "url": "https://github.com/surya-dev/homelab",
                }],
            })
            try:
                result = discover_public(
                    "surya homelab", source, ledger, store,
                    known_usernames=("surya-dev",),
                    known_terms=("proxmox", "homelab", "automation"))
                self.assertEqual(result.recorded, 1)
                records = store.visible()
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].kind, "identity")
                self.assertIn("public", records[0].tags)
                self.assertLessEqual(records[0].confidence, 0.85)
                # Provenance resolved from the source event.
                explanation = store.explain(records[0].id)
                self.assertTrue(explanation.supporting)
                self.assertEqual(explanation.supporting[0].kind, "source")
            finally:
                ledger.close()
                store.close()

    def test_weak_match_never_becomes_certainty(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            store = UnderstandingStore(db)
            source = MockPublicSource(records={
                "surya": [{"name": "Surya", "title": "unrelated cooking blog"}],
            })
            try:
                result = discover_public("surya", source, ledger, store)
                self.assertEqual(result.recorded, 0)
                self.assertEqual(result.weak_skipped, 1)
                self.assertEqual(store.visible(), [])
            finally:
                ledger.close()
                store.close()

    def test_inference_kind_for_plausible_but_unproven(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            store = UnderstandingStore(db)
            source = MockPublicSource(records={
                "surya": [{"name": "Surya", "title": "suns-chan autonomous companion",
                           "description": "persistent agent"}],
            })
            try:
                result = discover_public(
                    "surya", source, ledger, store,
                    known_projects=("suns-chan",))
                self.assertEqual(result.recorded, 1)
                record = store.visible()[0]
                self.assertEqual(record.kind, "inference")
                self.assertLessEqual(record.confidence, 0.55)
            finally:
                ledger.close()
                store.close()

    def test_search_failure_degrades_to_event(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            store = UnderstandingStore(db)

            class BrokenSource(MockPublicSource):
                def search(self, query, *, limit=5):
                    raise RuntimeError("offline")

            try:
                result = discover_public("x", BrokenSource(), ledger, store)
                self.assertEqual(result.recorded, 0)
                self.assertTrue(ledger.events_of_kind("source_failed"))
                self.assertEqual(store.visible(), [])
            finally:
                ledger.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
