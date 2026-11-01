Write a Python module `solution.py` that matches slash-separated paths against glob patterns:

```python
def match(pattern, path):
```

Return `True` if the whole `path` matches the whole `pattern`. Paths and patterns are split into segments at `/`. Within a segment:

- `*` matches any run of characters, including none, but never `/`.
- `?` matches exactly one character other than `/`.
- `[...]` matches one character from a set: single characters and ranges such as `a-z`, for example `[abc]` or `[0-9_]`. `[!...]` matches one character not in the set. A `]` right after `[` or `[!` is a member of the set. A class never matches `/`.
- `\` makes the next character literal, so `\*` matches only `*`. A pattern ending in a lone `\` is invalid.

A segment that is exactly `**` matches zero or more whole segments: `a/**/b` matches `a/b`, `a/x/b` and `a/x/y/b`; `**` alone matches any path, including the empty one. Elsewhere, such as in `a**b`, two stars act like one `*`.

Raise `ValueError` for an invalid pattern, such as an unclosed `[`.

Matching must not take exponential time: `match("a*" * 12 + "b", "a" * 5000)` must return `False` in well under a second, and so must a `**` pattern against a path of 2,000 segments.

Examples:

```python
match("src/*.py", "src/app.py")            is True
match("src/*.py", "src/lib/app.py")        is False
match("**/test_?.py", "a/b/test_1.py")     is True
match("data/[!.]*", "data/.hidden")        is False
match(r"notes/\*", "notes/*")              is True
```
