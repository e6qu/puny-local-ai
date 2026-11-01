import time
import unittest

from solution import CycleError, order


def check_cycle(test, tasks, cycle):
    test.assertGreaterEqual(len(cycle), 2)
    test.assertEqual(cycle[0], cycle[-1])
    test.assertEqual(len(set(cycle[:-1])), len(cycle) - 1, "a simple cycle visits each task once")
    for a, b in zip(cycle, cycle[1:]):
        test.assertIn(b, list(tasks.get(a, ())), f"{a} does not depend on {b}")


class OrderTest(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(order({"b": ["a"], "c": ["a"], "a": []}), ["a", "b", "c"])
        self.assertEqual(order({"app": ["lib", "log"], "lib": ["log"]}), ["log", "lib", "app"])

    def test_smallest_available_name_comes_first(self):
        tasks = {"d": [], "c": [], "b": ["d"], "a": ["c", "d"]}
        self.assertEqual(order(tasks), ["c", "d", "a", "b"])

    def test_dependencies_that_are_not_keys(self):
        self.assertEqual(order({"z": ["m", "n"]}), ["m", "n", "z"])

    def test_any_iterable_and_duplicates(self):
        self.assertEqual(order({"b": ("a", "a"), "c": {"b"}, "a": iter([])}), ["a", "b", "c"])

    def test_every_task_once_and_after_its_dependencies(self):
        tasks = {f"t{i:03d}": [f"t{j:03d}" for j in range(i) if (i * 7 + j) % 5 == 0] for i in range(200)}
        result = order(tasks)
        self.assertEqual(sorted(result), sorted(tasks))
        position = {name: i for i, name in enumerate(result)}
        for name, deps in tasks.items():
            for dep in deps:
                self.assertLess(position[dep], position[name])

    def test_empty(self):
        self.assertEqual(order({}), [])

    def test_two_task_cycle(self):
        tasks = {"x": ["y"], "y": ["x"]}
        with self.assertRaises(CycleError) as caught:
            order(tasks)
        check_cycle(self, tasks, caught.exception.cycle)

    def test_self_dependency(self):
        with self.assertRaises(CycleError) as caught:
            order({"a": ["a"], "b": []})
        self.assertEqual(caught.exception.cycle, ["a", "a"])

    def test_cycle_behind_a_valid_part(self):
        tasks = {"start": ["p"], "p": ["q"], "q": ["r"], "r": ["s"], "s": ["q"], "free": []}
        with self.assertRaises(CycleError) as caught:
            order(tasks)
        check_cycle(self, tasks, caught.exception.cycle)
        self.assertEqual(set(caught.exception.cycle), {"q", "r", "s"})

    def test_cycle_error_is_a_value_error(self):
        self.assertTrue(issubclass(CycleError, ValueError))

    def test_long_chain_is_fast(self):
        n = 100000
        tasks = {f"n{i:06d}": [f"n{i + 1:06d}"] for i in range(n)}
        started = time.monotonic()
        result = order(tasks)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(result[0], f"n{n:06d}")
        self.assertEqual(result[-1], "n000000")

    def test_long_cycle_is_found_without_recursion(self):
        n = 50000
        tasks = {f"c{i}": [f"c{(i + 1) % n}"] for i in range(n)}
        with self.assertRaises(CycleError) as caught:
            order(tasks)
        self.assertEqual(len(caught.exception.cycle), n + 1)
        check_cycle(self, tasks, caught.exception.cycle)


if __name__ == "__main__":
    unittest.main()
