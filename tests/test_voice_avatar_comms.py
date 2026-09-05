from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suns_chan import EventLedger, PolicyGate
from suns_chan.avatar import MockAvatar, build_presentation, present_text
from suns_chan.comms import (
    CommunicationProposal,
    DispatchResult,
    MockChannel,
    approval_brief,
    dispatch,
    scrub_secrets,
)
from suns_chan.voice import MockVoiceInput, MockVoiceOutput, VoiceBus


def open_ledger(tmp: str) -> EventLedger:
    return EventLedger(Path(tmp) / "m.db")


class VoiceTests(unittest.TestCase):
    def test_mock_roundtrip_and_text_fallback(self) -> None:
        bus = VoiceBus(MockVoiceInput(script={b"\x01": "hello there"}), MockVoiceOutput())
        self.assertTrue(bus.input_enabled and bus.output_enabled)
        self.assertEqual(bus.to_text(b"\x01"), "hello there")
        audio = bus.to_audio("hi back")
        self.assertTrue(audio.startswith(b"[mock-audio:"))
        with self.assertRaises(ValueError):
            bus.to_text(b"\xff")

    def test_text_mode_without_providers(self) -> None:
        bus = VoiceBus()
        self.assertFalse(bus.input_enabled)
        with self.assertRaises(RuntimeError):
            bus.to_text(b"\x01")
        with self.assertRaises(RuntimeError):
            bus.to_audio("hi")


class AvatarTests(unittest.TestCase):
    def test_core_works_without_avatar(self) -> None:
        state = build_presentation("Hello, Surya.")
        self.assertEqual(present_text(state), "Hello, Surya.")
        with self.assertRaises(ValueError):
            build_presentation("  ")

    def test_mock_adapter_renders_snapshot(self) -> None:
        adapter = MockAvatar()
        state = build_presentation("Working on it.", expression="focused",
                                   attention="exploit", activity="Probe disks",
                                   affect_note="rhythm=steady")
        out = present_text(state, adapter)
        self.assertIn("Working on it.", out)
        self.assertIn("mock-avatar", out)
        self.assertEqual(adapter.rendered[0].activity, "Probe disks")


class CommsTests(unittest.TestCase):
    def test_unapproved_never_executes(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                channel = MockChannel()
                proposal = CommunicationProposal(
                    "console", "surya", "Deploy finished.", "daily note",
                    evidence_ids=(3,), confidence=0.8)
                result = dispatch(proposal, PolicyGate(), None, channel, ledger)
                self.assertIsInstance(result, DispatchResult)
                self.assertFalse(result.sent)
                self.assertEqual(channel.outbox, [])
                events = ledger.events_of_kind("communication")
                self.assertEqual(len(events), 1)
                self.assertFalse(events[0].metadata["sent"])
            finally:
                ledger.close()

    def test_approved_sends_and_audits(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                channel = MockChannel()
                seen: list = []
                proposal = CommunicationProposal("webhook", "ops", "All green.", "status")
                result = dispatch(proposal, PolicyGate(), lambda p: True, channel, ledger,
                                  auditor=lambda c, m, s: seen.append((c, s)))
                self.assertTrue(result.sent)
                self.assertTrue(result.receipt.startswith("mock-receipt"))
                self.assertEqual(len(channel.outbox), 1)
                self.assertEqual(seen, [("webhook", True)])
            finally:
                ledger.close()

    def test_secrets_scrubbed_and_brief_complete(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = open_ledger(tmp)
            try:
                channel = MockChannel()
                proposal = CommunicationProposal(
                    "webhook", "ops", "Deploy done. token: abc123 Bearer xyz.9",
                    "status update")
                dispatch(proposal, PolicyGate(), lambda p: True, channel, ledger)
                text = ledger.events_of_kind("communication")[0].text
                self.assertNotIn("abc123", text)
                self.assertNotIn("xyz.9", text)
                self.assertIn("[REDACTED]", text)
                brief = approval_brief(proposal)
                for required in ("WHAT", "WHY", "EVIDENCE", "AFFECTED", "EXACT CONTENT"):
                    self.assertIn(required, brief)
                with self.assertRaises(ValueError):
                    CommunicationProposal(" ", "r", "c", "why")
            finally:
                ledger.close()

    def test_scrub_unit(self) -> None:
        self.assertIn("[REDACTED]", scrub_secrets("password= hunter2 ok"))


if __name__ == "__main__":
    unittest.main()
