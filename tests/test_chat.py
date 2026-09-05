from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    AgentCore,
    Decision,
    EchoProvider,
    EventLedger,
    IdentitySeed,
    PolicyGate,
    build_chat_decide,
    handle_command,
    inspect_audit,
    inspect_state,
)
from suns_chan.chat import build_system_prompt, build_user_prompt
from suns_chan.core import TurnContext
from suns_chan.state import AgentState


PROJECT = Path(__file__).resolve().parents[1]


def make_context() -> TurnContext:
    return TurnContext(
        observation="hello",
        recalled_events=[],
        identity=IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"),
        state=AgentState(),
        active_goals=[],
    )


class ChatDecideTests(unittest.TestCase):
    def test_echo_provider_yields_valid_decision(self) -> None:
        decide = build_chat_decide(EchoProvider(model="fake-echo"))
        decision = decide(make_context())
        self.assertIsInstance(decision, Decision)
        self.assertTrue(decision.response)

    def test_failing_provider_returns_visible_error(self) -> None:
        class Broken:
            kind = "broken"

            def generate(self, request):
                raise RuntimeError("server down")

        decision = build_chat_decide(Broken())(make_context())  # type: ignore[arg-type]
        self.assertIn("provider error", decision.response)
        self.assertIsNone(decision.action)

    def test_prompts_carry_identity_and_memory(self) -> None:
        ctx = make_context()
        system = build_system_prompt(ctx)
        self.assertIn("Suns Chan", system)
        self.assertIn("decision schema", system)
        user = build_user_prompt(ctx)
        self.assertIn("hello", user)


class InspectorTests(unittest.TestCase):
    def test_commands_and_inspectors(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = EventLedger(Path(tmp) / "m.db")
            try:
                ledger.record("observation", "Surya likes rendang")
                core = AgentCore(
                    ledger,
                    PolicyGate(),
                    IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"),
                )
                core.turn("hello", lambda ctx: Decision("hi"))
                handled, out = handle_command(ledger, "/recall rendang")
                self.assertTrue(handled)
                self.assertIn("rendang", out)
                for cmd in ("/audit", "/state", "/goals"):
                    handled, out = handle_command(ledger, cmd)
                    self.assertTrue(handled)
                    self.assertTrue(out)
                self.assertIn("decisions=1", inspect_audit(ledger))
                self.assertIn("curiosity", inspect_state(ledger))
                handled, out = handle_command(ledger, "/quit")
                self.assertEqual(out, "__quit__")
                handled, _ = handle_command(ledger, "just chat")
                self.assertFalse(handled)
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
