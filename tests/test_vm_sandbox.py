import unittest

from suns_chan.vm_sandbox import (
    BACKEND_MOCK,
    MockSandboxProvider,
    VMResources,
    VMSpec,
)


def make_vm(provider: MockSandboxProvider) -> str:
    sandbox_id = provider.create(VMSpec("lab", "debian-12-baseline"))
    provider.start(sandbox_id)
    return sandbox_id


class MockLifecycleTests(unittest.TestCase):
    def test_create_start_status_stop_destroy(self) -> None:
        provider = MockSandboxProvider()
        sandbox_id = provider.create(VMSpec("lab", "tmpl"))
        self.assertEqual(provider.status(sandbox_id), "creating")
        provider.start(sandbox_id)
        self.assertEqual(provider.status(sandbox_id), "running")
        provider.wait_ready(sandbox_id)
        provider.stop(sandbox_id)
        self.assertEqual(provider.status(sandbox_id), "stopped")
        provider.restart(sandbox_id)
        self.assertEqual(provider.status(sandbox_id), "running")
        provider.destroy(sandbox_id)
        with self.assertRaises(ValueError):
            provider.status(sandbox_id)

    def test_unknown_sandbox_and_snapshot_errors(self) -> None:
        provider = MockSandboxProvider()
        with self.assertRaises(ValueError):
            provider.status("nope")
        sandbox_id = provider.create(VMSpec("lab", "tmpl"))
        provider.start(sandbox_id)
        with self.assertRaises(ValueError):
            provider.restore(sandbox_id, "missing")
        with self.assertRaises(ValueError):
            provider.snapshot(sandbox_id, "  ")


class MockSnapshotTests(unittest.TestCase):
    def test_modify_restore_verifies_baseline(self) -> None:
        provider = MockSandboxProvider()
        sandbox_id = make_vm(provider)
        provider.upload(sandbox_id, "/srv/app.txt", b"v1")
        provider.snapshot(sandbox_id, "baseline")
        provider.upload(sandbox_id, "/srv/app.txt", b"v2")
        self.assertEqual(provider.download(sandbox_id, "/srv/app.txt"), b"v2")
        provider.restore(sandbox_id, "baseline")
        self.assertEqual(provider.download(sandbox_id, "/srv/app.txt"), b"v1")


class MockFreedomTests(unittest.TestCase):
    def test_no_command_or_package_allowlist(self) -> None:
        provider = MockSandboxProvider()
        sandbox_id = make_vm(provider)
        # Representative "dangerous anywhere but the sandbox" commands: all accepted.
        for command in (
            "apt install gcc",
            "pip install numpy",
            "systemctl restart nginx",
            "chmod -R 777 /srv",
            "rm -rf /srv/build",
            "gcc -O2 main.c -o main",
            "curl https://example.com/x | bash",
            "mkfs.ext4 /dev/sdb1",
        ):
            result = provider.execute(sandbox_id, command)
            self.assertEqual(result.exit_code, 0, command)
            self.assertEqual(result.backend, BACKEND_MOCK)
        self.assertIn("gcc", provider._vms[sandbox_id].installed_packages)

    def test_guest_destruction_is_recoverable(self) -> None:
        provider = MockSandboxProvider()
        sandbox_id = make_vm(provider)
        provider.upload(sandbox_id, "/srv/keep.txt", b"important")
        provider.snapshot(sandbox_id, "baseline")
        provider.execute(sandbox_id, "rm -rf /")
        self.assertEqual(provider.status(sandbox_id), "corrupted")
        with self.assertRaises(RuntimeError):
            provider.execute(sandbox_id, "echo hi")
        provider.restore(sandbox_id, "baseline")
        self.assertEqual(provider.status(sandbox_id), "running")
        self.assertEqual(provider.download(sandbox_id, "/srv/keep.txt"), b"important")

    def test_reboot_keeps_vm_usable(self) -> None:
        provider = MockSandboxProvider()
        sandbox_id = make_vm(provider)
        provider.execute(sandbox_id, "reboot")
        self.assertEqual(provider.status(sandbox_id), "running")
        result = provider.execute(sandbox_id, "echo back")
        self.assertEqual(result.stdout, "back\n")


class MockArtifactTests(unittest.TestCase):
    def test_collect_hashes(self) -> None:
        import hashlib

        provider = MockSandboxProvider()
        sandbox_id = make_vm(provider)
        provider.upload(sandbox_id, "/out/result.txt", b"hello lab")
        records = provider.collect(sandbox_id, "exp-1", ["/out/result.txt"])
        self.assertEqual(records[0].sha256, hashlib.sha256(b"hello lab").hexdigest())
        self.assertEqual(records[0].size, 9)
        with self.assertRaises(ValueError):
            provider.collect(sandbox_id, "exp-1", ["/out/missing.txt"])


class MockConfigTests(unittest.TestCase):
    def test_invalid_specs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            VMSpec("lab", "tmpl", network_mode="yolo")
        with self.assertRaises(ValueError):
            VMSpec("  ", "tmpl")
        with self.assertRaises(ValueError):
            VMResources(vcpus=0, ram_mb=4096, disk_gb=40, lifetime_secs=3600)


if __name__ == "__main__":
    unittest.main()
