"""Approval-gated external communication. Proposals are data; sending needs approval.

Flow: validate proposal -> PolicyGate (risk=message, never auto-allowed) ->
explicit approval callback -> channel.send -> audit ledger event. Unapproved
proposals NEVER execute. Secrets are scrubbed from every persisted artifact:
tokens/keys/passwords become [REDACTED]. Reuses ToolRegistry-compatible
ActionRequest semantics; no parallel security pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any, Callable, Protocol

from .memory import EventLedger
from .policy import ActionRequest, PolicyGate


_SECRET_PATTERN = re.compile(r"(?i)\b(token|secret|password|api[_-]?key|auth)\b\s*[:=]\s*\S+")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~-]+")


def scrub_secrets(text: str) -> str:
    text = _SECRET_PATTERN.sub("[REDACTED]", text)
    return _BEARER_PATTERN.sub("Bearer [REDACTED]", text)


@dataclass(frozen=True)
class CommunicationProposal:
    channel: str     # e.g. "console", "webhook", "email"
    recipient: str
    content: str
    reason: str
    activity_id: str = ""
    goal_id: int | None = None
    evidence_ids: tuple[int, ...] = ()
    confidence: float = 0.5
    created_at: datetime = datetime.now(UTC)

    def __post_init__(self) -> None:
        if not self.channel.strip() or not self.recipient.strip():
            raise ValueError("channel and recipient must be non-empty")
        if not self.content.strip() or not self.reason.strip():
            raise ValueError("content and reason must be non-empty")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)) \
                or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


class CommunicationChannel(Protocol):
    kind: str

    def send(self, proposal: CommunicationProposal) -> str:
        """Deliver; return a receipt id. Mocks must label receipts as mock."""
        ...


@dataclass
class MockChannel:
    """Records proposals instead of sending. Receipts say MOCK."""

    kind: str = "mock-channel"
    outbox: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.outbox = []

    def send(self, proposal: CommunicationProposal) -> str:
        self.outbox.append(proposal)
        return f"mock-receipt-{len(self.outbox)}"


@dataclass(frozen=True)
class DispatchResult:
    sent: bool
    verdict: str
    receipt: str = ""
    reason: str = ""


def approval_brief(proposal: CommunicationProposal) -> str:
    evidence = ", ".join(f"E{i}" for i in proposal.evidence_ids) or "(none)"
    return (
        f"WHAT: send via {proposal.channel} to {proposal.recipient}\n"
        f"WHY: {proposal.reason}\n"
        f"EVIDENCE: {evidence} (confidence {proposal.confidence})\n"
        f"AFFECTED: {proposal.recipient} via {proposal.channel}\n"
        f"EXACT CONTENT:\n{proposal.content}"
    )


def dispatch(
    proposal: CommunicationProposal,
    policy: PolicyGate,
    approver: Callable[[CommunicationProposal], bool] | None,
    channel: CommunicationChannel,
    ledger: EventLedger,
    *,
    auditor: Callable[[str, dict[str, Any], bool], None] | None = None,
) -> DispatchResult:
    """Validate -> policy -> approval -> execute -> audit. Never skips a stage."""
    verdict = policy.evaluate(ActionRequest(
        name=f"comm.{proposal.channel}", risk="message",
        description=f"Send to {proposal.recipient}: {proposal.content[:120]}",
        scope="external"))
    if verdict.value != "allow":
        approved = approver(proposal) if approver is not None else False
        if not approved:
            _audit(ledger, auditor, proposal, verdict.value, False, "")
            return DispatchResult(False, verdict.value, reason="approval denied or missing")
    receipt = channel.send(proposal)
    _audit(ledger, auditor, proposal, verdict.value, True, receipt)
    return DispatchResult(True, verdict.value, receipt=receipt)


def _audit(ledger: EventLedger, auditor: Callable | None, proposal: CommunicationProposal,
           verdict: str, sent: bool, receipt: str) -> None:
    safe_content = scrub_secrets(proposal.content)[:500]
    event = ledger.record("communication", (
        f"[{'sent' if sent else 'blocked'}:{proposal.channel}] to {proposal.recipient}: "
        f"{safe_content}"), {
        "channel": proposal.channel,
        "recipient": proposal.recipient,
        "verdict": verdict,
        "sent": sent,
        "receipt": receipt,
        "activity_id": proposal.activity_id,
        "evidence_ids": list(proposal.evidence_ids),
    })
    if auditor is not None:
        auditor(proposal.channel, {"event_id": event.id, "sent": sent}, sent)
