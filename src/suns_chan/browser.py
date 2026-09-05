"""Browser interaction abstraction: navigate the digital environment.

SEARCH is finding information; BROWSER is interacting with a digital
environment (open, navigate, inspect, follow links, download). The cognition
layer depends on the BrowserProvider protocol, never on one implementation.

Two backends ship here:
- MockBrowserProvider — deterministic in-memory page graph for tests (MOCK).
- HttpBrowserProvider — REAL fetch over an allowlist of domains (REAL), with
  crude-but-deterministic link extraction. No JS, no session persistence —
  this is a bounded document browser, not a full headless engine; heavier
  backends (Playwright etc.) attach later behind the same protocol.

Nothing here grants authorization; downloads are bytes-in, never trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .sources import extract_text


@dataclass(frozen=True)
class BrowserDocument:
    url: str
    title: str
    text: str
    links: tuple[tuple[str, str], ...]  # (label, url)
    backend: str  # MOCK | REAL


class BrowserProvider(Protocol):
    backend: str

    def open(self, url: str) -> BrowserDocument:
        ...

    def current(self) -> BrowserDocument | None:
        ...

    def links(self) -> tuple[tuple[str, str], ...]:
        ...

    def follow(self, target: str) -> BrowserDocument:
        """Follow a link by URL or by its label."""

    def download(self, url: str) -> bytes:
        ...

    def history(self) -> tuple[str, ...]:
        ...


_HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_TAG_STRIP = re.compile(r"<[^>]+>")


@dataclass
class MockBrowserProvider:
    """Deterministic page graph for tests. Honest MOCK label, no network."""

    backend: str = "MOCK"
    pages: dict[str, dict] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)

    def __init__(self, pages: dict[str, dict] | None = None,
                 files: dict[str, bytes] | None = None) -> None:
        self.pages = dict(pages or {})
        self.files = dict(files or {})
        self._current: BrowserDocument | None = None
        self._history: list[str] = []

    def _page(self, url: str) -> dict:
        try:
            return self.pages[url]
        except KeyError:
            raise ValueError(f"mock-browser has no page for {url} (MOCK)") from None

    def open(self, url: str) -> BrowserDocument:
        page = self._page(url)
        self._current = BrowserDocument(
            url, page.get("title", ""), page.get("text", ""),
            tuple(page.get("links", [])), "MOCK")
        self._history.append(url)
        return self._current

    def current(self) -> BrowserDocument | None:
        return self._current

    def links(self) -> tuple[tuple[str, str], ...]:
        return self._current.links if self._current else ()

    def follow(self, target: str) -> BrowserDocument:
        for label, url in self.links():
            if target == url or target == label:
                return self.open(url)
        raise ValueError(f"no link matches {target!r} on {self._current.url if self._current else '?'}")

    def download(self, url: str) -> bytes:
        if url in self.files:
            return self.files[url]
        # Fall back to page text as a downloadable document.
        page = self._page(url)
        return (page.get("text", "") or "").encode("utf-8")

    def history(self) -> tuple[str, ...]:
        return tuple(self._history)


@dataclass
class HttpBrowserProvider:
    """REAL document browser over allowlisted domains. No JS, no cache."""

    backend: str = "REAL"
    allowed_domains: tuple[str, ...] = ()
    timeout_secs: float = 15.0
    max_bytes: int = 1_000_000

    def __init__(self, allowed_domains: tuple[str, ...] = (),
                 timeout_secs: float = 15.0, max_bytes: int = 1_000_000) -> None:
        self.allowed_domains = allowed_domains
        self.timeout_secs = timeout_secs
        self.max_bytes = max_bytes
        self._current: BrowserDocument | None = None
        self._history: list[str] = []

    def _fetch(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("only http/https URLs are allowed")
        host = (parsed.hostname or "").lower()
        if not any(host == d or host.endswith("." + d) for d in self.allowed_domains):
            raise ValueError(f"domain not allowlisted: {host}")
        request = Request(url, headers={"User-Agent": "suns-chan-browser/0.1"})
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
                        raise ValueError("page exceeds max bytes")
                    chunks.append(chunk)
        except OSError as exc:
            raise RuntimeError(f"fetch failed for {url}: {exc}") from exc
        return b"".join(chunks).decode("utf-8", errors="replace")

    def open(self, url: str) -> BrowserDocument:
        html = self._fetch(url)
        title = _extract_title(html)
        text, _ = extract_text(html)
        links: list[tuple[str, str]] = []
        for target, label in _HREF_RE.findall(html):
            absolute = urljoin(url, target)
            links.append((_TAG_STRIP.sub("", label).strip() or absolute, absolute))
        self._current = BrowserDocument(url, title, text, tuple(links), "REAL")
        self._history.append(url)
        return self._current

    def current(self) -> BrowserDocument | None:
        return self._current

    def links(self) -> tuple[tuple[str, str], ...]:
        return self._current.links if self._current else ()

    def follow(self, target: str) -> BrowserDocument:
        for label, url in self.links():
            if target == url or target == label:
                return self.open(url)
        raise ValueError(f"no link matches {target!r}")

    def download(self, url: str) -> bytes:
        return self._fetch(url).encode("utf-8")

    def history(self) -> tuple[str, ...]:
        return tuple(self._history)


def _extract_title(html: str) -> str:
    match = _TITLE_RE.search(html)
    if not match:
        return ""
    return _TAG_STRIP.sub("", match.group(1)).strip()[:200]
