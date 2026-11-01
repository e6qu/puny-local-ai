import time
import unittest

from solution import expand, length


class NestedRleTest(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(expand("3[ab2[c]]x", 100), "abccabccabccx")
        self.assertEqual(length("2[3[a]b]"), 8)
        self.assertEqual(expand("", 0), "")
        with self.assertRaises(OverflowError):
            expand("9[a]", 8)

    def test_expansions(self):
        cases = {
            "abc": "abc", "1[x]": "x", "2[]": "", "2[a]2[b]": "aabb", "z3[]y": "zy",
            "2[a2[b2[c]]]": "abccbccabccbcc", "10[q]": "q" * 10, "999[]": "",
        }
        for text, expected in cases.items():
            with self.subTest(text):
                self.assertEqual(expand(text, 1000), expected)
                self.assertEqual(length(text), len(expected))

    def test_limit_is_inclusive(self):
        self.assertEqual(expand("4[ab]", 8), "abababab")
        with self.assertRaises(OverflowError):
            expand("4[ab]", 7)

    def test_huge_lengths_are_exact_and_fast(self):
        started = time.monotonic()
        self.assertEqual(length("999[999[999[999[999[a]]]]]"), 999 ** 5)
        text = "".join("999[" for _ in range(60)) + "ab" + "]" * 60
        self.assertEqual(length(text), 2 * 999 ** 60)
        with self.assertRaises(OverflowError):
            expand(text, 10 ** 6)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_deep_nesting(self):
        text = "1[" * 500 + "abc" + "]" * 500
        self.assertEqual(length(text), 3)
        self.assertEqual(expand(text, 3), "abc")

    def test_long_flat_input(self):
        text = "ab2[c]" * 20000
        self.assertEqual(length(text), 4 * 20000)
        self.assertEqual(expand(text, 10 ** 6), "abcc" * 20000)

    def test_malformed(self):
        for text in ("3[a", "a]", "0[a]", "01[a]", "3a", "[a]", "1000[a]", "A", "a b", "2[a]]", "2[[a]]", "-1[a]", "2[a3]", "3", "]["):
            with self.subTest(text):
                with self.assertRaises(ValueError):
                    length(text)
                with self.assertRaises(ValueError):
                    expand(text, 10 ** 6)

    def test_overflow_is_not_a_value_error(self):
        with self.assertRaises(OverflowError):
            expand("2[a]", 1)
        self.assertFalse(issubclass(OverflowError, ValueError))


if __name__ == "__main__":
    unittest.main()
