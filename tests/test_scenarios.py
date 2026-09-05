"""Phase 1 repeatable scenarios: correction, forgotten fact, failed experiment,
changing interest, conflicting memories — plus the Phase 1 exit test.

Exit test: a later conversation demonstrably recalls a source event and
updates a belief/state after a recorded contradiction.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    AgentCore,
    Decision,
    EventLedger,
    GoalProposal,
    IdentitySeed,
    KnowledgeStore,
    PolicyGate,
    SandboxRequest,
    consolidate,
)


PROJECT = Path(__file__).resolve().parents[1]


def make_core(ledger: EventLedger) -> AgentCore:
    return AgentCore(ledger, PolicyGate(), IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"))


def open_pair(tmp: str) -> tuple[EventLedger, KnowledgeStore, AgentCore]:
    db = Path(tmp) / "m.db"
    ledger = EventLedger(db)
    store = KnowledgeStore(db)
    return ledger, store, make_core(ledger)


class ScenarioTests(unittest.TestCase):
    def test_correction_updates_belief(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, core = open_pair(tmp)
            try:
                first = ledger.record("observation", "Surya prefers tea in the morning.")
                old = store.propose("Surya prefers tea in the morning.", [first.id], 0.6)
                correction = ledger.record("observation", "Correction: Surya switched to coffee in the morning.")
                new = store.supersede(
                    old.id, "Surya prefers coffee in the morning.", [correction.id], 0.7,
                    reason="user correction",
                )
                self.assertEqual(store.get(old.id).status, "superseded")
                self.assertEqual(new.source_ids, (correction.id,))
                self.assertIn("coffee", store.active()[0].statement)
            finally:
                ledger.close()
                store.close()

    def test_forgotten_fact_is_recalled_later(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, core = open_pair(tmp)
            try:
                core.turn("Remember: the homelab Proxmox node is called pve-01.", lambda ctx: Decision("Noted."))
                hits = ledger.recall("What is the Proxmox node called?")
                self.assertTrue(any("pve-01" in e.text for e in hits))
            finally:
                ledger.close()
                store.close()

    def test_failed_experiment_leaves_learning_trail(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, core = open_pair(tmp)
            try:
                before = ledger.load_state()["confidence"] if ledger.load_state() else 0.5
                decision, _ = core.autonomous_turn(
                    "Try a driving simulator install in the sandbox.",
                    lambda ctx: Decision(
                        "Trying simulator install.",
                        SandboxRequest("sandbox.install", "Install sim", "lab-01").to_action_request(),
                        goals=(GoalProposal("Sim experiment", "curiosity", 0.6),),
                        interest_tags=("driving",),
                    ),
                )
                decision_event = ledger.events_of_kind("decision")[0]
                core.record_outcome(decision_event.id, "failure", "Incompatible dependency.", interest_tags=("driving",))
                self.assertEqual(len(ledger.events_of_kind("learning")), 1)
                self.assertLess(ledger.load_state()["confidence"], before)
                result = consolidate(ledger, store, budget=10)
                self.assertGreater(result.created, 0)
            finally:
                ledger.close()
                store.close()

    def test_changing_interest_is_tracked(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, core = open_pair(tmp)
            try:
                core.turn("Cooking rendang today.", lambda ctx: Decision("Nice.", interest_tags=("cooking",)))
                first_level = ledger.load_state()["interests"]["cooking"]
                core.turn("Cooking rendang again.", lambda ctx: Decision("Again!", interest_tags=("cooking",)))
                self.assertGreater(ledger.load_state()["interests"]["cooking"], first_level)
            finally:
                ledger.close()
                store.close()

    def test_exit_contradiction_recalls_source_and_updates_belief(self) -> None:
        """Phase 1 exit test."""
        with TemporaryDirectory() as tmp:
            ledger, store, core = open_pair(tmp)
            try:
                original = ledger.record("observation", "The backup service runs on host atlas.")
                store.propose("Backups run on host atlas.", [original.id], 0.6)
                contradiction = ledger.record(
                    "observation", "Contradiction: backups moved from atlas to host vega."
                )
                # A later conversation demonstrably recalls the source event...
                recalled = ledger.recall("Which host runs the backup service?")
                recalled_ids = {e.id for e in recalled}
                self.assertIn(contradiction.id, recalled_ids)
                # ...and the belief/state is updated with source linkage.
                old = store.active()[0]
                new = store.supersede(
                    old.id, "Backups run on host vega.", [contradiction.id], 0.75,
                    reason="contradicted by later observation",
                )
                claims = store.recall("backup host")
                self.assertIn("vega", claims[0].statement)
                self.assertEqual(claims[0].source_ids, (contradiction.id,))
                self.assertEqual(new.supersedes_id, old.id)
                self.assertEqual(len(ledger.events_of_kind("observation")), 2)
            finally:
                ledger.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
