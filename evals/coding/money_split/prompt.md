Write a Python module `solution.py` that splits an amount of money by weights, exactly:

```python
def split(total_cents, weights):
```

`total_cents` is an int, possibly negative; `weights` is a list of ints, each zero or more, at least one positive. Return a list of ints, one per weight, that adds up to exactly `total_cents`:

1. For a non-negative total, each share starts as `floor(total_cents * weight / sum(weights))`.
2. The cents left over go one each to the shares with the largest remainders of that division. When remainders are equal, the share that comes first in the list gets the cent first.
3. For a negative total, split the absolute amount as above and negate every share.

A weight of zero always gets 0. Raise `ValueError` if `weights` is empty, any weight is negative, or all weights are zero, and `TypeError` if the total or a weight is not an int (a `bool` is not accepted). Use exact integer arithmetic: totals and weights can be far beyond what a float holds exactly.

Examples:

```python
split(100, [1, 1, 1])     == [34, 33, 33]
split(-100, [1, 1, 1])    == [-34, -33, -33]
split(10, [3, 0, 7])      == [3, 0, 7]
split(5, [1, 2])          == [2, 3]    # 1 and 3 with remainders 2 and 1 (in thirds); the cent goes to the first
```
