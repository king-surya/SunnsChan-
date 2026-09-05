from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    EventLedger,
    LearnedStore,
    compute_confidence,
    explain_learning,
    status_for,
)


def open_stores(tmp: str) -> tuple[EventLedger, LearnedStore]:
    db = Path(tmp) / "m.db"
    return EventLedger(db), LearnedStore(db)


def record(ledger: EventLedger, text: str, kind: str = "outcome", **meta) -> int:
    return ledger.record(kind, text, meta).id


class EvidenceTests(unittest.TestCase):
    def test_accumulation_needs_support(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, learned = open_stores(tmp)
            try:
                first = record(ledger, "Deploy checklist works.")
                obj = learned.observe("pattern", "pattern:success:checklist", first)
                self.assertEqual(obj.status, "candidate")
                self.assertLess(obj.confidence, 0.6)
                second = record(ledger, "Deploy checklist works again.")
                third = record(ledger, "Deploy checklist works a third time.")
                obj = learned.observe("pattern", "pattern:success:checklist", second)
                obj = learned.observe("pattern", "pattern:success:checklist", third)
                self.assertEqual(obj.status, "established")
                self.assertGreaterEqual(obj.confidence, 0.6)
                self.assertEqual(obj.support_count, 3)
                # Same evidence never double-counts.
                again = learned.observe("pattern", "pattern:success:checklist", third)
                self.assertEqual(again.support_count, 3)
            finally:
                ledger.close()
                learned.close()

    def test_confidence_is_transparent(self) -> None:
        breakdown = compute_confidence(6, 1, diversity=4, recent=True)
        self.assertGreaterEqual(breakdown.confidence, 0.7)
        self.assertLess(breakdown.confidence, 0.95)
        self.assertIn("6 supporting", breakdown.reason)
        self.assertIn("1 contradicting", breakdown.reason)
        weak = compute_confidence(1, 0)
        self.assertLess(weak.confidence, 0.6)
        with self.assertRaises(ValueError):
            compute_confidence(-1, 0)
        self.assertEqual(status_for(1, 0, 0.5), "candidate")
        self.assertEqual(status_for(4, 0, 0.7), "established")
        self.assertEqual(status_for(4, 3, 0.5), "uncertain")
        self.assertEqual(status_for(2, 3, 0.5), "contradicted")
        self.assertEqual(status_for(4, 2, 0.5), "uncertain")


class ContradictionTests(unittest.TestCase):
    def test_contradictions_reduce_confidence_and_status(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, learned = open_stores(tmp)
            try:
                ids = [record(ledger, f"Strategy X works, trial {i}.") for i in range(4)]
                for event_id in ids:
                    obj = learned.observe("strategy", "strategy:general:debug", event_id)
                self.assertEqual(obj.status, "established")
                peak = obj.confidence
                bad = record(ledger, "Strategy X failed badly.")
                obj = learned.observe("strategy", "strategy:general:debug", bad, contradicts=True)
                worse = record(ledger, "Strategy X failed again.")
                obj = learned.observe("strategy", "strategy:general:debug", worse, contradicts=True)
                self.assertLess(obj.confidence, peak)
                self.assertEqual(obj.status, "uncertain")
                self.assertEqual(obj.contra_count, 2)
                # Contradictions outnumbering support flip to contradicted.
                for i in range(3):
                    extra = record(ledger, f"Strategy X keeps failing ({i}).")
                    obj = learned.observe("strategy", "strategy:general:debug", extra, contradicts=True)
                self.assertEqual(obj.status, "contradicted")
            finally:
                ledger.close()
                learned.close()

    def test_explicit_correction_beats_weak_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, learned = open_stores(tmp)
            try:
                ids = [record(ledger, f"Prefers tea, sighting {i}.") for i in range(3)]
                for event_id in ids:
                    obj = learned.observe("preference", "prefers:tea", event_id)
                correction = record(ledger, "Correction: prefers coffee, not tea.")
                obj = learned.apply_correction(obj.id, correction, note="user correction")
                self.assertEqual(obj.status, "uncertain")
                self.assertIn(correction, obj.contradicting_ids)
            finally:
                ledger.close()
                learned.close()


class ProvenanceTests(unittest.TestCase):
    def test_explain_returns_full_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, learned = open_stores(tmp)
            try:
                first = record(ledger, "Checklist deploy succeeds.")
                second = record(ledger, "Checklist deploy succeeds again.")
                obj = learned.observe("pattern", "pattern:success:checklist", first,
                                      predicate="tends to success",
                                      conditions={"outcome": "success"})
                obj = learned.observe("pattern", "pattern:success:checklist", second)
                explanation = explain_learning(learned, ledger, obj.id)
                self.assertIn("pattern:success:checklist", explanation.what)
                self.assertEqual(explanation.confidence, obj.confidence)
                self.assertEqual({s[0] for s in explanation.supporting}, {first, second})
                self.assertTrue(all(kind == "outcome" for _, kind, _ in explanation.supporting))
                self.assertEqual(explanation.conditions["outcome"], "success")
                self.assertIsNotNone(explanation.last_observed)
            finally:
                ledger.close()
                learned.close()

    def test_secrets_never_enter_subjects(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, learned = open_stores(tmp)
            try:
                event = record(ledger, "Deploy with token: abc123.")
                obj = learned.observe("pattern", "deploy token: abc123", event)
                self.assertNotIn("abc123", obj.subject)
                self.assertIn("[REDACTED]", obj.subject)
            finally:
                ledger.close()
                learned.close()


if __name__ == "__main__":
    unittest.main()
