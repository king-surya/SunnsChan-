"""Experience-driven development tests (second Phase 6 charter).

Association, reconsideration, sub-skills, interest change, reinterpretation,
self-model — behavior first, structure second.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import (
    CuriosityStore,
    EventLedger,
    KnowledgeStore,
    LearnedStore,
    associate,
    build_self_model,
    record_experience,
    tend_interests,
)
from suns_chan.graph import build_index
from suns_chan.learned import mine_skills
from suns_chan.state import AgentState


def open_all(tmp: str):
    db = Path(tmp) / "m.db"
    return EventLedger(db), KnowledgeStore(db), LearnedStore(db), CuriosityStore(db)


def close_all(ledger, store, learned, curiosities) -> None:
    ledger.close()
    store.close()
    learned.close()
    curiosities.close()


class DevelopmentTests(unittest.TestCase):
    def test_b_association_finds_related(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned, curiosities = open_all(tmp)
            try:
                first = record_experience(ledger, session_id="s", activity_id="s-a1",
                                          intent="Scheduler latency probe shows p99 two seconds",
                                          result="failure")
                second = record_experience(ledger, session_id="s", activity_id="s-a2",
                                           intent="Scheduler latency probe retry shows p99 still high",
                                           result="failure",
                                           related={"follow_up_of": [first.id]})
                ledger.record("observation", "Unrelated cooking note about rendang.")
                index = build_index(ledger)
                hits = associate(ledger, second.id, index=index)
                self.assertTrue(any(h.event_id == first.id for h in hits))
                channels = {h.event_id: h.via for h in hits if h.event_id == first.id}
                self.assertTrue(channels)
                # Related history outranks recency noise.
                self.assertEqual(hits[0].event_id, first.id)
            finally:
                close_all(ledger, store, learned, curiosities)

    def test_e_old_understanding_reconsidered(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned, curiosities = open_all(tmp)
            try:
                event = ledger.record("observation", "Cache always helps.")
                claim = store.propose("Cache always helps.", [event.id], 0.7)
                contra = ledger.record("observation", "Correction: cache hurt tail latency here.")
                revised = store.contradict(claim.id,
                                           "Cache helps except under tail-latency pressure.",
                                           [contra.id], 0.6)
                _ = revised
                self.assertEqual(store.get(claim.id).status, "contradicted")
                explanation = store.explain(claim.id)
                self.assertTrue(explanation.contradicted_by)
                # Original event untouched; understanding moved on.
                self.assertEqual(ledger.events_of_kind("observation")[1].text,
                                 "Cache always helps.")
            finally:
                close_all(ledger, store, learned, curiosities)

    def test_g_subskills_revisable(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned, curiosities = open_all(tmp)
            try:
                for i in range(3):
                    ledger.record("activity_started", f"Probe DNS {i}",
                                  {"activity_key": f"d{i}", "category": "INVESTIGATE"})
                    ledger.record("activity_finished", "dns ok",
                                  {"activity_key": f"d{i}", "result": "success"})
                mine_skills(ledger, learned)
                children = [o for o in learned.by_kind("skill")
                            if o.subject.startswith("skill:investigate:")]
                self.assertTrue(children)
                child = children[0]
                self.assertEqual(child.status, "established")
                # A failure revises the sub-skill without deleting its history.
                ledger.record("activity_started", "Probe DNS 9",
                              {"activity_key": "dx", "category": "INVESTIGATE"})
                ledger.record("activity_finished", "dns failed",
                              {"activity_key": "dx", "result": "failure"})
                before = child.support_count
                mine_skills(ledger, learned)
                after = next(o for o in learned.by_kind("skill") if o.id == child.id)
                self.assertEqual(after.support_count, before)
                self.assertEqual(after.contra_count, 1)
            finally:
                close_all(ledger, store, learned, curiosities)

    def test_h_interests_change(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned, curiosities = open_all(tmp)
            try:
                state = AgentState(interests={"sourdough": 0.5, "routers": 0.5})
                ledger.save_state(state.to_dict(), cause_event_id=None)
                ledger.record("observation", "BGP routers need new firmware.")
                now = datetime.now(UTC)
                changed = tend_interests(ledger, idle_days=30, now=now)
                self.assertIn("sourdough", changed)  # weakened, unsupported
                self.assertNotIn("routers", changed)  # recent mention keeps it
                self.assertLess(ledger.load_state()["interests"]["sourdough"], 0.5)
                # Years of silence: disappears, history intact.
                future = now + timedelta(days=400)
                for _ in range(8):
                    tend_interests(ledger, idle_days=30, now=future)
                self.assertNotIn("sourdough", ledger.load_state()["interests"])
                self.assertTrue(ledger.events_of_kind("observation"))
                with self.assertRaises(ValueError):
                    tend_interests(ledger, idle_days=0)
            finally:
                close_all(ledger, store, learned, curiosities)

    def test_k_reinterpretation_preserves_history(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned, curiosities = open_all(tmp)
            try:
                old_event = ledger.record("observation", "Latency spikes are random.")
                old_claim = store.propose("Latency spikes are random.", [old_event.id], 0.6)
                week_later = ledger.record(
                    "observation", "Reinterpretation: spikes correlate with cron; not random.")
                new_claim = store.supersede(
                    old_claim.id, "Latency spikes follow the cron schedule.", [week_later.id],
                    0.75, reason="later evidence reinterprets E%d" % old_event.id)
                # Both events and both claims survive; the chain explains itself.
                self.assertEqual(len(ledger.events_of_kind("observation")), 2)
                self.assertEqual(store.get(old_claim.id).status, "superseded")
                self.assertEqual(new_claim.supersedes_id, old_claim.id)
                index = build_index(ledger, knowledge=store)
                paths = index.query("knowledge", str(old_claim.id))
                self.assertTrue(any(p.nodes[-1].key == str(new_claim.id) for p in paths))
            finally:
                close_all(ledger, store, learned, curiosities)

    def test_self_model_derived_and_revisable(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned, curiosities = open_all(tmp)
            try:
                from suns_chan.learned import mine_preferences, mine_skills

                for i in range(3):
                    ledger.record("activity_started", f"Probe {i}",
                                  {"activity_key": f"k{i}", "category": "INVESTIGATE"})
                    ledger.record("activity_finished", "ok",
                                  {"activity_key": f"k{i}", "result": "success"})
                    ledger.record("activity_started", f"Explore {i}",
                                  {"activity_key": f"e{i}", "category": "EXPLORE"})
                mine_skills(ledger, learned)
                mine_preferences(ledger, learned)
                origin = ledger.record("observation", "Routing tables confuse me.")
                curiosities.open("How does routing actually work?", origin.id, topic="routing")
                state = AgentState(interests={"routing": 0.6})
                model = build_self_model(ledger, learned, curiosities=curiosities, state=state)
                self.assertTrue(any("skill:investigate" in s for s in model.strengths))
                self.assertTrue(any("prefers:explore" in s for s in model.enjoys))
                self.assertIn("routing", model.returns_to)
                self.assertTrue(model.curiosities)
                # New contradictory evidence revises the model on rebuild.
                for i in range(4):
                    ledger.record("activity_started", f"Bad {i}",
                                  {"activity_key": f"z{i}", "category": "INVESTIGATE"})
                    ledger.record("activity_finished", "bad",
                                  {"activity_key": f"z{i}", "result": "failure"})
                mine_skills(ledger, learned)
                revised = build_self_model(ledger, learned, curiosities=curiosities, state=state)
                self.assertTrue(any("investigate" in w for w in revised.weaknesses))
                self.assertNotEqual(model.strengths, revised.strengths + revised.weaknesses)
            finally:
                close_all(ledger, store, learned, curiosities)

    def test_affect_recorded_in_experience(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger, store, learned, curiosities = open_all(tmp)
            try:
                exp = record_experience(ledger, session_id="s", activity_id="s-a1",
                                        intent="Probe", result="success",
                                        affect={"energy": 0.9, "bogus": 1.0, "rhythm": "steady"})
                self.assertEqual(exp.metadata["affect"]["energy"], 0.9)
                self.assertNotIn("bogus", exp.metadata["affect"])
                plain = record_experience(ledger, session_id="s", activity_id="s-a2",
                                          intent="Probe again", result="success")
                self.assertEqual(plain.metadata["affect"], {})
            finally:
                close_all(ledger, store, learned, curiosities)


if __name__ == "__main__":
    unittest.main()
