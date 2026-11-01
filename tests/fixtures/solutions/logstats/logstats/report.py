"""Summarizes an access log."""

from collections import Counter, defaultdict

from .parse import parse


def endpoint(path):
    """The path with numeric segments replaced by {id}: /api/items/42 -> /api/items/{id}."""
    return "/".join("{id}" if part.isdigit() else part for part in path.split("/"))


def percentile(values, p):
    """The nearest-rank p-th percentile of values."""
    ordered = sorted(values)
    rank = -(-p * len(ordered) // 100)  # ceiling
    return ordered[max(rank, 1) - 1]


def report(lines):
    """A text report of the log lines.

    - "lines: N, malformed: M" first; malformed lines are counted and otherwise ignored.
    - Then one line per endpoint, the busiest first, ties by endpoint name:
      "METHOD ENDPOINT requests=N errors=E p50=Xms p95=Yms users=U"
      where errors are responses with status 500 or more and users counts distinct users.
    - Then "top users:" and the five users with the most requests, ties by name: "  NAME N".
    """
    times = defaultdict(list)
    errors = Counter()
    users = defaultdict(set)
    per_user = Counter()
    malformed = 0
    endpoints = {}
    for line in lines:
        parsed = parse(line)
        if parsed is None:
            malformed += 1
            continue
        _, method, path, status, millis, user = parsed
        ep = endpoints.get(path)
        if ep is None:
            ep = endpoints[path] = endpoint(path)
        key = (method, ep)
        times[key].append(millis)
        if status >= 500:
            errors[key] += 1
        users[key].add(user)
        per_user[user] += 1

    rows = []
    for key, values in times.items():
        values.sort()
        n = len(values)
        p50 = values[max(-(-50 * n // 100), 1) - 1]
        p95 = values[max(-(-95 * n // 100), 1) - 1]
        rows.append((n, key, errors[key], p50, p95, len(users[key])))
    rows.sort(key=lambda r: (-r[0], r[1][1], r[1][0]))

    out = [f"lines: {len(lines)}, malformed: {malformed}"]
    for count, (method, ep), errs, p50, p95, n_users in rows:
        out.append(f"{method} {ep} requests={count} errors={errs} p50={p50}ms p95={p95}ms users={n_users}")
    out.append("top users:")
    for name, count in sorted(per_user.items(), key=lambda c: (-c[1], c[0]))[:5]:
        out.append(f"  {name} {count}")
    return "\n".join(out) + "\n"
