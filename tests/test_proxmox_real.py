"""REAL Proxmox integration test (§22). Gated: runs ONLY when the environment
declares SANDBOX_MODE=proxmox with PROXMOX_HOST/USER/TOKEN set. Anywhere else
this file SKIPS honestly — a skipped real test is never reported as passed.
"""

import os
import unittest

REAL_MODE = os.environ.get("SANDBOX_MODE", "mock") == "proxmox" and bool(os.environ.get("PROXMOX_HOST"))


@unittest.skipUnless(REAL_MODE, "requires SANDBOX_MODE=proxmox with PROXMOX_* credentials")
class RealVMTests(unittest.TestCase):
    def test_full_vm_lifecycle(self) -> None:
        from suns_chan import (
            ProxmoxCredentials,
            ProxmoxSandboxProvider,
            SandboxController,
            VMSpec,
            VMResources,
            validate_proposal,
        )

        creds = ProxmoxCredentials.from_env()
        provider = ProxmoxSandboxProvider(creds)
        controller = SandboxController(
            provider, template=os.environ.get("SANDBOX_TEMPLATE", "debian-12-baseline"),
            default_resources=VMResources(
                vcpus=int(os.environ.get("SANDBOX_VCPUS", "2")),
                ram_mb=int(os.environ.get("SANDBOX_RAM_MB", "4096")),
                disk_gb=int(os.environ.get("SANDBOX_DISK_GB", "40")),
            ),
            approver=lambda proposal: True,
        )
        sandbox_id = provider.create(VMSpec("real-smoke", controller._template))
        try:
            provider.start(sandbox_id)
            provider.wait_ready(sandbox_id)
            self.assertEqual(provider.status(sandbox_id), "running")
            self.assertIn("real-vm", provider.execute(sandbox_id, "echo real-vm").stdout)
            provider.execute(sandbox_id, "python3 -c \"import pathlib; pathlib.Path('/tmp/smoke.txt').write_text('ok')\"")
            provider.execute(sandbox_id, "reboot")
            provider.wait_ready(sandbox_id)
            self.assertEqual(provider.status(sandbox_id), "running")
            provider.snapshot(sandbox_id, "baseline")
            provider.execute(sandbox_id, "rm /tmp/smoke.txt")
            provider.restore(sandbox_id, "baseline")
            record = controller.run_experiment(validate_proposal({
                "goal": "Real VM smoke experiment",
                "hypothesis": "Guest runs programs and keeps files",
                "operations": ["python3 --version", "echo experiment-ok"],
                "expected_outcome": "commands succeed",
            }))
            self.assertEqual(record.outcome, "success")
            self.assertEqual(record.backend, "REAL")
        finally:
            try:
                provider.destroy(sandbox_id)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
