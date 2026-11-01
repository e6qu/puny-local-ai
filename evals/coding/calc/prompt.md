Write a Python module `solution.py` with an exact calculator:

```python
def evaluate(expression, variables=None):
```

The expression has integers (decimal digits only), names (a letter or `_`, then letters, digits or `_`) looked up in the `variables` dict, parentheses, spaces, and these operators, loosest first:

1. `+` and `-`, left to right
2. `*`, `/`, `//` and `%`, left to right
3. unary `+` and `-`
4. `**`, right to left, binding tighter than a unary operator on its left: `-2 ** 2` is `-4`, and `2 ** -1` is allowed

Return a `fractions.Fraction`. `/` divides exactly; `//` and `%` work as Python's do on fractions. The exponent of `**` must be a whole number, otherwise raise `ValueError`.

Errors:

- Division by zero, including `//` and `%` by zero and `0 ** -1`: raise `ZeroDivisionError`.
- A name not in `variables`: raise `NameError`.
- Anything else malformed: raise `SyntaxError` with its `offset` attribute set to the 0-based index in `expression` of the first character that cannot be read as part of a valid expression, or `len(expression)` if the expression ends too early.

Parentheses nested 100 deep must work. Do not use `eval`, `exec`, `compile` or the `ast` module.

Examples:

```python
evaluate("1 + 2 * 3")              == 7
evaluate("7 / 2")                  == Fraction(7, 2)
evaluate("2 ** 3 ** 2")            == 512
evaluate("-x ** 2", {"x": 3})      == -9
evaluate("(1 + 2")                 # SyntaxError, offset 6
evaluate("1 + * 2")                # SyntaxError, offset 4
```
