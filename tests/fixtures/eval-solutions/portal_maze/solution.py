"""Reference solution: breadth-first search over cells, with portal jumps as edges."""

from collections import deque


def shortest(grid):
    if not grid or not grid[0]:
        raise ValueError("the grid is empty")
    width = len(grid[0])
    if any(len(row) != width for row in grid):
        raise ValueError("the rows differ in length")
    cells = "".join(grid)
    for ch in set(cells):
        if not (ch in "#.SG" or "a" <= ch <= "z"):
            raise ValueError(f"unexpected cell {ch!r}")
    if cells.count("S") != 1 or cells.count("G") != 1:
        raise ValueError("need exactly one S and one G")
    portals = {}
    for i, ch in enumerate(cells):
        if "a" <= ch <= "z":
            portals.setdefault(ch, []).append(i)
    if any(len(spots) != 2 for spots in portals.values()):
        raise ValueError("each portal letter must appear exactly twice")
    twin = {}
    for a, b in portals.values():
        twin[a], twin[b] = b, a
    height = len(grid)
    start, goal = cells.index("S"), cells.index("G")
    dist = [-1] * len(cells)
    dist[start] = 0
    queue = deque([start])
    while queue:
        i = queue.popleft()
        if i == goal:
            return dist[i]
        r, c = divmod(i, width)
        nexts = []
        if r > 0:
            nexts.append(i - width)
        if r < height - 1:
            nexts.append(i + width)
        if c > 0:
            nexts.append(i - 1)
        if c < width - 1:
            nexts.append(i + 1)
        if i in twin:
            nexts.append(twin[i])
        for n in nexts:
            if dist[n] < 0 and cells[n] != "#":
                dist[n] = dist[i] + 1
                queue.append(n)
    return -1
