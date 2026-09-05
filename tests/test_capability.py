import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from suns_chan import CapabilityStore, detect_capability_gap


def open_store(tmp: str) -> CapabilityStore:
    return CapabilityStore(Path(tmp) / "c.db")


class CapabilityStoreTests(unittest.TestCase):
    def test_lifecycle_and_dedup(self) -> None:
        with TemporaryDirectory() as tmp:
            store = open_store(tmp)
            try:
                a = store.discover("jq", "executable", "JSON processor", version="1.6")
                again = store.discover("jq", "executable", "JSON processor", version="1.6")
                self.assertEqual(a.id, again.id)  # dedup by (name, version)
                self.assertEqual(a.status, "discovered")
                for status in ("downloaded", "installed", "initialized", "validated", "available"):
                    a = store.transition(a.id, status)
                self.assertEqual(a.status, "available")
                self.assertIsNotNone(a.last_validated)
            finally:
                store.close()

    def test_invalid_transition_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = open_store(tmp)
            try:
                a = store.discover("x", "tool")
                with self.assertRaises(ValueError):
                    store.transition(a.id, "available")  # skips lifecycle
            finally:
                store.close()

    def test_failure_and_disable_reactivate_remove(self) -> None:
        with TemporaryDirectory() as tmp:
            store = open_store(tmp)
            try:
                a = store.discover("y", "package")
                for status in ("downloaded", "installed", "initialized", "validated", "available"):
                    a = store.transition(a.id, status)
                a = store.transition(a.id, "disabled", reason="flaky")
                self.assertEqual(a.status, "disabled")
                a = store.transition(a.id, "available")
                self.assertEqual(a.status, "available")
                a = store.transition(a.id, "removed", reason="obsolete")
                self.assertEqual(a.status, "removed")
            finally:
                store.close()

    def test_usage_history_and_reliability(self) -> None:
        with TemporaryDirectory() as tmp:
            store = open_store(tmp)
            try:
                a = store.discover("z", "tool", version="1.0")
                for status in ("downloaded", "installed", "initialized", "validated", "available"):
                    a = store.transition(a.id, status)
                self.assertIsNone(store.reliability(a.id))
                store.record_usage(a.id, "success", task="parse csv")
                store.record_usage(a.id, "success", task="parse csv")
                store.record_usage(a.id, "failure", task="parse weird csv")
                self.assertEqual(store.reliability(a.id), round(2 / 3, 4))
                self.assertEqual(len(store.usage(a.id)), 3)
            finally:
                store.close()

    def test_match_filters_by_status(self) -> None:
        with TemporaryDirectory() as tmp:
            store = open_store(tmp)
            try:
                good = store.discover("csv-parser", "tool", "parses CSV files", capabilities=["csv"])
                for status in ("downloaded", "installed", "initialized", "validated", "available"):
                    good = store.transition(good.id, status)
                bad = store.discover("other-parser", "tool", "parses CSV too", capabilities=["csv"])
                bad = store.transition(bad.id, "failed", reason="broken")
                matches = store.match("parse csv files")
                self.assertEqual([r.id for r, _ in matches], [good.id])
            finally:
                store.close()

    def test_gap_detection(self) -> None:
        with TemporaryDirectory() as tmp:
            store = open_store(tmp)
            try:
                gap = detect_capability_gap("analyze grib2 weather files", store)
                self.assertIsNotNone(gap)
                self.assertIn("grib2 weather files", gap.requirement)
                c = store.discover("grib-analyzer", "tool", "analyzes grib2 weather files",
                                   capabilities=["grib2"])
                for status in ("downloaded", "installed", "initialized", "validated", "available"):
                    c = store.transition(c.id, status)
                self.assertIsNone(detect_capability_gap("analyze grib2 weather files", store))
            finally:
                store.close()

    def test_persistence_across_reopen(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "c.db"
            store = CapabilityStore(db)
            c = store.discover("persist-tool", "tool", "persists", version="2.0")
            for status in ("downloaded", "installed", "initialized", "validated", "available"):
                c = store.transition(c.id, status)
            store.record_usage(c.id, "success", task="t")
            store.close()

            reopened = CapabilityStore(db)
            try:
                record = reopened.get(c.id)
                self.assertEqual(record.status, "available")
                self.assertEqual(record.version, "2.0")
                self.assertEqual(reopened.reliability(c.id), 1.0)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
