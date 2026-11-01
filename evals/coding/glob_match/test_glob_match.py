import time
import unittest

from solution import match


class MatchTest(unittest.TestCase):
    def check(self, cases):
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertIs(match(pattern, path), expected)

    def test_examples(self):
        self.check([
            ("src/*.py", "src/app.py", True), ("src/*.py", "src/lib/app.py", False),
            ("**/test_?.py", "a/b/test_1.py", True), ("data/[!.]*", "data/.hidden", False),
            (r"notes/\*", "notes/*", True),
        ])

    def test_star_and_question_mark(self):
        self.check([
            ("*", "", True), ("*", "abc", True), ("*", "a/b", False), ("a*c", "ac", True), ("a*c", "abbbc", True),
            ("a*c", "abcd", False), ("?", "", False), ("?", "x", True), ("??", "x", False), ("a?c", "a/c", False),
            ("*.tar.gz", "x.tar.gz", True), ("*.tar.gz", "x.tar.gzz", False), ("*a*b*c*", "zzazzbzzczz", True),
            ("*a*b*c*", "zzczzbzza", False),
        ])

    def test_classes(self):
        self.check([
            ("[abc]", "b", True), ("[abc]", "d", False), ("[a-c]x", "bx", True), ("[a-c]x", "dx", False),
            ("[!a-c]", "d", True), ("[!a-c]", "a", False), ("[]]", "]", True), ("[!]]", "]", False),
            ("[!]]", "x", True), ("[0-9_]*", "_tmp", True), ("[!x]", "/", False),
            ("[a-]", "-", True), ("[*?]", "?", True), ("[*?]", "x", False),
        ])

    def test_escapes(self):
        self.check([
            (r"\*", "*", True), (r"\*", "x", False), (r"\?", "?", True), (r"\[a]", "[a]", True),
            (r"a\\b", "a\\b", True), (r"\a", "a", True),
        ])

    def test_double_star_segments(self):
        self.check([
            ("a/**/b", "a/b", True), ("a/**/b", "a/x/b", True), ("a/**/b", "a/x/y/b", True), ("a/**/b", "a/x/y/c", False),
            ("**", "", True), ("**", "a/b/c", True), ("**/c", "c", True), ("**/c", "a/b/c", True), ("**/c", "a/b/cc", False),
            ("a/**", "a", True), ("a/**", "a/b/c", True), ("a/**", "b/c", False), ("**/**/x", "x", True),
            ("a**b", "axxb", True), ("a**b", "ax/xb", False), ("src/**/*.py", "src/a/b/m.py", True), ("src/**/*.py", "src/m.py", True),
        ])

    def test_segments_must_line_up(self):
        self.check([
            ("a/b", "a/b/", False), ("a/b/", "a/b/", True), ("a//b", "a//b", True), ("a/*/b", "a//b", True),
            ("", "", True), ("", "a", False), ("a", "", False),
        ])

    def test_invalid_patterns(self):
        # Patterns split at / first, so a class cannot hold one.
        for pattern in ("[abc", "x[", "[!", "abc\\", "a/[z", "a[/]b"):
            with self.subTest(pattern), self.assertRaises(ValueError):
                match(pattern, "abc")

    def test_no_exponential_time_on_stars(self):
        started = time.monotonic()
        self.assertFalse(match("a*" * 12 + "b", "a" * 5000))
        self.assertTrue(match("a*" * 12 + "a", "a" * 5000))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_no_exponential_time_on_double_stars(self):
        path = "/".join(["d"] * 2000)
        started = time.monotonic()
        self.assertFalse(match("**/" * 6 + "x", path))
        self.assertTrue(match("**/d/**/d", path))
        self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
