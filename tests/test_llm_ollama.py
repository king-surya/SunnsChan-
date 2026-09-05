import json
import unittest
from unittest.mock import patch

from suns_chan import LLMMessage, LLMRequest, provider_from_settings
from suns_chan.providers.ollama import OllamaProvider


class FakeHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


def make_request() -> LLMRequest:
    return LLMRequest(messages=(LLMMessage("user", "hello"),))


class OllamaProviderTests(unittest.TestCase):
    def test_factory_builds_ollama_without_network(self) -> None:
        provider = provider_from_settings("ollama", "llama3.1")
        self.assertIsInstance(provider, OllamaProvider)
        self.assertEqual(provider.model, "llama3.1")

    def test_generate_parses_response(self) -> None:
        body = json.dumps({"response": "hi there", "prompt_eval_count": 3, "eval_count": 5}).encode()
        with patch("suns_chan.providers.ollama.urlopen", return_value=FakeHTTPResponse(body)):
            resp = OllamaProvider(model="llama3.1").generate(make_request())
        self.assertEqual(resp.text, "hi there")
        self.assertEqual((resp.prompt_tokens, resp.completion_tokens), (3, 5))

    def test_generate_sends_json_format_flag(self) -> None:
        seen: dict = {}
        body = json.dumps({"response": '{"a": 1}'}).encode()

        def fake_open(request, timeout=None):
            seen.update(json.loads(request.data.decode()))
            return FakeHTTPResponse(body)

        with patch("suns_chan.providers.ollama.urlopen", side_effect=fake_open):
            OllamaProvider().generate(LLMRequest(messages=(LLMMessage("user", "x"),), json_mode=True))
        self.assertEqual(seen.get("format"), "json")
        self.assertFalse(seen.get("stream"))

    def test_unreachable_server_raises_instead_of_faking(self) -> None:
        with patch("suns_chan.providers.ollama.urlopen", side_effect=OSError("refused")):
            with self.assertRaises(RuntimeError):
                OllamaProvider().generate(make_request())

    def test_invalid_payload_raises(self) -> None:
        with patch("suns_chan.providers.ollama.urlopen", return_value=FakeHTTPResponse(b"not json")):
            with self.assertRaises(RuntimeError):
                OllamaProvider().generate(make_request())
        empty = json.dumps({"response": "  "}).encode()
        with patch("suns_chan.providers.ollama.urlopen", return_value=FakeHTTPResponse(empty)):
            with self.assertRaises(RuntimeError):
                OllamaProvider().generate(make_request())


if __name__ == "__main__":
    unittest.main()
