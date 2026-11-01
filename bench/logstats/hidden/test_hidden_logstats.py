import random
import threading
import time
import unittest
from collections import Counter, defaultdict

from logstats.parse import parse
from logstats.report import report

# A frozen copy of the original, slow report, to check the new one against.


def endpoint(path):
    """The path with numeric segments replaced by {id}: /api/items/42 -> /api/items/{id}."""
    return "/".join("{id}" if part.isdigit() else part for part in path.split("/"))


def percentile(values, p):
    """The nearest-rank p-th percentile of values."""
    ordered = sorted(values)
    rank = -(-p * len(ordered) // 100)  # ceiling
    return ordered[max(rank, 1) - 1]


def slow_report(lines):
    """A text report of the log lines.

    - "lines: N, malformed: M" first; malformed lines are counted and otherwise ignored.
    - Then one line per endpoint, the busiest first, ties by endpoint name:
      "METHOD ENDPOINT requests=N errors=E p50=Xms p95=Yms users=U"
      where errors are responses with status 500 or more and users counts distinct users.
    - Then "top users:" and the five users with the most requests, ties by name: "  NAME N".
    """
    entries = []
    malformed = 0
    for line in lines:
        parsed = parse(line)
        if parsed is None:
            malformed += 1
        else:
            entries.append(parsed)

    keys = []
    for _, method, path, _, _, _ in entries:
        key = (method, endpoint(path))
        if key not in keys:
            keys.append(key)

    rows = []
    for key in keys:
        matching = [e for e in entries if (e[1], endpoint(e[2])) == key]
        times = [e[4] for e in matching]
        errors = len([e for e in matching if e[3] >= 500])
        users = []
        for e in matching:
            if e[5] not in users:
                users.append(e[5])
        rows.append((len(matching), key, errors, percentile(times, 50), percentile(times, 95), len(users)))
    rows.sort(key=lambda r: (-r[0], r[1][1], r[1][0]))

    names = []
    for e in entries:
        if e[5] not in names:
            names.append(e[5])
    counts = [(len([e for e in entries if e[5] == name]), name) for name in names]
    counts.sort(key=lambda c: (-c[0], c[1]))

    out = [f"lines: {len(lines)}, malformed: {malformed}"]
    for count, (method, ep), errors, p50, p95, users in rows:
        out.append(f"{method} {ep} requests={count} errors={errors} p50={p50}ms p95={p95}ms users={users}")
    out.append("top users:")
    for count, name in counts[:5]:
        out.append(f"  {name} {count}")
    return "\n".join(out) + "\n"


def fast_reference(lines):
    """The same report, written for speed, to check the large case."""
    groups = defaultdict(list)
    users = defaultdict(set)
    per_user = Counter()
    malformed = 0
    for line in lines:
        e = parse(line)
        if e is None:
            malformed += 1
            continue
        key = (e[1], endpoint(e[2]))
        groups[key].append(e)
        users[key].add(e[5])
        per_user[e[5]] += 1
    rows = []
    for key, es in groups.items():
        times = [e[4] for e in es]
        rows.append((len(es), key, sum(e[3] >= 500 for e in es), percentile(times, 50), percentile(times, 95), len(users[key])))
    rows.sort(key=lambda r: (-r[0], r[1][1], r[1][0]))
    out = [f"lines: {len(lines)}, malformed: {malformed}"]
    for count, (method, ep), errors, p50, p95, n in rows:
        out.append(f"{method} {ep} requests={count} errors={errors} p50={p50}ms p95={p95}ms users={n}")
    out.append("top users:")
    for name, count in sorted(per_user.items(), key=lambda c: (-c[1], c[0]))[:5]:
        out.append(f"  {name} {count}")
    return "\n".join(out) + "\n"


def make_log(rng, n, paths=None, users=40):
    names = ["ana", "bo", "cy", "dee", "eli", "fay", "gus"] + [f"u{k}" for k in range(users)]
    paths = paths or ["/api/items/{n}", "/api/items", "/api/users/{n}/orders", "/health", "/api/orders/{n}/lines/{n}", "/static/app.js"]
    lines = []
    for i in range(n):
        roll = rng.random()
        if roll < 0.01:
            lines.append(rng.choice(["", "garbage", "2026-10-30T09:00:00Z GET /x 200 5 user=a", "2026-10-30T09:00:00Z GET /x OK 5ms user=a",
                                     "2026-10-30T09:00:00Z GET /x 200 5ms ana", "2026-10-30T09:00:00Z GET /x 200 5ms user=a extra"]))
            continue
        path = rng.choice(paths).replace("{n}", str(rng.randint(1, 500)), 1).replace("{n}", str(rng.randint(1, 9)))
        status = rng.choice([200, 200, 200, 201, 204, 301, 404, 500, 502, 503])
        lines.append(f"2026-10-30T{i // 3600 % 24:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z {rng.choice(['GET', 'GET', 'POST', 'DELETE'])} "
                     f"{path} {status} {int(rng.expovariate(1 / 80))}ms user={rng.choice(names)}")
    return lines


class HiddenReportTest(unittest.TestCase):
    def test_matches_the_original_on_many_small_logs(self):
        rng = random.Random(77)
        for _ in range(300):
            lines = make_log(rng, rng.randint(0, 60), paths=["/a/{n}", "/a", "/b/{n}/c", "/{n}"])
            with self.subTest(lines=lines[:3]):
                self.assertEqual(report(lines), slow_report(lines))

    def test_ties_and_edge_cases_match_the_original(self):
        cases = [
            [],
            ["only junk"],
            ["2026-10-30T09:00:00Z GET /b 200 5ms user=z", "2026-10-30T09:00:00Z GET /a 200 5ms user=y",
             "2026-10-30T09:00:00Z POST /a 200 5ms user=x", "2026-10-30T09:00:00Z DELETE /a 500 7ms user=w"],
            ["2026-10-30T09:00:00Z GET /1/2/3 500 0ms user=a"] * 7,
            [f"2026-10-30T09:00:00Z GET /p 200 {ms}ms user=u{ms % 6}" for ms in range(100)],
        ]
        for lines in cases:
            with self.subTest(lines=lines[:2]):
                self.assertEqual(report(lines), slow_report(lines))

    def test_fast_reference_matches_the_original(self):
        rng = random.Random(5)
        for _ in range(50):
            lines = make_log(rng, rng.randint(0, 200))
            self.assertEqual(fast_reference(lines), slow_report(lines))

    def test_200000_lines_in_under_two_seconds(self):
        lines = make_log(random.Random(2026), 200000, users=3000)
        out = {}
        # A daemon thread, so a slow report fails this test instead of holding up the run.
        thread = threading.Thread(target=lambda: out.update(text=report(lines)), daemon=True)
        started = time.monotonic()
        thread.start()
        thread.join(2.0)
        self.assertFalse(thread.is_alive(), "report took more than two seconds for 200,000 lines")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(out["text"], fast_reference(lines))


if __name__ == "__main__":
    unittest.main()
