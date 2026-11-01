"""Summarizes an access log."""

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
