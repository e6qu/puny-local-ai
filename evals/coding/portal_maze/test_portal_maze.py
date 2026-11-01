import collections
import random
import time
import unittest

from solution import shortest


class ShortestTest(unittest.TestCase):
    def test_example(self):
        self.assertEqual(shortest(["S.a.#...", "####.#.#", "G..a...."]), 6)

    def test_plain_walk(self):
        self.assertEqual(shortest(["S..G"]), 3)
        self.assertEqual(shortest(["S#G"]), -1)
        self.assertEqual(shortest(["S", ".", "G"]), 2)
        self.assertEqual(shortest(["SG"]), 1)

    def test_walls_force_a_detour(self):
        self.assertEqual(shortest([
            "S.#....",
            ".##.##.",
            "....#.G",
        ]), 12)

    def test_portal_shortcut(self):
        self.assertEqual(shortest([
            "Sa.......",
            "#########",
            "........a",
            "G........",
        ]), 1 + 1 + 1 + 8)

    def test_using_a_portal_is_optional(self):
        self.assertEqual(shortest(["S.a.G", "#####", "a...."]), 4)

    def test_portals_chain(self):
        # S, jump a, walk to b, jump b, walk to G.
        self.assertEqual(shortest(["Sa##b..G", "########", "a..b####"]), 9)

    def test_portal_that_leads_nowhere(self):
        self.assertEqual(shortest(["Sa#G", "####", "#a##"]), -1)

    def test_bad_grids(self):
        bad = [
            [], ["S.", "G"], ["S..", "..."], ["SG", "S."], ["S.a", "..G"], ["S.a", "aaG"], ["S*G"], ["S G"], ["SAG"],
        ]
        for grid in bad:
            with self.subTest(grid=grid), self.assertRaises(ValueError):
                shortest(grid)

    def test_matches_a_reference_search(self):
        rng = random.Random(21)
        for _ in range(150):
            h, w = rng.randint(2, 7), rng.randint(3, 7)
            cells = [rng.choice("..#") for _ in range(h * w)]
            spots = rng.sample(range(h * w), 6)
            cells[spots[0]], cells[spots[1]] = "S", "G"
            cells[spots[2]] = cells[spots[3]] = "p"
            cells[spots[4]] = cells[spots[5]] = "q"
            grid = ["".join(cells[r * w:(r + 1) * w]) for r in range(h)]
            with self.subTest(grid=grid):
                self.assertEqual(shortest(grid), reference(grid))

    def test_large_grid_is_fast(self):
        rng = random.Random(8)
        n = 400
        rows = [["#" if rng.random() < 0.25 else "." for _ in range(n)] for _ in range(n)]
        rows[0][0], rows[n - 1][n - 1] = "S", "G"
        for letter in "abcdefghijklmnopqrstuvwxyz":
            for _ in range(2):
                while True:
                    r, c = rng.randrange(n), rng.randrange(n)
                    if rows[r][c] in ".#":
                        rows[r][c] = letter
                        break
        grid = ["".join(r) for r in rows]
        started = time.monotonic()
        result = shortest(grid)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(result, reference(grid))


def reference(grid):
    h, w = len(grid), len(grid[0])
    cells = {(r, c): grid[r][c] for r in range(h) for c in range(w)}
    twins = collections.defaultdict(list)
    for spot, ch in cells.items():
        if ch.islower():
            twins[ch].append(spot)
    start = next(s for s, ch in cells.items() if ch == "S")
    dist = {start: 0}
    queue = collections.deque([start])
    while queue:
        spot = queue.popleft()
        if cells[spot] == "G":
            return dist[spot]
        r, c = spot
        nexts = [(r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)]
        if cells[spot].islower():
            nexts += [t for t in twins[cells[spot]] if t != spot]
        for n in nexts:
            if n in cells and cells[n] != "#" and n not in dist:
                dist[n] = dist[spot] + 1
                queue.append(n)
    return -1


if __name__ == "__main__":
    unittest.main()
