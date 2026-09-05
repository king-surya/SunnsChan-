from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    EchoProvider,
    EventLedger,
    IdentitySeed,
    KnowledgeStore,
    build_chat_decide,
    build_knowledge_section,
    handle_command,
    label_record,
)
from suns_chan.chat import build_user_prompt
from suns_chan.core import TurnContext
from suns_chan.state import AgentState


PROJECT = Path(__file__).resolve().parents[1]


def open_pair(tmp: str) -> tuple[EventLedger, KnowledgeStore]:
    db = Path(tmp) / "m.db"
    return EventLedger(db), KnowledgeStore(db)


def make_context(ledger: EventLedger, observation: str) -> TurnContext:
    return TurnContext(
        observation=observation,
        recalled_events=ledger.recall(observation),
        identity=IdentitySeed.from_file(PROJECT / "config" / "identity_seed.json"),
        state=AgentState(),
        active_goals=[],
    )


class ContextBuilderTests(unittest.TestCase):
    def test_labels_distinguish_claim_strength(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                fact_event = ledger.record("observation", "Water boils at 100C at sea level.")
                fact = store.propose("Water boils at 100C at sea level.", [fact_event.id], 0.9)
                weak_event = ledger.record("observation", "Maybe cache helps latency.")
                weak = store.propose("Cache helps latency.", [weak_event.id], 0.45)
                weak = store.mark_uncertain(weak.id, "single weak observation")
                pref_event = ledger.record("observation", "Surya prefers coffee.")
                pref = store.propose("Surya prefers coffee.", [pref_event.id], 0.6, tags=["preference", "drink"])
                self.assertEqual(label_record(fact), "FACT")
                self.assertEqual(label_record(store.propose("X.", [fact_event.id], 0.6)), "BELIEF")
                self.assertEqual(label_record(weak), "UNCERTAIN")
                self.assertEqual(label_record(pref), "PREFERENCE")
            finally:
                ledger.close()
                store.close()

    def test_section_is_bounded_and_relevant(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                event = ledger.record("observation", "Backups run on host vega.")
                store.propose("Backups run on host vega.", [event.id], 0.8)
                section = build_knowledge_section(store, "which host runs backups?")
                self.assertIn("[FACT", section)
                self.assertIn("vega", section)
                tiny = build_knowledge_section(store, "which host runs backups?", char_budget=10)
                self.assertEqual(tiny, "")
                with self.assertRaises(ValueError):
                    build_knowledge_section(store, "q", limit=0)
            finally:
                ledger.close()
                store.close()

    def test_chat_decide_includes_knowledge(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                event = ledger.record("observation", "Backups run on host vega.")
                store.propose("Backups run on host vega.", [event.id], 0.8)
                decide = build_chat_decide(EchoProvider(), knowledge_store=store)
                decision = decide(make_context(ledger, "which host runs backups?"))
                self.assertIn("vega", decision.response)  # echo carries the section
                prompt = build_user_prompt(make_context(ledger, "hosts?"), "custom section")
                self.assertIn("custom section", prompt)
            finally:
                ledger.close()
                store.close()


class InspectorExpansionTests(unittest.TestCase):
    def test_knowledge_and_sources_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                event = ledger.record("observation", "Backups run on host vega.")
                store.propose("Backups run on host vega.", [event.id], 0.8)
                handled, out = handle_command(ledger, "/knowledge backup host", store=store)
                self.assertTrue(handled)
                self.assertIn("vega", out)
                handled, out = handle_command(ledger, "/sources 1", store=store)
                self.assertTrue(handled)
                self.assertIn("supporting", out)
                handled, out = handle_command(ledger, "/sources nope", store=store)
                self.assertTrue(handled)
                self.assertIn("usage", out)
                handled, out = handle_command(ledger, "/knowledge x")
                self.assertEqual(out, "(knowledge store not attached)")
            finally:
                ledger.close()
                store.close()

    def test_reflect_and_consolidate_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store = open_pair(tmp)
            try:
                ledger.record("outcome", "Deploy worked using the checklist.", {"outcome": "success"})
                ledger.record("outcome", "Deploy worked using the checklist twice.", {"outcome": "success"})
                handled, out = handle_command(ledger, "/reflect")
                self.assertTrue(handled)
                self.assertIn("success_pattern", out)
                handled, out = handle_command(ledger, "/consolidate 5", store=store)
                self.assertTrue(handled)
                self.assertIn("created=", out)
                handled, out = handle_command(ledger, "/consolidate")
                self.assertEqual(out, "(knowledge store not attached)")
            finally:
                ledger.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
