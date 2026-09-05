from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import EventLedger, KnowledgeStore


def open_pair(tmp: str) -> tuple[EventLedger, KnowledgeStore]:
    db = Path(tmp) / "m.db"
    return EventLedger(db), KnowledgeStore(db)


class KnowledgeViewTests(unittest.TestCase):
    def test_tags_and_timestamps(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                event = ledger.record("observation", "Surya prefers coffee.")
                record = store.propose("Surya prefers coffee.", [event.id], 0.6, tags=["Preference", " coffee "])
                self.assertEqual(record.tags, ("preference", "coffee"))
                self.assertGreaterEqual(record.updated_at, record.created_at)
                affirmed = store.affirm(record.id, event.id)
                self.assertGreaterEqual(affirmed.updated_at, record.updated_at)
                self.assertIn(event.id, affirmed.source_ids)
            finally:
                ledger.close()
                store.close()

    def test_uncertain_keeps_claim_visible(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                event = ledger.record("observation", "Maybe the cache helps.")
                record = store.propose("Cache helps.", [event.id], 0.45)
                flagged = store.mark_uncertain(record.id, "single weak observation")
                self.assertEqual(flagged.status, "uncertain")
                self.assertEqual([r.id for r in store.active()], [])
                self.assertEqual([r.id for r in store.visible()], [record.id])
                with self.assertRaises(ValueError):
                    store.mark_uncertain(record.id, "again")
            finally:
                ledger.close()
                store.close()

    def test_contradict_preserves_both_sides(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                first = ledger.record("observation", "Backups run on atlas.")
                old = store.propose("Backups run on atlas.", [first.id], 0.7)
                second = ledger.record("observation", "Backups moved to vega.")
                new = store.contradict(old.id, "Backups run on vega.", [second.id], 0.65, reason="conflicting report")
                self.assertEqual(store.get(old.id).status, "contradicted")
                self.assertEqual(new.supersedes_id, old.id)
                self.assertEqual(new.source_ids, (second.id,))
                # Raw events untouched.
                self.assertEqual(len(ledger.events_of_kind("observation")), 2)
                explanation = store.explain(new.id)
                self.assertEqual(explanation.statement, "Backups run on vega.")
                self.assertEqual(explanation.supporting[0].event_id, second.id)
                old_explanation = store.explain(old.id)
                self.assertEqual(old_explanation.contradicted_by[0].event_id, second.id)
            finally:
                ledger.close()
                store.close()

    def test_explain_traces_full_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                first = ledger.record("observation", "X is true.")
                second = ledger.record("outcome", "X confirmed again.", {"outcome": "success"})
                record = store.propose("X is true.", [first.id], 0.6)
                record = store.affirm(record.id, second.id)
                explanation = store.explain(record.id)
                self.assertEqual(explanation.confidence, record.confidence)
                self.assertEqual({s.event_id for s in explanation.supporting}, {first.id, second.id})
                self.assertTrue(all(s.text for s in explanation.supporting))
                self.assertEqual(explanation.contradicted_by, ())
            finally:
                ledger.close()
                store.close()

    def test_recall_includes_uncertain_but_not_superseded(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                event = ledger.record("learning", "Driving simulators need constraint checks.")
                record = store.propose("Driving simulators need constraint checks.", [event.id], 0.6)
                store.mark_uncertain(record.id, "needs more evidence")
                hits = store.recall("driving simulator constraints")
                self.assertEqual([h.id for h in hits], [record.id])
                other = ledger.record("learning", "Driving simulators need license checks.")
                store.supersede(record.id, "Driving simulators need license checks.", [other.id], 0.7)
                hits = store.recall("driving simulator constraints")
                self.assertTrue(all(h.status == "active" for h in hits))
            finally:
                ledger.close()
                store.close()

    def test_migration_handles_legacy_database(self) -> None:
        import sqlite3

        with TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "legacy.db")
            connection = sqlite3.connect(db)
            connection.execute(
                """CREATE TABLE knowledge_records (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, statement TEXT NOT NULL,
                    confidence REAL NOT NULL, source_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', supersedes_id INTEGER,
                    reason TEXT NOT NULL DEFAULT '')"""
            )
            connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO events (id) VALUES (1)")
            connection.execute(
                "INSERT INTO knowledge_records (id, created_at, statement, confidence,"
                " source_ids_json, status, supersedes_id, reason)"
                " VALUES (1, '2026-01-01T00:00:00+00:00', 'Legacy claim.', 0.5, '[1]', 'active', NULL, '')"
            )
            connection.commit()
            connection.close()
            store = KnowledgeStore(db)
            try:
                record = store.get(1)
                self.assertEqual(record.tags, ())
                self.assertEqual(record.updated_at, record.created_at)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
