import random
import time
import unittest

from solution import covered


def brute(intervals, k):
    points = sorted({p for s, e, _ in intervals for p in (s, e)})
    segments = []
    for a, b in zip(points, points[1:]):
        if sum(w for s, e, w in intervals if s <= a and b <= e) >= k:
            if segments and segments[-1][1] == a:
                segments[-1] = (segments[-1][0], b)
            else:
                segments.append((a, b))
    return segments


class CoveredTest(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(covered([(0, 4, 1), (2, 6, 1)], 2), [(2, 4)])
        self.assertEqual(covered([(0, 4, 1), (2, 6, 1)], 1), [(0, 6)])
        self.assertEqual(covered([(0, 2, 3), (2, 5, 3)], 3), [(0, 5)])
        self.assertEqual(covered([(1, 1, 9)], 1), [])

    def test_nothing_reaches_the_threshold(self):
        self.assertEqual(covered([(0, 10, 2), (5, 15, 2)], 5), [])
        self.assertEqual(covered([], 1), [])

    def test_weights_add_up_across_many_intervals(self):
        self.assertEqual(covered([(0, 10, 1), (1, 9, 1), (2, 8, 1), (3, 7, 1)], 3), [(2, 8)])
        self.assertEqual(covered([(0, 10, 1), (1, 9, 1), (2, 8, 1), (3, 7, 1)], 4), [(3, 7)])

    def test_identical_and_nested_intervals(self):
        self.assertEqual(covered([(0, 5, 1), (0, 5, 1)], 2), [(0, 5)])
        self.assertEqual(covered([(0, 100, 1), (40, 60, 5)], 6), [(40, 60)])

    def test_gaps_stay_separate(self):
        self.assertEqual(covered([(0, 2, 1), (3, 5, 1)], 1), [(0, 2), (3, 5)])

    def test_coverage_dips_and_returns(self):
        self.assertEqual(covered([(0, 10, 1), (0, 3, 1), (6, 10, 1)], 2), [(0, 3), (6, 10)])

    def test_negative_and_huge_coordinates(self):
        self.assertEqual(covered([(-10**12, 10**12, 2), (-5, 5, 1)], 3), [(-5, 5)])
        self.assertEqual(covered([(-7, -3, 4)], 4), [(-7, -3)])

    def test_bad_input(self):
        for intervals, k in (([(3, 1, 1)], 1), ([(0, 1, 0)], 1), ([(0, 1, -2)], 1), ([(0, 1, 1)], 0), ([(0, 1, 1)], -1)):
            with self.subTest(intervals=intervals, k=k), self.assertRaises(ValueError):
                covered(intervals, k)

    def test_matches_brute_force(self):
        rng = random.Random(11)
        for _ in range(300):
            intervals = []
            for _ in range(rng.randint(0, 12)):
                s = rng.randint(-20, 20)
                intervals.append((s, s + rng.randint(0, 15), rng.randint(1, 4)))
            k = rng.randint(1, 8)
            with self.subTest(intervals=intervals, k=k):
                self.assertEqual(covered(intervals, k), brute(intervals, k))

    def test_large_input_is_fast(self):
        rng = random.Random(5)
        intervals = []
        for _ in range(200000):
            s = rng.randint(0, 10**9)
            intervals.append((s, s + rng.randint(0, 10**5), rng.randint(1, 3)))
        started = time.monotonic()
        result = covered(intervals, 3)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertTrue(all(a < b for a, b in result))
        self.assertTrue(all(result[i][1] < result[i + 1][0] for i in range(len(result) - 1)))


if __name__ == "__main__":
    unittest.main()
