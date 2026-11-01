Write a Python module `solution.py` that orders tasks so each comes after the tasks it depends on.

```python
class CycleError(ValueError):
    """Raised when the dependencies form a cycle. Its `cycle` attribute holds it."""

def order(tasks):
```

`tasks` maps a task name (a string) to an iterable of the names it depends on. A name that appears only as a dependency is a task with no dependencies of its own.

Return a list of every task name, each exactly once, with every task after all of its dependencies. Whenever more than one task could come next, take the one that sorts first as a string, so the result is always the same.

If the dependencies form a cycle, raise `CycleError` and set its `cycle` attribute to a list of names that walks one cycle along the dependencies and returns to where it started: `["a", "b", "c", "a"]` means `a` depends on `b`, `b` on `c`, and `c` on `a`. A task depending on itself is the cycle `["a", "a"]`.

It must handle a chain of 100,000 tasks in well under two seconds, so no recursion that deep.

Examples:

```python
order({"b": ["a"], "c": ["a"], "a": []})   == ["a", "b", "c"]
order({"app": ["lib", "log"], "lib": ["log"]}) == ["log", "lib", "app"]
order({"x": ["y"], "y": ["x"]})   # raises CycleError, cycle == ["x", "y", "x"] or ["y", "x", "y"]
```
