import random
import time
import unittest

from solution import TTLCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TTLCacheTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def cache(self, capacity, ttl=10):
        return TTLCache(capacity, ttl, clock=self.clock)

    def test_example(self):
        cache = self.cache(2)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertEqual(cache.get("a"), 1)
        cache.put("c", 3)
        self.assertNotIn("b", cache)
        self.assertEqual((cache.get("a"), cache.get("c")), (1, 3))
        self.clock.now = 10.0
        self.assertEqual(len(cache), 0)

    def test_expiry_boundary(self):
        cache = self.cache(5, ttl=3)
        cache.put("k", "v")
        self.clock.now = 2.999
        self.assertEqual(cache.get("k"), "v")
        self.clock.now = 3.0
        self.assertIsNone(cache.get("k"))
        self.assertEqual(cache.get("k", "gone"), "gone")
        self.assertNotIn("k", cache)

    def test_put_restarts_the_lifetime_but_get_does_not(self):
        cache = self.cache(5, ttl=10)
        cache.put("a", 1)
        cache.put("b", 1)
        self.clock.now = 8
        cache.put("a", 2)
        cache.get("b")
        self.clock.now = 12
        self.assertEqual(cache.get("a"), 2)
        self.assertIsNone(cache.get("b"))

    def test_expired_entries_do_not_count_towards_capacity(self):
        cache = self.cache(2, ttl=5)
        cache.put("old1", 1)
        cache.put("old2", 2)
        self.clock.now = 6
        cache.put("new1", 3)
        cache.put("new2", 4)
        self.assertEqual(len(cache), 2)
        self.assertEqual((cache.get("new1"), cache.get("new2")), (3, 4))

    def test_eviction_skips_nothing_live_but_the_least_recent(self):
        cache = self.cache(3, ttl=100)
        for key in "abc":
            cache.put(key, key)
        cache.get("a")
        cache.put("b", "B")
        cache.put("d", "d")
        self.assertEqual(sorted(k for k in "abcd" if k in cache), ["a", "b", "d"])

    def test_eviction_prefers_expired_over_live(self):
        cache = self.cache(2, ttl=10)
        cache.put("live", 1)
        self.clock.now = 5
        cache.put("short", 2)
        cache.get("live")
        self.clock.now = 10
        cache.put("new", 3)
        # "live" expired at 10; "short" is still live and stays.
        self.assertEqual(sorted(k for k in ("live", "short", "new") if k in cache), ["new", "short"])

    def test_replacing_does_not_evict(self):
        cache = self.cache(2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 3)
        self.assertEqual((cache.get("a"), cache.get("b"), len(cache)), (3, 2, 2))

    def test_delete(self):
        cache = self.cache(2, ttl=5)
        cache.put("a", 1)
        self.assertTrue(cache.delete("a"))
        self.assertFalse(cache.delete("a"))
        cache.put("b", 2)
        self.clock.now = 5
        self.assertFalse(cache.delete("b"))
        self.assertFalse(cache.delete("never"))

    def test_falsy_values(self):
        cache = self.cache(3)
        cache.put("zero", 0)
        cache.put("none", None)
        self.assertEqual(cache.get("zero", "missing"), 0)
        self.assertIsNone(cache.get("none", "missing"))
        self.assertIn("none", cache)

    def test_bad_arguments(self):
        for capacity, ttl in ((0, 1), (-1, 1), (1, 0), (1, -5)):
            with self.subTest(capacity=capacity, ttl=ttl), self.assertRaises(ValueError):
                TTLCache(capacity, ttl, clock=self.clock)

    def test_uses_time_monotonic_by_default(self):
        cache = TTLCache(1, 60)
        cache.put("a", 1)
        self.assertEqual(cache.get("a"), 1)

    def test_matches_a_simple_model(self):
        rng = random.Random(3)
        cache = self.cache(4, ttl=7)
        model = {}  # key -> (value, put_time, last_use)
        tick = 0
        for _ in range(4000):
            tick += 1
            self.clock.now += rng.choice((0, 0, 1, 2, 3))
            now = self.clock.now
            live = {k: v for k, v in model.items() if now < v[1] + 7}
            model = live
            key = rng.choice("abcdefgh")
            op = rng.random()
            if op < 0.5:
                if key not in model and len(model) >= 4:
                    del model[min(model, key=lambda k: model[k][2])]
                model[key] = (tick, now, tick)
                cache.put(key, tick)
            elif op < 0.85:
                expected = model[key][0] if key in model else None
                if key in model:
                    model[key] = (model[key][0], model[key][1], tick)
                self.assertEqual(cache.get(key), expected)
            else:
                self.assertEqual(cache.delete(key), key in model)
                model.pop(key, None)
            self.assertEqual(len(cache), len(model))

    def test_many_operations_are_fast(self):
        rng = random.Random(9)
        cache = self.cache(1000, ttl=50)
        started = time.monotonic()
        for i in range(200000):
            self.clock.now = i / 100
            key = rng.randrange(5000)
            if i % 3:
                cache.put(key, i)
            else:
                cache.get(key)
            if i % 1000 == 0:
                len(cache)
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
