from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    AgentCore,
    AgentRuntime,
    Decision,
    DecisionValidationError,
    EventBus,
    EventLedger,
    FakeEnvironment,
    IdentitySeed,
    PolicyGate,
    Task,
    default_registry,
    parse_decision,
    parse_decision_json,
)


PROJECT = Path(__file__).resolve().parents[1]


def identity() -> IdentitySeed:
    return IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json")


class SchemaTests(unittest.TestCase):
    def test_valid_full_decision(self) -> None:
        decision = parse_decision_json(
            '{"response": "hi", "action": {"name": "echo", "risk": "read_only",'
            ' "description": "say hi"}, "goals": [{"title": "G", "reason": "why",'
            ' "priority": 0.7}], "interest_tags": ["driving"], "confidence": 0.8,'
            ' "expected_outcome": "user smiles"}'
        )
        self.assertEqual(decision.response, "hi")
        self.assertEqual(decision.action.name, "echo")
        self.assertEqual(decision.goals[0].priority, 0.7)
        self.assertEqual(decision.interest_tags, ("driving",))
        self.assertEqual(decision.confidence, 0.8)

    def test_minimal_decision_gets_defaults(self) -> None:
        decision = parse_decision({"response": "hello"})
        self.assertIsNone(decision.action)
        self.assertEqual(decision.goals, ())
        self.assertEqual(decision.confidence, 0.5)

    def test_rejects_malformed_input(self) -> None:
        with self.assertRaises(DecisionValidationError):
            parse_decision_json("not json {")
        with self.assertRaises(DecisionValidationError):
            parse_decision({})
        with self.assertRaises(DecisionValidationError):
            parse_decision({"response": "  "})
        with self.assertRaises(DecisionValidationError):
            parse_decision({"response": "x", "confidence": 1.5})
        with self.assertRaises(DecisionValidationError):
            parse_decision({"response": "x", "goals": [{"title": "G", "priority": 9}]})
        with self.assertRaises(DecisionValidationError):
            parse_decision({"response": "x", "interest_tags": "driving"})
        with self.assertRaises(DecisionValidationError):
            parse_decision({"response": "x", "action": {"name": "a"}})


class InvalidDecisionTests(unittest.TestCase):
    def test_invalid_provider_output_becomes_event_never_action(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = EventLedger(Path(tmp) / "m.db")
            core = AgentCore(ledger, PolicyGate(), identity())

            def bad_decide(ctx) -> Decision:
                return parse_decision_json('{"nope": true}')

            with self.assertRaises(DecisionValidationError):
                core.turn("hello", bad_decide)
            invalid = ledger.events_of_kind("invalid_decision")
            self.assertEqual(len(invalid), 1)
            self.assertEqual(ledger.events_of_kind("decision"), [])
            ledger.close()

    def test_runtime_marks_invalid_decision_as_failure_without_tools(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = EventLedger(Path(tmp) / "m.db")
            core = AgentCore(ledger, PolicyGate(), identity())
            bus = EventBus()
            runtime = AgentRuntime(core, PolicyGate(), default_registry(), FakeEnvironment(), bus)

            def bad_decide(ctx) -> Decision:
                raise DecisionValidationError("boom")

            result = runtime.run_task(Task(title="broken", max_steps=2), bad_decide)
            self.assertEqual(result.outcome, "failure")
            self.assertIn("Invalid decision", result.response)
            self.assertEqual(bus.history("ToolExecuted"), [])
            self.assertEqual(len(ledger.events_of_kind("invalid_decision")), 1)
            ledger.close()


if __name__ == "__main__":
    unittest.main()
