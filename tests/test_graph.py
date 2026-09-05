from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import CuriosityStore, EventLedger, KnowledgeStore, record_experience
from suns_chan.graph import build_index, format_paths


def open_all(tmp: str):
    db = Path(tmp) / "m.db"
    ledger = EventLedger(db)
    return ledger, KnowledgeStore(db), CuriosityStore(db)


class GraphTests(unittest.TestCase):
    def test_relationship_creation_with_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, curiosities = open_all(tmp)
            try:
                first = record_experience(ledger, session_id="s", activity_id="s-a1",
                                          intent="Probe scheduler", result="failure")
                second = record_experience(
                    ledger, session_id="s", activity_id="s-a2", intent="Retry probe",
                    result="success", related={"follow_up_of": [first.id], "confirms": [first.id]})
                index = build_index(ledger)
                paths = index.query("experience", str(first.id))
                self.assertTrue(paths)
                for path in paths:
                    self.assertTrue(path.provenance)
                follow = index.query("experience", str(first.id), relation="follow_up_of")
                self.assertEqual(len(follow), 1)
                self.assertEqual(follow[0].nodes[1].key, str(second.id))
                text = format_paths(paths)
                self.assertIn(str(first.id), text)
            finally:
                ledger.close()
                store.close()
                curiosities.close()

    def test_contradiction_preserves_both_sides(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, curiosities = open_all(tmp)
            try:
                first = ledger.record("observation", "Backups run on atlas.")
                old = store.propose("Backups run on atlas.", [first.id], 0.7)
                second = ledger.record("observation", "Backups moved to vega.")
                new = store.contradict(old.id, "Backups run on vega.", [second.id], 0.65)
                index = build_index(ledger, knowledge=store)
                paths = index.query("knowledge", str(old.id), relation="contradicts")
                self.assertEqual(len(paths), 1)
                self.assertEqual(paths[0].nodes[1].key, str(new.id))
                # Old claim still present and queryable.
                self.assertIn(("knowledge", str(old.id)), index.nodes)
                self.assertEqual(store.get(old.id).status, "contradicted")
            finally:
                ledger.close()
                store.close()
                curiosities.close()

    def test_topic_entities_link_records(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, curiosities = open_all(tmp)
            try:
                origin = ledger.record("observation", "Disk fills on Sundays.")
                item = curiosities.open("Why does disk fill on Sundays?", origin.id, topic="storage")
                index = build_index(ledger, curiosities=curiosities)
                paths = index.query("curiosity", str(item.id))
                topics = [n.key for p in paths for n in p.nodes if n.kind == "topic"]
                self.assertIn("storage", topics)
                with self.assertRaises(ValueError):
                    index.query("curiosity", str(item.id), depth=0)
                self.assertEqual(index.query("curiosity", "9999"), [])
            finally:
                ledger.close()
                store.close()
                curiosities.close()


if __name__ == "__main__":
    unittest.main()
