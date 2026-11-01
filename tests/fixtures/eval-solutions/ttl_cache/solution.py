"""Reference solution: one OrderedDict by recency and one by expiry.

Every entry has the same ttl, so the order of puts is the order of expiry.
"""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, ttl, clock=time.monotonic):
        if capacity < 1 or ttl <= 0:
            raise ValueError("capacity must be at least 1 and ttl positive")
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self.recency = OrderedDict()  # key -> value, least recent first
        self.expiry = OrderedDict()   # key -> expiry time, soonest first

    def _purge(self):
        now = self.clock()
        while self.expiry:
            key, expires = next(iter(self.expiry.items()))
            if now < expires:
                break
            del self.expiry[key]
            del self.recency[key]

    def get(self, key, default=None):
        self._purge()
        if key not in self.recency:
            return default
        self.recency.move_to_end(key)
        return self.recency[key]

    def put(self, key, value):
        self._purge()
        if key not in self.recency and len(self.recency) >= self.capacity:
            oldest, _ = self.recency.popitem(last=False)
            del self.expiry[oldest]
        self.recency[key] = value
        self.recency.move_to_end(key)
        self.expiry.pop(key, None)
        self.expiry[key] = self.clock() + self.ttl

    def delete(self, key):
        self._purge()
        if key not in self.recency:
            return False
        del self.recency[key]
        del self.expiry[key]
        return True

    def __len__(self):
        self._purge()
        return len(self.recency)

    def __contains__(self, key):
        self._purge()
        return key in self.recency
