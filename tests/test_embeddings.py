import unittest
from unittest.mock import patch

from suns_chan.embeddings import HashingEmbedder, OllamaEmbedder, cosine_similarity


class FakeHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


class HashingEmbedderTests(unittest.TestCase):
    def test_deterministic_and_normalized(self) -> None:
        embedder = HashingEmbedder()
        first = embedder.embed("the nginx server stopped")
        second = embedder.embed("the nginx server stopped")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 128)
        norm = sum(v * v for v in first) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=6)

    def test_similar_texts_score_higher(self) -> None:
        embedder = HashingEmbedder()
        query = embedder.embed("nginx service stopped")
        related = embedder.embed("nginx stopped after memory pressure")
        unrelated = embedder.embed("cooking rendang takes patience")
        self.assertGreater(
            cosine_similarity(query, related), cosine_similarity(query, unrelated)
        )

    def test_empty_text_gives_zero_vector(self) -> None:
        vector = HashingEmbedder().embed("the and")
        self.assertEqual(vector, [0.0] * 128)
        self.assertEqual(cosine_similarity(vector, vector), 0.0)

    def test_batch_matches_single(self) -> None:
        embedder = HashingEmbedder()
        texts = ["first text", "second text"]
        self.assertEqual(embedder.embed_batch(texts), [embedder.embed(t) for t in texts])


class OllamaEmbedderTests(unittest.TestCase):
    def test_batch_parses_vectors(self) -> None:
        import json as json_lib

        body = json_lib.dumps({"embeddings": [[1.0, 0.0], [0.0, 1.0]]}).encode()
        with patch("suns_chan.embeddings.urlopen", return_value=FakeHTTPResponse(body)):
            vectors = OllamaEmbedder().embed_batch(["a", "b"])
        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])

    def test_failures_raise(self) -> None:
        with patch("suns_chan.embeddings.urlopen", side_effect=OSError("down")):
            with self.assertRaises(RuntimeError):
                OllamaEmbedder().embed("hi")
        with patch("suns_chan.embeddings.urlopen", return_value=FakeHTTPResponse(b"bad")):
            with self.assertRaises(RuntimeError):
                OllamaEmbedder().embed("hi")


if __name__ == "__main__":
    unittest.main()
