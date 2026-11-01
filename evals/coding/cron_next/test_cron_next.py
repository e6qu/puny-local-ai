import time
import unittest
from datetime import datetime, timedelta

from solution import next_time


def brute(expression, after, limit_days=800):
    """Minute by minute, for checking: slow but plain."""
    fields = expression.split()

    def values(field, low, high, names=None):
        out = set()
        for part in field.lower().split(","):
            step = 1
            if "/" in part:
                part, step = part.split("/")
                step = int(step)
            if names:
                for i, n in enumerate(names):
                    part = part.replace(n, str(i + low))
            if part == "*":
                a, b = low, high
            elif "-" in part:
                a, b = map(int, part.split("-"))
            else:
                a = int(part)
                b = high if step != 1 else a
            out.update(range(a, b + 1, step))
        return out

    minutes = values(fields[0], 0, 59)
    hours = values(fields[1], 0, 23)
    doms = values(fields[2], 1, 31)
    months = values(fields[3], 1, 12, ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])
    dows = {d % 7 for d in values(fields[4], 0, 7, ["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = after + timedelta(days=limit_days)
    while t < end:
        dom_ok, dow_ok = t.day in doms, (t.isoweekday() % 7) in dows
        if fields[2] != "*" and fields[4] != "*":
            day_ok = dom_ok or dow_ok
        else:
            day_ok = dom_ok and dow_ok
        if t.minute in minutes and t.hour in hours and t.month in months and day_ok:
            return t
        t += timedelta(minutes=1)
    return None


class NextTimeTest(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(next_time("*/15 * * * *", datetime(2026, 1, 1, 10, 7)), datetime(2026, 1, 1, 10, 15))
        self.assertEqual(next_time("0 9 * * mon-fri", datetime(2026, 1, 2, 9, 0)), datetime(2026, 1, 5, 9, 0))
        self.assertEqual(next_time("0 0 29 2 *", datetime(2026, 3, 1)), datetime(2028, 2, 29))

    def test_strictly_after(self):
        self.assertEqual(next_time("30 10 * * *", datetime(2026, 5, 5, 10, 30)), datetime(2026, 5, 6, 10, 30))
        self.assertEqual(next_time("30 10 * * *", datetime(2026, 5, 5, 10, 29, 59, 999999)), datetime(2026, 5, 5, 10, 30))
        self.assertEqual(next_time("* * * * *", datetime(2026, 12, 31, 23, 59, 30)), datetime(2027, 1, 1, 0, 0))

    def test_lists_ranges_and_steps(self):
        self.assertEqual(next_time("5,35 */6 * * *", datetime(2026, 7, 1, 6, 36)), datetime(2026, 7, 1, 12, 5))
        self.assertEqual(next_time("0 8-18/5 * * *", datetime(2026, 7, 1, 13, 1)), datetime(2026, 7, 1, 18, 0))
        self.assertEqual(next_time("10/20 * * * *", datetime(2026, 7, 1, 0, 51)), datetime(2026, 7, 1, 1, 10))
        self.assertEqual(next_time("0 0 1 */4 *", datetime(2026, 2, 1)), datetime(2026, 5, 1))

    def test_names_in_any_case(self):
        self.assertEqual(next_time("0 12 1 JUN,Dec *", datetime(2026, 6, 2)), datetime(2026, 12, 1, 12, 0))
        # 1 Jan 2026 is a Thursday.
        self.assertEqual(next_time("0 0 * * Fri-SAT", datetime(2026, 1, 1)), datetime(2026, 1, 2))
        self.assertEqual(next_time("0 0 * * Fri-SAT", datetime(2026, 1, 3, 1)), datetime(2026, 1, 9))

    def test_sunday_is_zero_and_seven(self):
        sunday = datetime(2026, 1, 4)
        self.assertEqual(next_time("0 0 * * 0", datetime(2026, 1, 1)), sunday)
        self.assertEqual(next_time("0 0 * * 7", datetime(2026, 1, 1)), sunday)

    def test_day_of_month_or_day_of_week(self):
        # The 13th, or any Friday: 2 Jan 2026 is a Friday.
        self.assertEqual(next_time("0 0 13 * 5", datetime(2026, 1, 1)), datetime(2026, 1, 2))
        self.assertEqual(next_time("0 0 13 * 5", datetime(2026, 1, 10)), datetime(2026, 1, 13))
        # With one of them *, only the other counts.
        self.assertEqual(next_time("0 0 13 * *", datetime(2026, 1, 1)), datetime(2026, 1, 13))
        self.assertEqual(next_time("0 0 * * 5", datetime(2026, 1, 3)), datetime(2026, 1, 9))
        # */1 is a restriction, even though it allows every day.
        self.assertEqual(next_time("0 0 */1 * 5", datetime(2026, 1, 3)), datetime(2026, 1, 4))

    def test_impossible_schedules(self):
        for expression in ("0 0 30 2 *", "0 0 31 4,6,9,11 *"):
            with self.subTest(expression):
                started = time.monotonic()
                with self.assertRaises(ValueError):
                    next_time(expression, datetime(2026, 1, 1))
                self.assertLess(time.monotonic() - started, 1.0)

    def test_rare_schedule_found_fast(self):
        started = time.monotonic()
        # In February, the 29th or any Monday: 1 February 2027 is the first such day.
        self.assertEqual(next_time("0 0 29 2 mon", datetime(2026, 3, 1)), datetime(2027, 2, 1))
        self.assertEqual(next_time("59 23 29 2 *", datetime(2028, 3, 1)), datetime(2032, 2, 29, 23, 59))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_malformed(self):
        for expression in ("* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *", "* * * 13 *",
                           "* * * * 8", "5-1 * * * *", "*/0 * * * *", "a * * * *", "1,,2 * * * *", "* * * foo *", "-1 * * * *", ""):
            with self.subTest(expression), self.assertRaises(ValueError):
                next_time(expression, datetime(2026, 1, 1))

    def test_matches_minute_by_minute(self):
        expressions = ["7 */3 * * *", "0,30 9-17 * * mon-fri", "15 4 1,15 * *", "0 0 10-20/5 * 2", "*/7 22-23 * 3-5 *",
                       "0 12 * jan,jul sun", "45 23 31 * *", "1-59/29 0 * * 3", "0 6 8 */2 1-3"]
        starts = [datetime(2026, 1, 31, 23, 50), datetime(2026, 2, 28, 22, 0), datetime(2027, 12, 31, 23, 59), datetime(2028, 2, 27, 12, 0)]
        for expression in expressions:
            for start in starts:
                with self.subTest(expression=expression, start=start):
                    self.assertEqual(next_time(expression, start), brute(expression, start))


if __name__ == "__main__":
    unittest.main()
