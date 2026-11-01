Write a Python module `solution.py` that applies a JSON Patch (RFC 6902) to a JSON document:

```python
class PatchError(ValueError):
    """Its `index` attribute is the position in `operations` of the operation that failed."""

def apply_patch(document, operations):
```

`document` is any JSON value as Python objects (dict, list, str, int, float, bool, None). `operations` is a list of dicts, each with an `op` of `add`, `remove`, `replace`, `move`, `copy` or `test`, a `path`, and a `value` (add, replace, test) or a `from` (move, copy).

Paths are JSON Pointers (RFC 6901): `""` is the whole document; otherwise `/`-separated tokens where `~1` means `/` and `~0` means `~`. In an array a token must be a decimal index without leading zeros (`0` is fine, `01` is not). For `add`, the index may equal the array's length, and `-` means the end of the array.

The operations follow RFC 6902: `add` inserts into arrays and sets object members (replacing an existing one); `remove` and `replace` need the target to exist; `move` is a remove then an add, and moving a location into one of its own children is an error; `copy` adds a deep copy; `test` checks that the value at `path` equals `value`. Equality is JSON equality: numbers compare by value (`1 == 1.0`), but `true` is not `1` and `false` is not `0`, and objects compare without regard to key order.

Return the patched document. Never change `document` or any value in `operations`, not even on success: work on a copy. Any failure — a missing target, a bad pointer, a failed `test`, an unknown or incomplete operation — raises `PatchError` with `index` set, and nothing is changed.

Example:

```python
doc = {"a": [1, 2], "b": {"c~d": True}}
apply_patch(doc, [
    {"op": "add", "path": "/a/-", "value": 3},
    {"op": "move", "from": "/b/c~0d", "path": "/flag"},
    {"op": "test", "path": "/a", "value": [1, 2, 3.0]},
]) == {"a": [1, 2, 3], "b": {}, "flag": True}
```
