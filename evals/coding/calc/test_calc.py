import inspect
import re
import unittest
from fractions import Fraction

import solution
from solution import evaluate


class EvaluateTest(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(evaluate("1 + 2 * 3"), 7)
        self.assertEqual(evaluate("7 / 2"), Fraction(7, 2))
        self.assertEqual(evaluate("2 ** 3 ** 2"), 512)
        self.assertEqual(evaluate("-x ** 2", {"x": 3}), -9)

    def test_returns_fractions(self):
        self.assertIsInstance(evaluate("3"), Fraction)
        self.assertIsInstance(evaluate("1/3 + 1/6"), Fraction)
        self.assertEqual(evaluate("1/3 + 1/6"), Fraction(1, 2))

    def test_precedence_and_associativity(self):
        cases = {
            "10 - 4 - 3": 3, "2 * 3 + 4 * 5": 26, "100 / 10 / 5": 2, "2 + 3 * 4 ** 2": 50,
            "(2 + 3) * 4": 20, "-3 - -3": 0, "- - 4": 4, "+5": 5, "2 ** -2": Fraction(1, 4),
            "(-2) ** 2": 4, "-2 ** 2": -4, "2 * -3": -6, "2 ** -1 ** 2": Fraction(1, 2),
        }
        for expression, expected in cases.items():
            with self.subTest(expression):
                self.assertEqual(evaluate(expression), expected)

    def test_floor_division_and_modulo_follow_python(self):
        for a, b in ((7, 2), (-7, 2), (7, -2), (-7, -2)):
            with self.subTest(a=a, b=b):
                self.assertEqual(evaluate(f"({a}) // ({b})"), a // b)
                self.assertEqual(evaluate(f"({a}) % ({b})"), a % b)
        self.assertEqual(evaluate("(7/2) // (1/3)"), Fraction(7, 2) // Fraction(1, 3))
        self.assertEqual(evaluate("(7/2) % (1/3)"), Fraction(7, 2) % Fraction(1, 3))

    def test_variables(self):
        self.assertEqual(evaluate("rate * hours_2 + _bonus", {"rate": 30, "hours_2": 8, "_bonus": Fraction(1, 2)}), Fraction(481, 2))

    def test_big_numbers_stay_exact(self):
        self.assertEqual(evaluate("2 ** 200 + 1 - 2 ** 200"), 1)
        self.assertEqual(evaluate("123456789123456789 * 987654321987654321"), 123456789123456789 * 987654321987654321)

    def test_whitespace(self):
        self.assertEqual(evaluate("  ( 1+2 )*\t3 "), 9)

    def test_division_by_zero(self):
        for expression in ("1 / 0", "1 // (2 - 2)", "5 % 0", "0 ** -1"):
            with self.subTest(expression), self.assertRaises(ZeroDivisionError):
                evaluate(expression)

    def test_unknown_name(self):
        with self.assertRaises(NameError):
            evaluate("a + b", {"a": 1})

    def test_fractional_exponent(self):
        with self.assertRaises(ValueError):
            evaluate("4 ** (1 / 2)")

    def test_syntax_error_offsets(self):
        cases = {
            "(1 + 2": 6, "1 + * 2": 4, "1 2": 2, "2 $ 3": 2, "": 0, "   ": 3, "()": 1,
            "1 +": 3, "3 ** ": 5, "(1))": 3, "4 ** * 2": 5, "1 + 2)": 5, "x y": 2, "1.5": 1,
        }
        for expression, offset in cases.items():
            with self.subTest(expression):
                with self.assertRaises(SyntaxError) as caught:
                    evaluate(expression, {"x": 1, "y": 2})
                self.assertEqual(caught.exception.offset, offset)

    def test_does_not_use_python_itself(self):
        source = inspect.getsource(solution)
        # Bare calls only: re.compile and the like are fine.
        for forbidden in (r"(?<![.\w])eval\s*\(", r"(?<![.\w])exec\s*\(", r"(?<![.\w])compile\s*\(",
                          r"\bimport\s+ast\b", r"\bfrom\s+ast\b", r"__import__"):
            self.assertIsNone(re.search(forbidden, source), forbidden)

    def test_deep_nesting(self):
        self.assertEqual(evaluate("(" * 100 + "1" + ")" * 100), 1)


if __name__ == "__main__":
    unittest.main()
