import unittest
from unittest.mock import patch

from suns_chan.sources import (
    HttpSource,
    MockApiSource,
    MockSearchSource,
    MockWebSource,
    SearchHit,
    extract_text,
)


class FakeHTTPResponse:
    def __init__(self, payloads: list[bytes]) -> None:
        self._payloads = list(payloads)

    def read(self, _size: int = 0) -> bytes:
        return self._payloads.pop(0) if self._payloads else b""

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


class SourceTests(unittest.TestCase):
    def test_extract_text(self) -> None:
        text, truncated = extract_text("<html><body><h1>Hi</h1><p>there<script>x()</script></p></body></html>")
        self.assertIn("Hi", text)
        self.assertNotIn("x()", text)
        self.assertFalse(truncated)
        long_text, truncated = extract_text("<p>" + "word " * 5000 + "</p>", max_chars=100)
        self.assertTrue(truncated)
        self.assertLessEqual(len(long_text), 100)

    def test_mock_web_and_search_and_api(self) -> None:
        web = MockWebSource(pages={"https://docs.example/x": "DNS resolver content here."})
        doc = web.fetch("https://docs.example/x")
        self.assertEqual(doc.backend, "MOCK")
        self.assertIn("DNS", doc.text)
        with self.assertRaises(ValueError):
            web.fetch("https://docs.example/missing")
        search = MockSearchSource(results={"dns": [SearchHit("https://docs.example/x", "DNS", "resolver")]})
        self.assertEqual(len(search.search("dns")), 1)
        self.assertEqual(search.search("unknown"), [])
        with self.assertRaises(ValueError):
            search.search("dns", limit=0)
        api = MockApiSource(records={"svc": [{"title": "status", "text": "all systems nominal"}]})
        self.assertEqual(api.query("svc")[0]["title"], "status")

    def test_http_allowlist_and_caps(self) -> None:
        source = HttpSource(allowed_domains=("docs.example",))
        with self.assertRaises(ValueError):
            source.fetch("https://evil.example/x")
        with self.assertRaises(ValueError):
            source.fetch("ftp://docs.example/x")
        body = b"<p>" + b"hello world " * 100 + b"</p>"
        with patch("suns_chan.sources.urlopen", return_value=FakeHTTPResponse([body, b""])):
            doc = source.fetch("https://docs.example/page")
        self.assertEqual(doc.backend, "REAL")
        self.assertIn("hello world", doc.text)
        with patch("suns_chan.sources.urlopen", side_effect=OSError("down")):
            with self.assertRaises(RuntimeError):
                source.fetch("https://docs.example/page")

    def test_http_empty_page_rejected(self) -> None:
        source = HttpSource(allowed_domains=("docs.example",))
        with patch("suns_chan.sources.urlopen", return_value=FakeHTTPResponse([b"<br>", b""])):
            with self.assertRaises(RuntimeError):
                source.fetch("https://docs.example/empty")


if __name__ == "__main__":
    unittest.main()
