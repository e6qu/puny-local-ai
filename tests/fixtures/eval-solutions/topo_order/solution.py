"""Reference solution: Kahn's algorithm with a heap, and an iterative walk for cycles."""

import heapq


class CycleError(ValueError):
    def __init__(self, cycle):
        super().__init__("dependency cycle: " + " -> ".join(cycle))
        self.cycle = cycle


def order(tasks):
    deps = {}
    for name, needs in tasks.items():
        deps.setdefault(name, set()).update(needs)
        for need in deps[name]:
            deps.setdefault(need, set())
    dependents = {name: [] for name in deps}
    waiting = {}
    for name, needs in deps.items():
        waiting[name] = len(needs)
        for need in needs:
            dependents[need].append(name)
    ready = [name for name, count in waiting.items() if count == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        name = heapq.heappop(ready)
        result.append(name)
        for dependent in dependents[name]:
            waiting[dependent] -= 1
            if waiting[dependent] == 0:
                heapq.heappush(ready, dependent)
    if len(result) == len(deps):
        return result
    # Every task left waits on another task left, so walking dependencies
    # among them must come back to a task already seen.
    left = {name for name, count in waiting.items() if count > 0}
    node = min(left)
    seen = {}
    path = []
    while node not in seen:
        seen[node] = len(path)
        path.append(node)
        node = min(n for n in deps[node] if n in left)
    cycle = path[seen[node]:] + [node]
    raise CycleError(cycle)
