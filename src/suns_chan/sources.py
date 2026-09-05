"""External source adapters: fetch, search, and structured APIs.

Provider-independent core: SourceAdapter / SearchSource / ApiSource protocols
with mock implementations for deterministic tests. The only real network
path is HttpSource, bound by an explicit domain allowlist, byte caps, and
timeouts (broker fetch-policy parity). Real search providers are NOT
configured in this build — SearchSource documents the interface, and
MockSearchSource stands in with honest labels. Nothing here judges truth;
evaluation lives in ingest.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class FetchedDocument:
    url: str
    source_type: str  # web | api | local | mock
    text: str
    title: str = ""
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    truncated: bool = False
    backend: str = "MOCK"  # MOCK vs REAL — never mislabel


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str


class SourceAdapter(Protocol):
    kind: str

    def fetch(self, url: str) -> FetchedDocument:
        ...


class SearchSource(Protocol):
    kind: str

    def search(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        ...


class ApiSource(Protocol):
    kind: str

    def query(self, query: str) -> list[dict]:
        """Return structured records (each should carry title/text-ish fields)."""
        ...


_TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def extract_text(html: str, *, max_chars: int = 8000) -> tuple[str, bool]:
    """Crude but deterministic HTML→text. Not a readability engine."""
    text = _TAG_RE.sub(" ", html)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _host_allowed(host: str, allowed_domains: tuple[str, ...]) -> bool:
    host = host.lower()
    for domain in allowed_domains:
        d = domain.lower()
        if host == d or host.endswith("." + d):
            return True
    return False


@dataclass
class HttpSource:
    """REAL network fetch over allowlisted domains. Honest failures, no cache."""

    kind: str = "web-http"
    allowed_domains: tuple[str, ...] = ()
    timeout_secs: float = 15.0
    max_bytes: int = 1_000_000
    backend: str = "REAL"

    def fetch(self, url: str) -> FetchedDocument:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("only http/https URLs are allowed")
        host = parsed.hostname or ""
        if not _host_allowed(host, self.allowed_domains):
            raise ValueError(f"domain not allowlisted: {host}")
        request = Request(url, headers={"User-Agent": "suns-chan-knowledge/0.1"})
        try:
            with urlopen(request, timeout=self.timeout_secs) as response:
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise ValueError("fetch exceeds max bytes")
                    chunks.append(chunk)
        except OSError as exc:
            raise RuntimeError(f"fetch failed for {url}: {exc}") from exc
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        text, truncated = extract_text(raw)
        if not text.strip():
            raise RuntimeError(f"no extractable text at {url}")
        return FetchedDocument(url, "web", text, retrieved_at=datetime.now(UTC),
                               truncated=truncated, backend="REAL")


@dataclass
class MockWebSource:
    """Scripted fetch for tests. Unknown URLs raise instead of faking."""

    kind: str = "mock-web"
    pages: dict[str, str] = field(default_factory=dict)

    def fetch(self, url: str) -> FetchedDocument:
        try:
            text = self.pages[url]
        except KeyError:
            raise ValueError(f"mock-web has no page for {url} (MOCK)") from None
        return FetchedDocument(url, "mock", text, backend="MOCK")


@dataclass
class MockSearchSource:
    kind: str = "mock-search"
    results: dict[str, list[SearchHit]] = field(default_factory=dict)

    def search(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        return list(self.results.get(query, []))[:limit]


@dataclass
class MockApiSource:
    kind: str = "mock-api"
    records: dict[str, list[dict]] = field(default_factory=dict)

    def query(self, query: str) -> list[dict]:
        return [dict(r) for r in self.records.get(query, [])]
