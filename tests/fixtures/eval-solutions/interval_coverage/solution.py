"""Reference solution: a sweep over the interval ends."""


def covered(intervals, k):
    if k <= 0:
        raise ValueError("k must be positive")
    change = {}
    for start, end, weight in intervals:
        if start > end or weight <= 0:
            raise ValueError(f"bad interval {(start, end, weight)}")
        if start == end:
            continue
        change[start] = change.get(start, 0) + weight
        change[end] = change.get(end, 0) - weight
    result = []
    total = 0
    open_at = None
    for point in sorted(change):
        total += change[point]
        if total >= k and open_at is None:
            open_at = point
        elif total < k and open_at is not None:
            result.append((open_at, point))
            open_at = None
    return result
