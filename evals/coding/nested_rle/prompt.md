Write a Python module `solution.py` for a nested repeat notation such as `3[ab2[c]]x`, which stands for `abccabccabccx`.

```python
def length(text):           # the expanded length, as an int
def expand(text, limit):    # the expanded string
```

The grammar:

- A text is a sequence of items, possibly empty.
- An item is either one or more lowercase letters `a`-`z`, or a count followed by `[`, a text, and `]`.
- A count is a whole number from 1 to 999 written without leading zeros.

Anything else, such as `3[a`, `a]`, `0[a]`, `01[a]`, `3a`, `[a]`, `1000[a]`, an uppercase letter or a space, raises `ValueError`.

`length` must return the exact length even when it is astronomically large, such as `999[999[999[999[999[a]]]]]`, without building the string, and fast. `expand` raises `OverflowError` when the result would be longer than `limit` characters, and must find that out before building anything large. Nesting may be 500 deep, so do not rely on recursion that deep.

Examples:

```python
expand("3[ab2[c]]x", 100) == "abccabccabccx"
length("2[3[a]b]")        == 8
expand("", 0)             == ""
expand("9[a]", 8)         # OverflowError
```
