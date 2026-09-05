from tempfile import TemporaryDirectory
import unittest

from suns_chan import SandboxBroker, SandboxPolicy


class SandboxBrokerTests(unittest.TestCase):
    def test_write_read_list_and_destroy_stay_inside_workspace(self) -> None:
        with TemporaryDirectory() as temporary:
            broker = SandboxBroker(temporary, SandboxPolicy())
            broker.create("lab-01")
            result = broker.write_text("lab-01", "notes/hello.txt", "hello sandbox")
            self.assertGreater(result.size, 0)
            self.assertEqual(len(result.sha256), 64)
            self.assertEqual(broker.read_text("lab-01", "notes/hello.txt"), "hello sandbox")
            self.assertIn("notes/hello.txt", broker.list_files("lab-01"))
            with self.assertRaises(ValueError):
                broker.write_text("lab-01", "../escape.txt", "nope")
            with self.assertRaises(ValueError):
                broker.read_text("lab-01", "../escape.txt")
            broker.destroy("lab-01")
            self.assertEqual(broker.list_files("lab-01"), [])

    def test_quotas_are_enforced(self) -> None:
        with TemporaryDirectory() as temporary:
            policy = SandboxPolicy(max_bytes_per_file=10, max_total_bytes=20, max_files=2)
            broker = SandboxBroker(temporary, policy)
            broker.create("lab-02")
            broker.write_text("lab-02", "a.txt", "12345")
            broker.write_text("lab-02", "b.txt", "12345")
            with self.assertRaises(ValueError):
                broker.write_text("lab-02", "c.txt", "12345")
            with self.assertRaises(ValueError):
                broker.write_text("lab-02", "big.txt", "12345678901")

    def test_fetch_rejects_non_allowlisted_domains(self) -> None:
        with TemporaryDirectory() as temporary:
            broker = SandboxBroker(temporary, SandboxPolicy(allowed_domains=("example.com",)))
            broker.create("lab-03")
            with self.assertRaises(ValueError):
                broker.fetch_to_file("lab-03", "https://not-allowed.example.org/x", "x.bin")

    def test_package_allowlist(self) -> None:
        with TemporaryDirectory() as temporary:
            broker = SandboxBroker(temporary, SandboxPolicy(allowed_packages=("numpy",)))
            self.assertTrue(broker.is_package_allowed("numpy"))
            self.assertTrue(broker.is_package_allowed(" NumPy "))
            self.assertFalse(broker.is_package_allowed("requests"))
            self.assertFalse(broker.is_package_allowed("bad;package"))


if __name__ == "__main__":
    unittest.main()
