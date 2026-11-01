Write a Python module `solution.py` with a size-limited cache whose entries also expire:

```python
class TTLCache:
    def __init__(self, capacity, ttl, clock=time.monotonic): ...
    def get(self, key, default=None): ...
    def put(self, key, value): ...
    def delete(self, key): ...       # True if a live entry was removed
    def __len__(self): ...           # live entries only
    def __contains__(self, key): ... # live entries only
```

- `capacity` is the most entries kept; `ttl` is how many seconds an entry lives. Raise `ValueError` if `capacity < 1` or `ttl <= 0`.
- `clock` returns the current time in seconds. Call it, never `time` directly, so tests can control time.
- An entry put at time `t` is live while `clock() < t + ttl`. Once expired it behaves exactly as if it were never there.
- `put` on an existing key replaces the value and restarts its lifetime.
- Both `get` (when it finds a live entry) and `put` make the key the most recently used. `get` does not extend the lifetime.
- When `put` adds a new key and the cache already holds `capacity` live entries, it removes the least recently used live entry. Expired entries never count towards the capacity.
- Every operation must take constant time on average: 200,000 operations on a cache of capacity 1,000 must finish in well under two seconds, however entries expire.

Example:

```python
now = [0.0]
cache = TTLCache(2, ttl=10, clock=lambda: now[0])
cache.put("a", 1); cache.put("b", 2)
cache.get("a")          # 1; "b" is now the least recently used
cache.put("c", 3)       # evicts "b"
now[0] = 10.0           # "a" and "c" expire now
len(cache)              # 0
```
