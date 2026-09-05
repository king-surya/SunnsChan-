import unittest

from suns_chan.sandbox_controller import (
    ExperimentProposal,
    SandboxController,
    validate_proposal,
)
from suns_chan.vm_sandbox import MockSandboxProvider, VMSpec
from suns_chan.vm_template import VMTemplateSpec, cloud_init_user_data


def propose(**overrides) -> ExperimentProposal:
    data = {
        "goal": "Inspect lab cron behavior",
        "hypothesis": "A marker file can be created and read back",
        "operations": ["write /out/marker.txt hello-lab", "echo done"],
        "expected_outcome": "marker file exists with expected content",
        "collect_paths": ["/out/marker.txt"],
    }
    data.update(overrides)
    return validate_proposal(data)


class ProposalTests(unittest.TestCase):
    def test_valid_proposal(self) -> None:
        proposal = propose()
        self.assertEqual(proposal.network_mode, "isolated")
        self.assertEqual(len(proposal.operations), 2)

    def test_malformed_proposals_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_proposal("not an object")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            propose(goal="  ")
        with self.assertRaises(ValueError):
            propose(operations=[])
        with self.assertRaises(ValueError):
            propose(operations=["ok", 42])
        with self.assertRaises(ValueError):
            propose(time_budget_secs=5)
        with self.assertRaises(ValueError):
            propose(network_mode="yolo")


class ControllerFlowTests(unittest.TestCase):
    def test_successful_experiment(self) -> None:
        controller = SandboxController(MockSandboxProvider(), template="debian-12-baseline")
        record = controller.run_experiment(propose())
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.outcome, "success")
        self.assertEqual(record.backend, "MOCK")
        self.assertEqual(len(record.artifacts), 1)
        self.assertEqual(record.artifacts[0].path, "/out/marker.txt")
        self.assertTrue(record.started_at and record.ended_at)
        # Disposable: VM destroyed afterwards.
        self.assertEqual(controller._provider._vms, {})

    def test_failed_command_no_retry(self) -> None:
        seen: list = []
        provider = MockSandboxProvider()
        original = provider.execute

        def fail_once(sandbox_id: str, command: str, timeout_secs: float = 120.0):
            seen.append(command)
            if command == "boom":
                from suns_chan.vm_sandbox import GuestResult, BACKEND_MOCK

                return GuestResult(command, 3, "", "kaput", 0.0, BACKEND_MOCK)
            return original(sandbox_id, command, timeout_secs)

        provider.execute = fail_once  # type: ignore[method-assign]
        controller = SandboxController(provider, template="t")
        record = controller.run_experiment(propose(operations=["echo first", "boom", "echo never"]))
        self.assertEqual(record.status, "rolled_back")  # failed -> restored
        self.assertEqual(record.outcome, "failure")
        self.assertTrue(record.rolled_back)
        self.assertIn("boom", record.failure_reason)
        self.assertNotIn("echo never", seen)  # remaining work terminated

    def test_guest_corruption_recovers(self) -> None:
        controller = SandboxController(MockSandboxProvider(), template="t")
        record = controller.run_experiment(propose(operations=["rm -rf /"]))
        self.assertEqual(record.status, "rolled_back")
        self.assertTrue(record.rolled_back)

    def test_timeout(self) -> None:
        ticks = [0.0]

        def fake_clock() -> float:
            ticks[0] += 1000.0
            return ticks[0]

        controller = SandboxController(MockSandboxProvider(), template="t", clock=fake_clock)
        record = controller.run_experiment(propose(operations=["echo a", "echo b"]))
        self.assertEqual(record.status, "timeout")
        self.assertEqual(record.outcome, "failure")
        self.assertIn("time budget exhausted", record.failure_reason)

    def test_cancel(self) -> None:
        controller = SandboxController(MockSandboxProvider(), template="t")
        controller.cancel("exp-0001")
        record = controller.run_experiment(propose())
        self.assertEqual(record.status, "cancelled")

    def test_real_backend_requires_approver(self) -> None:
        from suns_chan.proxmox_provider import ProxmoxCredentials, ProxmoxSandboxProvider

        creds = ProxmoxCredentials("h", "u", "n", "v")
        controller = SandboxController(ProxmoxSandboxProvider(creds), template="t")
        with self.assertRaises(RuntimeError):
            controller.run_experiment(propose())
        allowed = SandboxController(ProxmoxSandboxProvider(creds), template="t",
                                    approver=lambda proposal: False)
        record = allowed.run_experiment(propose())
        self.assertEqual(record.status, "cancelled")
        self.assertEqual(record.failure_reason, "approval denied")

    def test_template_generator(self) -> None:
        user_data = cloud_init_user_data(VMTemplateSpec())
        self.assertIn("qemu-guest-agent", user_data)
        self.assertIn("suns-sandbox", user_data)
        deterministic = cloud_init_user_data(VMTemplateSpec())
        self.assertEqual(user_data, deterministic)
        with self.assertRaises(ValueError):
            VMTemplateSpec(distro="  ")


if __name__ == "__main__":
    unittest.main()
