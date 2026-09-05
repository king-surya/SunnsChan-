from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    ActionRequest,
    ActionVerdict,
    CuriosityStore,
    EventLedger,
    PolicyGate,
)
from suns_chan.affect import (
    AffectEvidence,
    apply_affect,
    attention_weights,
    collect_evidence,
    expression_note,
    selection_bias,
    update_affect,
)
from suns_chan.state import AgentState


def open_ledger(tmp: str) -> EventLedger:
    return EventLedger(Path(tmp) / "m.db")


class AffectTests(unittest.TestCase):
    def test_evidence_driven_updates(self) -> None:
        state = AgentState()
        before_confidence = state.confidence
        deltas = update_affect(state, AffectEvidence(
            reflection_types=("lesson", "open_question"),
            recent_outcomes=("failure", "failure", "failure"),
            new_curiosities=2, activities_this_session=8))
        self.assertLess(state.confidence, before_confidence)
        self.assertGreater(state.stress, 0.28)
        self.assertGreater(state.curiosity, 0.78)
        self.assertEqual(state.rhythm, "slow")
        self.assertTrue(all(d.reason for d in deltas))
        # Single failure is not enough for the streak rules.
        calm = AgentState()
        update_affect(calm, AffectEvidence(recent_outcomes=("failure",)))
        self.assertEqual(calm.confidence, 0.5)

    def test_apply_persists_with_audit(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                origin = ledger.record("observation", "Why so slow?")
                store = CuriosityStore(Path(tmp) / "m.db")
                try:
                    store.open("Why so slow?", origin.id)
                    evidence = collect_evidence(ledger, curiosities=store)
                    self.assertEqual(evidence.new_curiosities, 1)
                    deltas = apply_affect(ledger, AffectEvidence(
                        reflection_types=evidence.reflection_types,
                        recent_outcomes=("failure", "failure", "failure"),
                        new_curiosities=evidence.new_curiosities,
                        activities_this_session=0))
                    self.assertTrue(deltas)
                    anchors = ledger.events_of_kind("affect")
                    self.assertEqual(len(anchors), 1)
                    self.assertLess(ledger.load_state()["confidence"], 0.5)
                finally:
                    store.close()
            finally:
                ledger.close()

    def test_attention_and_expression(self) -> None:
        curious = AgentState(curiosity=0.95, energy=0.9, confidence=0.3)
        weights = attention_weights(curious)
        self.assertAlmostEqual(weights.explore + weights.exploit + weights.rest, 1.0, places=3)
        self.assertGreater(weights.explore, weights.exploit)
        bias = selection_bias(weights)
        self.assertGreater(bias["novelty"], 0.0)
        self.assertLess(bias["relevance"], 0.05)
        note = expression_note(curious)
        self.assertIn("attention=explore", note)

    def test_affect_never_authorizes(self) -> None:
        gate = PolicyGate()
        for state in (AgentState(confidence=1.0, energy=1.0),
                      AgentState(confidence=0.0, stress=1.0, energy=0.0)):
            self.assertEqual(gate.evaluate(ActionRequest("x", "destructive", "x")), ActionVerdict.DENY)
            self.assertEqual(
                gate.evaluate(ActionRequest("cmd", "command", "run", scope="external")),
                ActionVerdict.REQUIRE_APPROVAL)
        # Attention bias stays within ±0.1 and never touches policy inputs.
        extreme = selection_bias(attention_weights(AgentState()))
        self.assertTrue(all(abs(v) <= 0.1 for v in extreme.values()))


if __name__ == "__main__":
    unittest.main()
