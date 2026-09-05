from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    ActionRequest,
    ActionVerdict,
    AgentCore,
    Decision,
    EventLedger,
    GoalProposal,
    IdentitySeed,
    PolicyGate,
    SandboxRequest,
)


PROJECT = Path(__file__).resolve().parents[1]


def identity() -> IdentitySeed:
    return IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json")


class CoreTests(unittest.TestCase):
    def test_recall_prefers_matching_events(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger = EventLedger(Path(temporary) / "memory.db")
            ledger.record("observation", "nginx service stopped after memory pressure")
            ledger.record("observation", "calendar reminder was sent")
            events = ledger.recall("Why did nginx stop?")
            self.assertIn("nginx", events[0].text)
            ledger.close()

    def test_write_requires_approval_and_turn_is_logged(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger = EventLedger(Path(temporary) / "memory.db")
            core = AgentCore(ledger, PolicyGate(), identity())
            decision, verdict = core.turn(
                "Disk is almost full.",
                lambda _: Decision("I can inspect first.", ActionRequest("clear_cache", "write", "Clear cache")),
            )
            self.assertEqual(decision.response, "I can inspect first.")
            self.assertEqual(verdict, ActionVerdict.REQUIRE_APPROVAL)
            self.assertGreaterEqual(len(ledger.recall("disk cache")), 2)
            ledger.close()

    def test_destructive_actions_are_denied(self) -> None:
        gate = PolicyGate(approver=lambda _: True)
        verdict = gate.evaluate(ActionRequest("delete_all", "destructive", "Delete all data"))
        self.assertEqual(verdict, ActionVerdict.DENY)

    def test_autonomous_goal_and_failure_change_state_with_audit_trail(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger = EventLedger(Path(temporary) / "memory.db")
            core = AgentCore(ledger, PolicyGate(), identity())
            decision, verdict = core.autonomous_turn(
                "I have spare sandbox time and want to understand safe driving simulations.",
                lambda _: Decision(
                    "I'll study a driving simulation in the sandbox.",
                    SandboxRequest("sandbox.install", "Install a simulator dependency", "lab-01").to_action_request(),
                    goals=(GoalProposal("Explore driving simulation", "Curiosity about driving", 0.7),),
                    interest_tags=("driving", "simulation"),
                ),
            )
            self.assertEqual(verdict, ActionVerdict.ALLOW)
            self.assertEqual(decision.action.scope, "sandbox")
            opened_goal = ledger.events_of_kind("goal_opened")[0]
            before_failure = ledger.load_state()["confidence"]
            decision_event = ledger.events_of_kind("decision")[0]
            core.record_outcome(decision_event.id, "failure", "The dependency was incompatible; inspect constraints first.", interest_tags=("driving",))
            state = ledger.load_state()
            self.assertLess(state["confidence"], before_failure)
            self.assertGreater(state["interests"]["driving"], 0.30)
            self.assertEqual(ledger.events_of_kind("learning")[0].metadata["decision_id"], decision_event.id)
            core.close_goal(opened_goal.id, "Experiment paused after incompatible dependency.")
            self.assertEqual(core._active_goals(), [])
            ledger.close()

    def test_external_install_still_requires_approval(self) -> None:
        request = ActionRequest("pip.install", "install", "Install an agent package", scope="external")
        self.assertEqual(PolicyGate().evaluate(request), ActionVerdict.REQUIRE_APPROVAL)


if __name__ == "__main__":
    unittest.main()
