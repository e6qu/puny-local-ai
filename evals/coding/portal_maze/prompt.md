Write a Python module `solution.py` that finds the shortest walk through a maze with portals:

```python
def shortest(grid):
```

`grid` is a list of strings of equal length. Each character is a cell:

- `#` is a wall, `.` is open floor.
- `S` is the start and `G` the goal; there is exactly one of each.
- A lowercase letter is a portal. Each letter that appears must appear exactly twice. A portal cell is floor you can walk on, and standing on one you may also jump to its twin.

One step moves to an adjacent open cell up, down, left or right, or jumps from a portal to its twin; each costs 1. You cannot leave the grid. Return the fewest steps from `S` to `G`, or `-1` if `G` cannot be reached.

Raise `ValueError` if the rows have different lengths, the grid is empty, there is not exactly one `S` and one `G`, a letter does not appear exactly twice, or any other character appears.

It must handle a 400 by 400 grid in well under two seconds.

Example:

```python
shortest([
    "S.a.#...",
    "####.#.#",
    "G..a....",
]) == 6        # S, two steps to a, jump, three steps to G
```
