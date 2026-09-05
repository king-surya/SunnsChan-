import unittest
from unittest.mock import patch

from suns_chan import (
    BrowserDocument,
    HttpBrowserProvider,
    MockBrowserProvider,
)


class MockBrowserTests(unittest.TestCase):
    def _browser(self) -> MockBrowserProvider:
        return MockBrowserProvider(
            pages={
                "https://hub.example/": {
                    "title": "Tool Hub",
                    "text": "A directory of tools.",
                    "links": [("csvkit", "https://hub.example/csvkit")],
                },
                "https://hub.example/csvkit": {
                    "title": "csvkit",
                    "text": "A suite of command-line tools for CSV.",
                    "links": [],
                },
            },
            files={"https://hub.example/csvkit-1.0.tar.gz": b"fake-tarball-bytes"},
        )

    def test_open_navigate_inspect_follow(self) -> None:
        browser = self._browser()
        doc = browser.open("https://hub.example/")
        self.assertEqual(doc.backend, "MOCK")
        self.assertEqual(doc.title, "Tool Hub")
        self.assertIn("csvkit", [label for label, _ in doc.links])
        self.assertEqual(browser.current().url, "https://hub.example/")
        follow = browser.follow("csvkit")
        self.assertEqual(follow.url, "https://hub.example/csvkit")
        self.assertIn("CSV", follow.text)
        self.assertEqual(browser.history(), ("https://hub.example/", "https://hub.example/csvkit"))

    def test_download(self) -> None:
        browser = self._browser()
        data = browser.download("https://hub.example/csvkit-1.0.tar.gz")
        self.assertEqual(data, b"fake-tarball-bytes")

    def test_unknown_page_raises(self) -> None:
        browser = self._browser()
        with self.assertRaises(ValueError):
            browser.open("https://hub.example/missing")


class HttpBrowserTests(unittest.TestCase):
    class FakeHTTPResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self, _size: int = 0) -> bytes:
            if self._body:
                data, self._body = self._body, b""
                return data
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    def test_open_extracts_links_and_follow(self) -> None:
        html = b"<html><title>Repo</title><body><a href='/x'>X</a><p>hello world</p></body></html>"
        browser = HttpBrowserProvider(allowed_domains=("example.org",))
        with patch("suns_chan.browser.urlopen",
                   return_value=self.FakeHTTPResponse(html)):
            doc = browser.open("https://example.org/home")
        self.assertEqual(doc.backend, "REAL")
        self.assertEqual(doc.title, "Repo")
        self.assertIn("hello world", doc.text)
        self.assertEqual(doc.links, (("X", "https://example.org/x"),))

    def test_domain_not_allowlisted_rejected(self) -> None:
        browser = HttpBrowserProvider(allowed_domains=("example.org",))
        with self.assertRaises(ValueError):
            browser.open("https://evil.example/")


if __name__ == "__main__":
    unittest.main()
