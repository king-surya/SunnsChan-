import unittest

from suns_chan.ranking import (
    DEFAULT_WEIGHTS,
    RankedCandidate,
    ScoredCandidate,
    rank,
    recency_decay,
    weighted_score,
)


class RankingTests(unittest.TestCase):
    def test_contributions_sum_to_score(self) -> None:
        score, contributions = weighted_score({"keyword": 1.0, "recency": 0.5})
        self.assertAlmostEqual(score, sum(contributions.values()), places=6)
        self.assertGreater(contributions["keyword"], contributions["recency"])

    def test_custom_weights_change_order(self) -> None:
        candidates = [
            ScoredCandidate(1, {"keyword": 1.0, "recency": 0.0}),
            ScoredCandidate(2, {"keyword": 0.0, "recency": 1.0}),
        ]
        self.assertEqual(rank(candidates)[0].key, 1)
        recency_only = {k: (1.0 if k == "recency" else 0.0) for k in DEFAULT_WEIGHTS}
        self.assertEqual(rank(candidates, weights=recency_only)[0].key, 2)

    def test_tiebreak_is_deterministic(self) -> None:
        candidates = [ScoredCandidate(1, {"keyword": 0.5}, tiebreak=1.0), ScoredCandidate(2, {"keyword": 0.5}, tiebreak=2.0)]
        first = rank(candidates)[0]
        self.assertIsInstance(first, RankedCandidate)
        self.assertEqual(first.key, 2)
        self.assertEqual([r.key for r in rank(candidates)], [r.key for r in rank(candidates)])

    def test_invalid_signals_raise(self) -> None:
        with self.assertRaises(ValueError):
            weighted_score({"mystery": 1.0})
        with self.assertRaises(ValueError):
            weighted_score({"keyword": 1.5})
        with self.assertRaises(ValueError):
            weighted_score({"keyword": 1.0}, {"mystery": 1.0})
        with self.assertRaises(ValueError):
            weighted_score({}, {k: 0.0 for k in DEFAULT_WEIGHTS})
        with self.assertRaises(ValueError):
            rank([ScoredCandidate(1, {"keyword": 0.5})], limit=0)

    def test_recency_decay_monotonic(self) -> None:
        self.assertEqual(recency_decay(0), 1.0)
        values = [recency_decay(i) for i in range(6)]
        self.assertEqual(values, sorted(values, reverse=True))
        with self.assertRaises(ValueError):
            recency_decay(-1)


if __name__ == "__main__":
    unittest.main()
