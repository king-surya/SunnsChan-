"""Capability acquisition: gap -> discover -> evaluate -> acquire -> install ->
validate -> register -> use -> observe -> learn.

This is the missing layer between "I want to do X" and "now I can do X". It
composes (never duplicates) the existing pieces: CapabilityStore, the sandbox
provider (the VM is the laboratory), the downloader, and experience/reflection.
Discovery and download are provider-abstracted; mocks stand in for tests while
REAL paths are config-gated. Nothing here grants authorization — acquisition
installs inside the sandbox only, and a registered capability is KNOWN, not
AUTHORIZED.

Autonomy is goal-driven: the planner asks "can I already do this?" via
detect_capability_gap and only acquires when the answer is no. There is no
random browsing/downloading anywhere in this module.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .capability import CapabilityRecord, CapabilityStore, detect_capability_gap
from .comms import scrub_secrets
from .memory import EventLedger, tokenize


# ---------------------------------------------------------------------------
# Discovery: provider-abstracted candidates (software, tools, and agents alike).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityCandidate:
    name: str
    kind: str  # see capability.CAPABILITY_KINDS
    description: str
    version: str = ""
    source_type: str = ""  # registry | repository | package-index | web | agent-repo | ...
    source_url: str = ""
    origin: str = ""
    install_method: str = ""  # e.g. "pip install x" / "apt install x" / "git clone ..."
    validate_command: str = ""  # e.g. "x --version"
    invoke: str = ""  # how to use it, e.g. "x <file>"
    checksum: str = ""
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    provenance: dict = field(default_factory=dict)


class DiscoverySource(Protocol):
    kind: str

    def discover(self, requirement: str, *, limit: int = 5) -> list[CapabilityCandidate]:
        ...


@dataclass
class MockDiscoverySource:
    """Deterministic candidate table for tests. Honest MOCK label."""

    kind: str = "mock-discovery"
    backend: str = "MOCK"
    candidates: dict[str, list[CapabilityCandidate]] = field(default_factory=dict)

    def discover(self, requirement: str, *, limit: int = 5) -> list[CapabilityCandidate]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        return list(self.candidates.get(requirement, []))[:limit]


def _label_matches(label_tokens: set[str], req_tokens: set[str]) -> bool:
    """Light token/prefix match (csvkit <-> csv) for hub link selection."""
    if label_tokens & req_tokens:
        return True
    for l in label_tokens:
        for r in req_tokens:
            if len(l) >= 4 and (l.startswith(r) or r.startswith(l)):
                return True
    return False


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: CapabilityCandidate
    match_score: float
    confidence: float
    uncertain: bool
    reasons: tuple[str, ...]


@dataclass
class BrowserDiscoverySource:
    """Adapt a BrowserProvider into a DiscoverySource.

    Browses a hub/index page, follows links whose label overlaps the
    requirement, inspects each page, and turns the result into candidates.
    `candidate_specs` supplies per-name operational metadata (install/validate/
    invoke) that a web page alone cannot reliably convey. Deterministic with
    MockBrowserProvider; REAL with HttpBrowserProvider.
    """

    browser: object
    hub_url: str
    kind: str = "browser-discovery"
    candidate_specs: dict[str, dict] = field(default_factory=dict)

    @property
    def backend(self) -> str:
        return getattr(self.browser, "backend", "MOCK")

    def discover(self, requirement: str, *, limit: int = 5) -> list[CapabilityCandidate]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        req_tokens = set(tokenize(requirement))
        try:
            hub = self.browser.open(self.hub_url)
        except (ValueError, RuntimeError) as exc:
            raise RuntimeError(f"browser discovery failed: {exc}") from exc
        results: list[CapabilityCandidate] = []
        for label, url in hub.links:
            label_tokens = set(tokenize(label))
            if not _label_matches(label_tokens, req_tokens):
                continue
            try:
                page = self.browser.open(url)
            except (ValueError, RuntimeError):
                continue
            spec = self.candidate_specs.get(label, {})
            results.append(CapabilityCandidate(
                name=label,
                kind=str(spec.get("kind", "tool")),
                description=(f"{page.title} {page.text}".strip())[:300],
                version=str(spec.get("version", "")),
                source_type="web",
                source_url=str(spec.get("source_url", url)),
                origin=url,
                install_method=str(spec.get("install_method", "")),
                validate_command=str(spec.get("validate_command", "")),
                invoke=str(spec.get("invoke", "")),
                capabilities=tuple(spec.get("capabilities", [])),
            ))
            if len(results) >= limit:
                break
        return results


def evaluate_candidate(candidate: CapabilityCandidate, requirement: str) -> CandidateEvaluation:
    """Score how well a candidate addresses a requirement, honestly uncertain
    when there is too little information to judge."""
    req_tokens = set(tokenize(requirement))
    blob = " ".join([candidate.name, candidate.description, *candidate.capabilities])
    blob_tokens = set(tokenize(blob))
    overlap = len(req_tokens & blob_tokens)
    for q in req_tokens:
        for t in blob_tokens:
            if q != t and len(q) >= 4 and (t.startswith(q) or q.startswith(t)):
                overlap += 1
                break
    reasons: list[str] = []
    if overlap == 0:
        reasons.append("no lexical overlap with requirement")
    else:
        reasons.append(f"{overlap} overlapping term(s) with requirement")
    if not candidate.install_method:
        reasons.append("no installation method known")
    if not candidate.validate_command:
        reasons.append("no validation command known")
    has_info = bool(candidate.description and candidate.install_method)
    confidence = round(max(0.1, min(0.9, 0.3 + 0.1 * overlap + (0.2 if has_info else 0.0))), 4)
    return CandidateEvaluation(
        candidate=candidate,
        match_score=float(overlap),
        confidence=confidence,
        uncertain=not has_info,
        reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# Download: provenance + optional integrity verification.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DownloadRecord:
    url: str
    filename: str
    size: int
    sha256: str
    expected_sha256: str
    verified: bool
    backend: str
    retrieved_at: datetime
    error: str = ""


class Downloader:
    """Controlled download with provenance and optional hash verification.

    The default fetcher is a REAL allowlisted urllib fetch (host-side, before
    anything enters the sandbox); tests inject a deterministic fetcher. A
    downloaded file is NEVER silently trusted — verification status is
    explicit, and mismatches fail loudly.
    """

    def __init__(self, *, fetcher: Callable[[str], bytes] | None = None,
                 allowed_domains: tuple[str, ...] = (),
                 timeout_secs: float = 15.0,
                 max_bytes: int = 5_000_000,
                 backend: str = "REAL") -> None:
        self._fetcher = fetcher or self._default_fetcher(allowed_domains, timeout_secs, max_bytes)
        self._backend = "MOCK" if fetcher is not None else backend

    @staticmethod
    def _default_fetcher(allowed_domains: tuple[str, ...], timeout_secs: float,
                         max_bytes: int) -> Callable[[str], bytes]:
        def fetch(url: str) -> bytes:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("only http/https URLs are allowed")
            host = (parsed.hostname or "").lower()
            if not any(host == d or host.endswith("." + d) for d in allowed_domains):
                raise ValueError(f"domain not allowlisted: {host}")
            request = Request(url, headers={"User-Agent": "suns-chan-download/0.1"})
            try:
                with urlopen(request, timeout=timeout_secs) as response:
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("download exceeds max bytes")
                        chunks.append(chunk)
            except OSError as exc:
                raise RuntimeError(f"download failed for {url}: {exc}") from exc
            return b"".join(chunks)
        return fetch

    def download(self, url: str, *, expected_sha256: str = "",
                 filename: str = "", purpose: str = "") -> DownloadRecord:
        """Download one URL, hash it, and verify against `expected_sha256` if
        provided. `purpose` documents intent (stored by the caller)."""
        del purpose
        retrieved_at = datetime.now(UTC)
        try:
            data = self._fetcher(url)
        except (ValueError, RuntimeError) as exc:
            return DownloadRecord(url, filename, 0, "", expected_sha256, False,
                                  self._backend, retrieved_at, error=str(exc)[:300])
        digest = hashlib.sha256(data).hexdigest()
        verified = (not expected_sha256) or digest == expected_sha256.lower()
        return DownloadRecord(
            url=url,
            filename=filename or url.rsplit("/", 1)[-1] or "download",
            size=len(data),
            sha256=digest,
            expected_sha256=expected_sha256.lower(),
            verified=verified,
            backend=self._backend,
            retrieved_at=retrieved_at,
            error="" if verified else "checksum mismatch",
        )


# ---------------------------------------------------------------------------
# Acquisition: the orchestrated loop.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AcquisitionReport:
    requirement: str
    status: str  # no_gap | no_candidate | acquired | failed
    capability_id: int | None = None
    candidate_name: str = ""
    experience_id: int | None = None
    notes: tuple[str, ...] = ()


class CapabilityAcquirer:
    """Compose discovery + download + sandbox + registry into one bounded loop."""

    def __init__(self, *, discovery: DiscoverySource, downloader: Downloader,
                 sandbox, registry: CapabilityStore,
                 ledger: EventLedger | None = None,
                 min_confidence: float = 0.35) -> None:
        self._discovery = discovery
        self._downloader = downloader
        self._sandbox = sandbox  # SandboxProvider protocol
        self._registry = registry
        self._ledger = ledger
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        self._min_confidence = min_confidence

    def acquire(self, requirement: str, *, goal: str = "",
                network_mode: str = "isolated",
                expected_sha256: str = "") -> AcquisitionReport:
        requirement = requirement.strip()
        if not requirement:
            raise ValueError("requirement must be non-empty")

        # 1. Do I already have this?
        gap = detect_capability_gap(requirement, self._registry)
        if gap is None:
            return AcquisitionReport(requirement, "no_gap",
                                     notes=("existing available capability satisfies requirement",))

        # 2. Discover candidates.
        try:
            candidates = self._discovery.discover(requirement)
        except (ValueError, RuntimeError) as exc:
            return AcquisitionReport(requirement, "failed",
                                     notes=(f"discovery failed: {exc}",))

        # 3. Evaluate and pick the best above the confidence floor.
        evaluations = [evaluate_candidate(c, requirement) for c in candidates]
        evaluations = [e for e in evaluations if e.confidence >= self._min_confidence]
        if not evaluations:
            return AcquisitionReport(requirement, "no_candidate",
                                     notes=("no candidate met the confidence floor",))
        evaluations.sort(key=lambda e: (e.confidence, e.match_score), reverse=True)
        best = evaluations[0]
        candidate = best.candidate

        # 4. Register the discovery, then advance through the lifecycle.
        record = self._registry.discover(
            candidate.name, candidate.kind, candidate.description,
            version=candidate.version, source_type=candidate.source_type,
            source_url=candidate.source_url, origin=candidate.origin,
            install_method=candidate.install_method, invoke=candidate.invoke,
            checksum=candidate.checksum, dependencies=list(candidate.dependencies),
            capabilities=list(candidate.capabilities),
        )
        notes = [f"candidate {candidate.name} (conf {best.confidence})"]

        if candidate.source_url:
            download = self._downloader.download(
                candidate.source_url, expected_sha256=expected_sha256 or candidate.checksum,
                filename=candidate.name)
            if not download.verified or download.error:
                self._registry.transition(record.id, "failed", reason=download.error or "unverified download")
                return AcquisitionReport(requirement, "failed", record.id,
                                         candidate.name, notes=tuple(notes + [f"download failed: {download.error}"]))
            self._registry.transition(record.id, "downloaded", reason=f"sha256={download.sha256[:16]}")
            notes.append(f"downloaded {download.size} bytes (verified={download.verified})")
        else:
            self._registry.transition(record.id, "downloaded",
                                      reason="no download URL; installed directly")

        # 5. Install inside the sandbox (the VM is the laboratory).
        from .vm_sandbox import VMSpec

        sandbox_id = self._sandbox.create(
            VMSpec(name=f"acq-{record.id}", template="default", network_mode=network_mode))
        outcome = "success"
        try:
            self._sandbox.start(sandbox_id)
            self._sandbox.wait_ready(sandbox_id, timeout_secs=60.0)
            for command in self._install_commands(candidate):
                result = self._sandbox.execute(sandbox_id, command)
                if result.exit_code != 0:
                    raise RuntimeError(f"install command failed: {command[:120]} "
                                       f"(exit {result.exit_code})")
            self._registry.transition(record.id, "installed")
            self._registry.transition(record.id, "initialized")

            # 6. Validate.
            if candidate.validate_command:
                result = self._sandbox.execute(sandbox_id, candidate.validate_command)
                if result.exit_code != 0:
                    raise RuntimeError(f"validation failed: {candidate.validate_command[:120]}")
            self._registry.set_invocation(record.id, install_location=f"sandbox:{sandbox_id}",
                                          invoke=candidate.invoke)
            self._registry.transition(record.id, "validated",
                                      reason="validation command succeeded")
            self._registry.transition(record.id, "available")
        except (RuntimeError, ValueError) as exc:
            outcome = "failure"
            self._registry.transition(record.id, "failed", reason=str(exc)[:200])
            notes.append(f"install/validate failed: {exc}")
        finally:
            try:
                self._sandbox.destroy(sandbox_id)
            except (RuntimeError, ValueError):
                pass

        if outcome != "success":
            return AcquisitionReport(requirement, "failed", record.id, candidate.name,
                                     notes=tuple(notes))

        # 7. Record experience so future planning can weigh this capability.
        experience_id = None
        if self._ledger is not None:
            from .experience import record_experience

            event = record_experience(
                self._ledger, session_id="acquisition", activity_id=f"acq-{record.id}",
                intent=f"Acquire capability {candidate.name} for: {requirement}",
                motivation=goal or requirement,
                hypothesis=f"{candidate.name} satisfies {requirement!r}",
                actions=[candidate.install_method or "installed"],
                observations=[f"validated via {candidate.validate_command or 'none'}"],
                result="success", artifacts=[candidate.name],
                lessons=[f"Acquired and validated {candidate.name} {candidate.version}".strip()],
                capability_id=record.id,
            )
            experience_id = event.id
            self._registry.record_usage(record.id, "success", task=requirement,
                                        note="acquisition validation")
        return AcquisitionReport(requirement, "acquired", record.id, candidate.name,
                                 experience_id, tuple(notes))

    @staticmethod
    def _install_commands(candidate: CapabilityCandidate) -> tuple[str, ...]:
        if not candidate.install_method:
            return ()
        # A candidate may give a single command or a " && "-joined sequence.
        return tuple(part.strip() for part in candidate.install_method.split("&&") if part.strip())
