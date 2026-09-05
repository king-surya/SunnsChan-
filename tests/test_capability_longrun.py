"""Long-run capability simulation: acquire, succeed/fail, reuse reliable,
avoid flaky, disable after repeated failures, survive restart — without
duplicate registrations, retry loops, or capability explosion."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import (
    CapabilityCandidate,
    CapabilityAcquirer,
    CapabilityStore,
    Downloader,
    EventLedger,
    MockDiscoverySource,
    MockSandboxProvider,
    recommend_capability,
)


class FlakySandbox(MockSandboxProvider):
    """Simulates a tool that fails validation intermittently (e.g. broken
    dependency), so a capability can be repeatedly re-acquired and fail."""

    def execute(self, sandbox_id, command, timeout_secs=120.0):
        from suns_chan.vm_sandbox import GuestResult, BACKEND_MOCK
        if command == "flaky --version":
            return GuestResult(command, 1, "", "dependency missing", 0.0, BACKEND_MOCK)
        return super().execute(sandbox_id, command, timeout_secs)


class CapabilityLongRunTests(unittest.TestCase):
    def _acquire(self, tmp, requirement, candidate, *, sandbox=None):
        db = Path(tmp) / "m.db"
        ledger = EventLedger(db)
        registry = CapabilityStore(db)
        discovery = MockDiscoverySource(candidates={requirement: [candidate]})
        acquirer = CapabilityAcquirer(
            discovery=discovery, downloader=Downloader(fetcher=lambda u: b"bytes"),
            sandbox=sandbox or MockSandboxProvider(), registry=registry, ledger=ledger)
        report = acquirer.acquire(requirement)
        return ledger, registry, report

    def test_reliable_reused_over_flaky(self) -> None:
        with TemporaryDirectory() as tmp:
            good = CapabilityCandidate(
                name="csv-parser", kind="tool", description="CSV parsing",
                install_method="pip install csv-parser", validate_command="csv-parser --version",
                invoke="csv-parser <file>", capabilities=["csv"])
            ledger, registry, r1 = self._acquire(tmp, "parse csv", good)
            try:
                self.assertEqual(r1.status, "acquired")
                good_id = r1.capability_id
                # A competing capability was acquired earlier through a
                # different need; register it manually (available).
                flaky = registry.discover("flaky-parser", "tool", "CSV parsing (unstable)",
                                          version="0.9", capabilities=["csv"])
                for status in ("downloaded", "installed", "initialized", "validated", "available"):
                    flaky = registry.transition(flaky.id, status)
                flaky_id = flaky.id
                # Use both; the flaky one fails repeatedly.
                for _ in range(3):
                    registry.record_usage(good_id, "success", task="parse csv")
                    registry.record_usage(flaky_id, "failure", task="parse csv")
                # Recommendation prefers the reliable one despite both matching.
                rec = recommend_capability("parse csv", registry)
                self.assertEqual(rec[0].id, good_id)
                # No duplicate registrations: each name registered once.
                names = [c.name for c in registry.all()]
                self.assertEqual(names.count("csv-parser"), 1)
                self.assertEqual(names.count("flaky-parser"), 1)
            finally:
                ledger.close()
                registry.close()

    def test_repeated_failure_becomes_disabled(self) -> None:
        with TemporaryDirectory() as tmp:
            flaky = CapabilityCandidate(
                name="flaky", kind="tool", description="unstable tool",
                install_method="pip install flaky", validate_command="flaky --version",
                invoke="flaky", capabilities=["x"])
            # Acquire fails validation (FlakySandbox) -> failed, not healthy.
            ledger, registry, report = self._acquire(
                tmp, "do x", flaky, sandbox=FlakySandbox())
            try:
                self.assertEqual(report.status, "failed")
                record = registry.get(report.capability_id)
                self.assertEqual(record.status, "failed")
                # A failed capability is NOT surfaced as available.
                self.assertEqual(registry.available(), [])
                self.assertIsNone(recommend_capability("do x", registry))
                # The lifecycle lets it be disabled rather than retried forever.
                record = registry.transition(record.id, "disabled", reason="repeated failure")
                self.assertEqual(record.status, "disabled")
            finally:
                ledger.close()
                registry.close()

    def test_no_retry_loop_and_bounded_registry(self) -> None:
        with TemporaryDirectory() as tmp:
            # The acquirer is goal-driven and single-shot: a failed acquisition
            # returns a report and does not loop. Re-running is the caller's
            # choice, and dedup prevents registry explosion.
            candidate = CapabilityCandidate(
                name="tool-x", kind="tool", description="does x",
                install_method="pip install tool-x", validate_command="tool-x --version",
                invoke="tool-x", capabilities=["x"])
            ledger, registry, report = self._acquire(tmp, "do x", candidate)
            try:
                self.assertEqual(report.status, "acquired")
                # Re-acquiring the same name+version returns the same record.
                registry.discover("tool-x", "tool", "does x", version="")
                self.assertEqual(len(registry.all()), 1)
            finally:
                ledger.close()
                registry.close()

    def test_survives_restart_with_history(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "m.db"
            ledger = EventLedger(db)
            registry = CapabilityStore(db)
            c = registry.discover("persist", "tool", "persists", version="1")
            for status in ("downloaded", "installed", "initialized", "validated", "available"):
                c = registry.transition(c.id, status)
            registry.record_usage(c.id, "success", task="t1")
            registry.record_usage(c.id, "failure", task="t2")
            ledger.close()
            registry.close()

            registry2 = CapabilityStore(db)
            try:
                self.assertEqual(registry2.get(c.id).status, "available")
                self.assertEqual(registry2.reliability(c.id), 0.5)
                self.assertEqual(len(registry2.usage(c.id)), 2)
            finally:
                registry2.close()


if __name__ == "__main__":
    unittest.main()
