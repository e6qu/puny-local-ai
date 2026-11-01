import random
import unittest
from fractions import Fraction

from solution import split


def reference(total, weights):
    sign = -1 if total < 0 else 1
    amount = abs(total)
    whole = sum(weights)
    shares = [amount * w // whole for w in weights]
    remainders = [amount * w % whole for w in weights]
    left = amount - sum(shares)
    for i in sorted(range(len(weights)), key=lambda i: (-remainders[i], i))[:left]:
        shares[i] += 1
    return [sign * s for s in shares]


class SplitTest(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(split(100, [1, 1, 1]), [34, 33, 33])
        self.assertEqual(split(-100, [1, 1, 1]), [-34, -33, -33])
        self.assertEqual(split(10, [3, 0, 7]), [3, 0, 7])
        self.assertEqual(split(5, [1, 2]), [2, 3])

    def test_largest_remainder_wins_over_position(self):
        # 10 * [1, 3, 3] / 7: 1 r3, 4 r2, 4 r2; one cent left, to the first share.
        self.assertEqual(split(10, [1, 3, 3]), [2, 4, 4])
        # 11 * [2, 5] / 7: 3 r1, 7 r6; one cent left, to the second share.
        self.assertEqual(split(11, [2, 5]), [3, 8])

    def test_ties_go_to_the_earlier_share(self):
        self.assertEqual(split(2, [1, 1, 1]), [1, 1, 0])
        self.assertEqual(split(1, [5, 5, 5, 5]), [1, 0, 0, 0])

    def test_zero_total_and_zero_weights(self):
        self.assertEqual(split(0, [4, 5]), [0, 0])
        self.assertEqual(split(7, [0, 1, 0]), [0, 7, 0])

    def test_exact_with_huge_numbers(self):
        total = 10 ** 30 + 7
        weights = [10 ** 20 + 1, 3, 10 ** 25]
        result = split(total, weights)
        self.assertEqual(sum(result), total)
        self.assertEqual(result, reference(total, weights))
        for share, w in zip(result, weights):
            self.assertLess(abs(Fraction(share) - Fraction(total * w, sum(weights))), 1)

    def test_matches_the_rules(self):
        rng = random.Random(13)
        for _ in range(2000):
            weights = [rng.choice((0, rng.randint(1, 9), rng.randint(1, 10 ** 6))) for _ in range(rng.randint(1, 7))]
            if not any(weights):
                weights[0] = 1
            total = rng.randint(-10 ** 7, 10 ** 7)
            with self.subTest(total=total, weights=weights):
                result = split(total, weights)
                self.assertEqual(result, reference(total, weights))
                self.assertTrue(all(type(s) is int for s in result))

    def test_bad_weights(self):
        for weights in ([], [0, 0], [1, -1]):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                split(10, weights)

    def test_bad_types(self):
        for total, weights in ((10.0, [1]), ("10", [1]), (True, [1]), (10, [1.5]), (10, [True, 1]), (10, ["1"])):
            with self.subTest(total=total, weights=weights), self.assertRaises(TypeError):
                split(total, weights)

    def test_does_not_change_its_argument(self):
        weights = [3, 1]
        split(9, weights)
        self.assertEqual(weights, [3, 1])


if __name__ == "__main__":
    unittest.main()
