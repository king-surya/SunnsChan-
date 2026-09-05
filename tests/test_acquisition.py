import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    CapabilityCandidate,
    CapabilityStore,
    CapabilityAcquirer,
    Downloader,
    EventLedger,
    MockDiscoverySource,
    MockSandboxProvider,
    evaluate_candidate,
)


class DownloaderTests(unittest.TestCase):
    def test_success_with_verified_sha256(self) -> None:
        data = b"hello download world"
        import hashlib
        expected = hashlib.sha256(data).hexdigest()
        downloader = Downloader(fetcher=lambda url: data)
        record = downloader.download("https://x.example/f", expected_sha256=expected)
        self.assertTrue(record.verified)
        self.assertEqual(record.size, len(data))
        self.assertEqual(record.sha256, expected)
        self.assertEqual(record.backend, "MOCK")

    def test_checksum_mismatch_fails_loudly(self) -> None:
        downloader = Downloader(fetcher=lambda url: b"actual bytes")
        record = downloader.download("https://x.example/f", expected_sha256="0" * 64)
        self.assertFalse(record.verified)
        self.assertEqual(record.error, "checksum mismatch")

    def test_download_failure_is_recorded(self) -> None:
        def fetcher(url):
            raise RuntimeError("network down")

        downloader = Downloader(fetcher=fetcher)
        record = downloader.download("https://x.example/f")
        self.assertFalse(record.verified)
        self.assertIn("network down", record.error)
        self.assertEqual(record.size, 0)


class EvaluateCandidateTests(unittest.TestCase):
    def test_overlap_and_uncertainty(self) -> None:
        candidate = CapabilityCandidate(
            name="csvkit", kind="tool", description="CSV parsing tools",
            install_method="pip install csvkit", validate_command="csvkit --version",
            capabilities=["csv"],
        )
        evaluation = evaluate_candidate(candidate, "parse csv files")
        self.assertGreater(evaluation.match_score, 0)
        self.assertFalse(evaluation.uncertain)
        self.assertGreater(evaluation.confidence, 0.3)

    def test_incomplete_candidate_is_uncertain(self) -> None:
        candidate = CapabilityCandidate(name="mystery", kind="tool", description="")
        evaluation = evaluate_candidate(candidate, "parse csv files")
        self.assertTrue(evaluation.uncertain)
        self.assertEqual(evaluation.match_score, 0.0)


class CapabilityAcquirerTests(unittest.TestCase):
    def _setup(self, tmp: str) -> tuple[EventLedger, CapabilityStore]:
        db = Path(tmp) / "m.db"
        return EventLedger(db), CapabilityStore(db)

    def _candidate(self) -> CapabilityCandidate:
        return CapabilityCandidate(
            name="csvkit", kind="tool", description="CSV parsing tools",
            version="1.0.5", source_type="package-index",
            source_url="https://pypi.example/csvkit",
            install_method="pip install csvkit",
            validate_command="csvkit --version",
            invoke="csvkit <file>",
            capabilities=["csv"],
        )

    def test_full_acquire_loop(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, registry = self._setup(tmp)
            try:
                discovery = MockDiscoverySource(candidates={
                    "parse csv files": [self._candidate()],
                })
                downloader = Downloader(fetcher=lambda url: b"csvkit-bytes")
                acquirer = CapabilityAcquirer(
                    discovery=discovery, downloader=downloader,
                    sandbox=MockSandboxProvider(), registry=registry, ledger=ledger)
                report = acquirer.acquire("parse csv files", goal="process a CSV dataset")
                self.assertEqual(report.status, "acquired")
                record = registry.get(report.capability_id)
                self.assertEqual(record.status, "available")
                self.assertEqual(record.version, "1.0.5")
                self.assertEqual(record.invoke, "csvkit <file>")
                self.assertEqual(registry.reliability(record.id), 1.0)
                self.assertTrue(ledger.events_of_kind("experience"))
                experience = ledger.events_of_kind("experience")[0]
                self.assertEqual(experience.metadata.get("capability_id"), record.id)
                from suns_chan import detect_capability_gap
                self.assertIsNone(detect_capability_gap("parse csv files", registry))
            finally:
                ledger.close()
                registry.close()

    def test_acquire_satisfied_requirement_is_no_gap(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, registry = self._setup(tmp)
            try:
                c = registry.discover("csvkit", "tool", "CSV parsing", capabilities=["csv"])
                for status in ("downloaded", "installed", "initialized", "validated", "available"):
                    c = registry.transition(c.id, status)
                acquirer = CapabilityAcquirer(
                    discovery=MockDiscoverySource(), downloader=Downloader(fetcher=lambda u: b""),
                    sandbox=MockSandboxProvider(), registry=registry, ledger=ledger)
                report = acquirer.acquire("parse csv files")
                self.assertEqual(report.status, "no_gap")
            finally:
                ledger.close()
                registry.close()

    def test_no_candidate_fails_cleanly(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, registry = self._setup(tmp)
            try:
                acquirer = CapabilityAcquirer(
                    discovery=MockDiscoverySource(candidates={}),
                    downloader=Downloader(fetcher=lambda u: b""),
                    sandbox=MockSandboxProvider(), registry=registry, ledger=ledger)
                report = acquirer.acquire("parse csv files")
                self.assertEqual(report.status, "no_candidate")
            finally:
                ledger.close()
                registry.close()

    def test_corrupt_download_fails_registration(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, registry = self._setup(tmp)
            try:
                discovery = MockDiscoverySource(candidates={
                    "parse csv files": [self._candidate()],
                })
                downloader = Downloader(fetcher=lambda url: b"wrong-bytes")
                acquirer = CapabilityAcquirer(
                    discovery=discovery, downloader=downloader,
                    sandbox=MockSandboxProvider(), registry=registry, ledger=ledger)
                report = acquirer.acquire("parse csv files", expected_sha256="a" * 64)
                self.assertEqual(report.status, "failed")
                record = registry.get(report.capability_id)
                self.assertEqual(record.status, "failed")
            finally:
                ledger.close()
                registry.close()

    def test_install_failure_marks_failed(self) -> None:
        class FailingSandbox(MockSandboxProvider):
            def execute(self, sandbox_id, command, timeout_secs=120.0):
                from suns_chan.vm_sandbox import GuestResult, BACKEND_MOCK
                if command.startswith("pip install"):
                    return GuestResult(command, 1, "", "package not found", 0.0, BACKEND_MOCK)
                return super().execute(sandbox_id, command, timeout_secs)

        with TemporaryDirectory() as tmp:
            ledger, registry = self._setup(tmp)
            try:
                discovery = MockDiscoverySource(candidates={
                    "parse csv files": [self._candidate()],
                })
                acquirer = CapabilityAcquirer(
                    discovery=discovery, downloader=Downloader(fetcher=lambda u: b"bytes"),
                    sandbox=FailingSandbox(), registry=registry, ledger=ledger)
                report = acquirer.acquire("parse csv files")
                self.assertEqual(report.status, "failed")
                record = registry.get(report.capability_id)
                self.assertEqual(record.status, "failed")
            finally:
                ledger.close()
                registry.close()

    def test_agent_discovery_and_acquisition(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, registry = self._setup(tmp)
            try:
                agent = CapabilityCandidate(
                    name="homelab-agent", kind="agent", description="automates homelab checks",
                    version="0.3.0", source_type="agent-repo",
                    source_url="https://git.example/homelab-agent",
                    install_method="git clone https://git.example/homelab-agent && pip install -e .",
                    validate_command="homelab-agent --help",
                    invoke="homelab-agent check",
                    capabilities=["homelab", "automation"],
                )
                discovery = MockDiscoverySource(candidates={"automate homelab": [agent]})
                acquirer = CapabilityAcquirer(
                    discovery=discovery, downloader=Downloader(fetcher=lambda u: b"repo"),
                    sandbox=MockSandboxProvider(), registry=registry, ledger=ledger)
                report = acquirer.acquire("automate homelab")
                self.assertEqual(report.status, "acquired")
                record = registry.get(report.capability_id)
                self.assertEqual(record.kind, "agent")
                self.assertEqual(record.status, "available")
            finally:
                ledger.close()
                registry.close()


if __name__ == "__main__":
    unittest.main()
