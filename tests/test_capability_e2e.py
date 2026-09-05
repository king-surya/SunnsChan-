"""End-to-end capability acquisition: browser discovery + download + install +
validate + register + use + experience + future selection, with restart
persistence. Deterministic (mocks, no network, no LLM)."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    BrowserDiscoverySource,
    CapabilityAcquirer,
    CapabilityStore,
    Downloader,
    EventLedger,
    MockBrowserProvider,
    MockSandboxProvider,
    detect_capability_gap,
    generate_activities,
    recommend_capability,
)


class CapabilityE2ETests(unittest.TestCase):
    def _setup(self, tmp: str) -> tuple[EventLedger, CapabilityStore]:
        db = Path(tmp) / "m.db"
        return EventLedger(db), CapabilityStore(db)

    def _hub(self) -> MockBrowserProvider:
        return MockBrowserProvider(
            pages={
                "https://hub.example/": {
                    "title": "Tool Hub",
                    "text": "A directory of command-line tools.",
                    "links": [
                        ("csvkit", "https://hub.example/csvkit"),
                        ("weather-cli", "https://hub.example/weather-cli"),
                    ],
                },
                "https://hub.example/csvkit": {
                    "title": "csvkit",
                    "text": "A suite of utilities for CSV files.",
                    "links": [],
                },
            },
            files={"https://pypi.example/csvkit": b"csvkit-package-bytes"},
        )

    def test_browser_discovery_to_available_capability(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, registry = self._setup(tmp)
            try:
                discovery = BrowserDiscoverySource(
                    self._hub(), "https://hub.example/",
                    candidate_specs={
                        "csvkit": {
                            "kind": "tool", "version": "1.0.5",
                            "source_url": "https://pypi.example/csvkit",
                            "install_method": "pip install csvkit",
                            "validate_command": "csvkit --version",
                            "invoke": "csvkit <file>",
                            "capabilities": ["csv"],
                        },
                    },
                )
                downloader = Downloader(fetcher=lambda url: b"csvkit-package-bytes")
                acquirer = CapabilityAcquirer(
                    discovery=discovery, downloader=downloader,
                    sandbox=MockSandboxProvider(), registry=registry, ledger=ledger)
                report = acquirer.acquire("parse csv files", goal="clean a CSV export")
                self.assertEqual(report.status, "acquired")
                record = registry.get(report.capability_id)
                self.assertEqual(record.name, "csvkit")
                self.assertEqual(record.status, "available")
                # No more gap; recommendation returns the acquired capability.
                self.assertIsNone(detect_capability_gap("parse csv files", registry))
                recommended = recommend_capability("parse csv files", registry)
                self.assertEqual(recommended[0].id, record.id)
            finally:
                ledger.close()
                registry.close()

    def test_restart_persistence(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            registry = CapabilityStore(db)
            from suns_chan import CapabilityCandidate, MockDiscoverySource

            discovery = MockDiscoverySource(candidates={
                "parse csv": [CapabilityCandidate(
                    name="csvkit", kind="tool", description="CSV tools",
                    install_method="pip install csvkit", validate_command="csvkit --version",
                    invoke="csvkit <file>", capabilities=["csv"])],
            })
            acquirer = CapabilityAcquirer(
                discovery=discovery, downloader=Downloader(fetcher=lambda u: b"x"),
                sandbox=MockSandboxProvider(), registry=registry, ledger=ledger)
            report = acquirer.acquire("parse csv")
            self.assertEqual(report.status, "acquired")
            cap_id = report.capability_id
            registry.record_usage(cap_id, "success", task="parse csv")
            ledger.close()
            registry.close()

            # Reopen: the acquired capability and its history survive.
            ledger2 = EventLedger(db)
            registry2 = CapabilityStore(db)
            try:
                record = registry2.get(cap_id)
                self.assertEqual(record.status, "available")
                self.assertEqual(registry2.reliability(cap_id), 1.0)
                self.assertIsNone(detect_capability_gap("parse csv", registry2))
                self.assertTrue(ledger2.events_of_kind("experience"))
            finally:
                ledger2.close()
                registry2.close()

    def test_goal_gap_becomes_acquisition_activity(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, registry = self._setup(tmp)
            try:
                goal = ledger.record("goal_opened", "analyze grib2 weather files",
                                     {"priority": 0.5})
                activities = generate_activities(ledger, capabilities=registry,
                                                 max_candidates=20)
                acquire = [a for a in activities if a.category == "ACQUIRE"]
                self.assertTrue(acquire)
                self.assertEqual(acquire[0].goal_id, goal.id)
                self.assertEqual(acquire[0].source, "CAPABILITY")
            finally:
                ledger.close()
                registry.close()


if __name__ == "__main__":
    unittest.main()
