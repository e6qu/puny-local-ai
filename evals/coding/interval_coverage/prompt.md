Write a Python module `solution.py` with one function:

```python
def covered(intervals, k):
```

`intervals` is a list of `(start, end, weight)` tuples of integers. Each describes the half-open range `[start, end)` with a positive `weight`. A point is *covered* when the weights of all intervals containing it add up to at least `k`.

Return the covered points as a sorted list of disjoint `(start, end)` tuples, half-open, each as long as possible: two covered ranges that touch, such as `[0, 2)` and `[2, 5)`, are one range `(0, 5)`.

- An interval with `start == end` contains no point and changes nothing.
- Raise `ValueError` if any interval has `start > end` or `weight <= 0`, or if `k <= 0`.
- Starts and ends may be negative or very large.
- It must handle 200,000 intervals in well under two seconds.

Examples:

```python
covered([(0, 4, 1), (2, 6, 1)], 2)        == [(2, 4)]
covered([(0, 4, 1), (2, 6, 1)], 1)        == [(0, 6)]
covered([(0, 2, 3), (2, 5, 3)], 3)        == [(0, 5)]
covered([(1, 1, 9)], 1)                   == []
```
