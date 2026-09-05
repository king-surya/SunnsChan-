from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    ActionRequest,
    AgentCore,
    AgentRuntime,
    ChatRequest,
    Database,
    Decision,
    EchoProvider,
    EventBus,
    EventLedger,
    FakeEnvironment,
    IdentitySeed,
    LLMRequest,
    LLMMessage,
    PolicyGate,
    Settings,
    Task,
    default_registry,
    health_check,
    provider_from_settings,
    route_table,
    user_message_received,
)


PROJECT = Path(__file__).resolve().parents[1]


class FoundationTests(unittest.TestCase):
    def test_settings_defaults_and_env_overlay(self) -> None:
        import os

        s = Settings.load()
        self.assertEqual(s.llm.provider, "fake")
        os.environ["SUNS_LLM_MODEL"] = "test-model"
        try:
            self.assertEqual(Settings.load().llm.model, "test-model")
        finally:
            del os.environ["SUNS_LLM_MODEL"]

    def test_sandbox_settings_safe_defaults(self) -> None:
        import os

        s = Settings.load()
        self.assertEqual(s.sandbox.mode, "mock")
        self.assertEqual(s.sandbox.network_mode, "isolated")
        for key in ("PROXMOX_HOST", "PROXMOX_USER", "PROXMOX_TOKEN_NAME", "PROXMOX_TOKEN_VALUE"):
            self.assertNotIn(key, os.environ, "test env must not carry real credentials")
        self.assertFalse(s.sandbox.proxmox_configured)

    def test_database_abstraction(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "app.db")
            db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            db.execute("INSERT INTO t (v) VALUES (?)", ("hello",))
            rows = db.query("SELECT v FROM t")
            self.assertEqual(rows[0]["v"], "hello")
            db.close()

    def test_event_bus_pubsub(self) -> None:
        bus = EventBus()
        seen: list = []
        bus.subscribe("UserMessageReceived", seen.append)
        errors = bus.publish(user_message_received("hi"))
        self.assertEqual(errors, [])
        self.assertEqual(len(bus.history("UserMessageReceived")), 1)
        self.assertEqual(len(seen), 1)

    def test_llm_provider_boundary(self) -> None:
        provider = provider_from_settings("fake", "fake-echo")
        resp = provider.generate(LLMRequest(messages=(LLMMessage("user", "hello"),)))
        self.assertIn("hello", resp.text)
        self.assertIsInstance(EchoProvider().generate(
            LLMRequest(messages=(LLMMessage("user", "x"),), json_mode=True)
        ).text, str)
        with self.assertRaises(ValueError):
            provider_from_settings("nope-unknown", "x")

    def test_tool_registry_safe_defaults(self) -> None:
        audits: list = []
        registry = default_registry(auditor=lambda n, a, r: audits.append(n))
        ok = registry.execute("echo", {"text": "hi"}, approved=False)
        self.assertTrue(ok.ok)
        bad = registry.execute("echo", {}, approved=False)
        self.assertFalse(bad.ok)
        unknown = registry.execute("shell.rm_rf", {}, approved=True)
        self.assertFalse(unknown.ok)
        self.assertGreaterEqual(len(audits), 2)

    def test_runtime_skeleton_runs_without_tools(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = EventLedger(Path(tmp) / "m.db")
            core = AgentCore(ledger, PolicyGate(), IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"))
            runtime = AgentRuntime(core, PolicyGate(), default_registry(), FakeEnvironment())
            result = runtime.run_task(Task(title="say hi", max_steps=2), lambda ctx: Decision("hello"))
            self.assertEqual(result.outcome, "success")
            self.assertIn("hello", result.response)
            self.assertGreater(len(result.steps), 3)
            ledger.close()

    def test_runtime_steps_are_persisted_in_ledger(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = EventLedger(Path(tmp) / "m.db")
            core = AgentCore(ledger, PolicyGate(), IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"))
            runtime = AgentRuntime(core, PolicyGate(), default_registry(), FakeEnvironment())
            result = runtime.run_task(Task(title="persist me", max_steps=2), lambda ctx: Decision("done"))
            self.assertEqual(result.outcome, "success")
            kinds = [e.kind for e in ledger.recall("persist me", limit=10)]
            self.assertIn("observation", kinds)
            self.assertIn("decision", kinds)
            ledger.close()

    def test_runtime_blocks_unapproved_action_names(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = EventLedger(Path(tmp) / "m.db")
            core = AgentCore(ledger, PolicyGate(), IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"))
            runtime = AgentRuntime(core, PolicyGate(), default_registry(), FakeEnvironment())
            result = runtime.run_task(
                Task(title="do write", max_steps=1),
                lambda ctx: Decision("trying", ActionRequest("do_write", "write", "write outside sandbox")),
            )
            self.assertEqual(result.outcome, "failure")
            self.assertIn("Blocked by policy", result.response)
            ledger.close()

    def test_api_boundary_schemas(self) -> None:
        with self.assertRaises(ValueError):
            ChatRequest("")
        self.assertIn("POST /chat", route_table())
        self.assertIn("GET /health", route_table())
        self.assertEqual(health_check()["status"], "ok")

    def test_fake_environment_is_readonly(self) -> None:
        env = FakeEnvironment()
        self.assertEqual(env.observe().summary, "fake environment nominal")
        self.assertEqual(env.poll_events(), [])


if __name__ == "__main__":
    unittest.main()
