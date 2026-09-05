from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from suns_chan import EventLedger, HashingEmbedder


class HybridRecallTests(unittest.TestCase):
    def open_ledger(self, tmp: str) -> EventLedger:
        return EventLedger(Path(tmp) / "m.db")

    def test_kinds_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self.open_ledger(tmp)
            try:
                ledger.record("observation", "nginx stopped unexpectedly")
                ledger.record("goal_opened", "nginx recovery plan")
                hits = ledger.recall("nginx", kinds=("goal_opened",))
                self.assertTrue(hits)
                self.assertTrue(all(e.kind == "goal_opened" for e in hits))
            finally:
                ledger.close()

    def test_metadata_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self.open_ledger(tmp)
            try:
                ledger.record("observation", "backup finished", {"host": "atlas"})
                ledger.record("observation", "backup finished", {"host": "vega"})
                hits = ledger.recall("backup finished", metadata_filter={"host": "vega"})
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].metadata["host"], "vega")
            finally:
                ledger.close()

    def test_importance_breaks_ties(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self.open_ledger(tmp)
            try:
                ledger.record("observation", "routine log line alpha")
                ledger.record("observation", "routine log line alpha", {"importance": 0.95})
                hits = ledger.recall("routine log alpha")
                self.assertEqual(hits[0].metadata.get("importance"), 0.95)
            finally:
                ledger.close()

    def test_semantic_signal_with_hashing_embedder(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self.open_ledger(tmp)
            try:
                ledger.record("observation", "the web server stopped responding")
                ledger.record("observation", "cooking rendang takes patience")
                hits = ledger.recall("nginx daemon halted", embedder=HashingEmbedder())
                self.assertTrue(hits)
                scored = ledger.recall_with_scores("nginx daemon halted", embedder=HashingEmbedder())
                self.assertTrue(all(item is not None for _, item in scored))
                self.assertIn("semantic", scored[0][1].contributions)
            finally:
                ledger.close()

    def test_scores_explain_ranking(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self.open_ledger(tmp)
            try:
                ledger.record("observation", "nginx stopped after memory pressure")
                scored = ledger.recall_with_scores("why did nginx stop?")
                event, explanation = scored[0]
                self.assertIn("nginx", event.text)
                self.assertAlmostEqual(explanation.score, sum(explanation.contributions.values()), places=6)
            finally:
                ledger.close()

    def test_retrieval_latency_is_practical(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = self.open_ledger(tmp)
            try:
                for i in range(200):
                    ledger.record("observation", f"routine event number {i} about servers")
                started = time.perf_counter()
                hits = ledger.recall("servers", limit=8)
                elapsed = time.perf_counter() - started
                self.assertEqual(len(hits), 8)
                self.assertLess(elapsed, 5.0, f"recall took {elapsed:.2f}s for 200 events")
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
