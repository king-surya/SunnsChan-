"""End-to-end sandbox tests on the mock backend + host-boundary proofs."""

import ast
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    AgentCore,
    Decision,
    EventLedger,
    IdentitySeed,
    KnowledgeStore,
    MockSandboxProvider,
    PolicyGate,
    SandboxController,
    consolidate,
    reflect,
    validate_proposal,
)


PROJECT = Path(__file__).resolve().parents[1]


def open_all(tmp: str) -> tuple[EventLedger, KnowledgeStore, AgentCore]:
    db = Path(tmp) / "m.db"
    ledger = EventLedger(db)
    store = KnowledgeStore(db)
    core = AgentCore(ledger, PolicyGate(), IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"))
    return ledger, store, core


class RealisticExperimentTests(unittest.TestCase):
    def test_create_file_and_inspect_end_to_end(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, core = open_all(tmp)
            try:
                before = set(Path(PROJECT).iterdir())
                controller = SandboxController(MockSandboxProvider(), template="debian-12-baseline")
                proposal = validate_proposal({
                    "goal": "Create a small file inside the sandbox and inspect it.",
                    "hypothesis": "Guest file writes persist for the experiment lifetime.",
                    "operations": ["write /out/note.txt hello-sandbox", "echo inspected"],
                    "expected_outcome": "note.txt exists with known content",
                    "collect_paths": ["/out/note.txt"],
                })
                record = controller.run_experiment(proposal, ledger=ledger)
                self.assertEqual(record.status, "completed")
                self.assertEqual(record.outcome, "success")
                # File exists inside sandbox scope: artifact recorded with hash.
                self.assertEqual(len(record.artifacts), 1)
                artifact = record.artifacts[0]
                self.assertEqual(artifact.path, "/out/note.txt")
                self.assertEqual(len(artifact.sha256), 64)
                # Host project unchanged.
                self.assertEqual(set(Path(PROJECT).iterdir()), before)
                # Outcome stored and explainable afterwards.
                kinds = {e.kind for e in ledger.recall("sandbox marker note")}
                self.assertIn("experiment", kinds)
                self.assertTrue(ledger.events_of_kind("outcome"))
            finally:
                ledger.close()
                store.close()

    def test_failed_experiment_influences_later_decision(self) -> None:
        """Exit condition: fail safely, leave evidence, change a later decision."""
        with TemporaryDirectory() as tmp:
            ledger, store, core = open_all(tmp)
            try:
                controller = SandboxController(MockSandboxProvider(), template="t")
                proposal = validate_proposal({
                    "goal": "Try the fragile deploy.",
                    "hypothesis": "Deploy works without the checklist.",
                    "operations": ["echo trying"],
                    "expected_outcome": "deployed",
                })
                provider = controller._provider
                original = provider.execute

                def fail(sandbox_id: str, command: str, timeout_secs: float = 120.0):
                    from suns_chan.vm_sandbox import BACKEND_MOCK, GuestResult

                    return GuestResult(command, 1, "", "deploy failed: checklist skipped", 0.0, BACKEND_MOCK)

                provider.execute = fail  # type: ignore[method-assign]
                record = controller.run_experiment(proposal, ledger=ledger)
                self.assertEqual(record.outcome, "failure")
                report = reflect(ledger)
                self.assertTrue(report.findings)
                consolidate(ledger, store, budget=10)
                # A later decision demonstrably sees the lesson in context.
                seen: list = []

                def decide(ctx) -> Decision:
                    seen.extend(e.text for e in ctx.recalled_events)
                    return Decision("Will use the checklist this time.")

                core.turn("Should I deploy again?", decide)
                self.assertTrue(any("checklist" in text for text in seen),
                                "lesson did not reach the later decision")
            finally:
                ledger.close()
                store.close()


class HostBoundaryTests(unittest.TestCase):
    def test_guest_cannot_touch_host(self) -> None:
        with TemporaryDirectory() as tmp:
            marker = Path(tmp) / "host-marker.txt"
            marker.write_text("untouched")
            provider = MockSandboxProvider()
            sandbox_id = provider.create(__import__("suns_chan").VMSpec("lab", "t"))
            provider.start(sandbox_id)
            result = provider.execute(sandbox_id, f"cat {marker} && rm {marker}")
            self.assertEqual(result.backend, "MOCK")
            self.assertEqual(marker.read_text(), "untouched")  # host file intact

    def test_credentials_never_reach_audit_or_ledger(self) -> None:
        import os
        from unittest.mock import patch

        with TemporaryDirectory() as tmp:
            ledger, store, _ = open_all(tmp)
            try:
                audits: list = []
                controller = SandboxController(MockSandboxProvider(), template="t",
                                               audit_sink=audits.append)
                with patch.dict(os.environ, {"PROXMOX_TOKEN_VALUE": "PLANTED-SECRET-XYZ"}):
                    record = controller.run_experiment(validate_proposal({
                        "goal": "g", "hypothesis": "h", "operations": ["echo x"],
                        "expected_outcome": "o",
                    }), ledger=ledger)
                self.assertEqual(record.status, "completed")
                blob = str(audits) + " ".join(e.text for e in ledger.recall("g"))
                self.assertNotIn("PLANTED-SECRET-XYZ", blob)
            finally:
                ledger.close()
                store.close()

    def test_controller_has_no_provider_or_llm_imports(self) -> None:
        tree = ast.parse((PROJECT / "src" / "suns_chan" / "sandbox_controller.py").read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
        self.assertFalse(any("proxmox" in name for name in imports))
        self.assertFalse(any("llm" in name for name in imports))


if __name__ == "__main__":
    unittest.main()
