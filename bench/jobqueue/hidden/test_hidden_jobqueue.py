import threading
import time
import unittest

from jobqueue import JobQueue, JobResult
from jobqueue.retry import attempts_allowed, backoff_seconds


def within(seconds, fn, *args):
    """Runs fn in a thread; fails the test instead of hanging the run."""
    out = {}

    def target():
        out["value"] = fn(*args)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise AssertionError(f"{getattr(fn, '__name__', fn)} did not return within {seconds}s")
    return out.get("value")


class Flaky:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, value):
        with self.lock:
            self.calls += 1
            if self.calls <= self.failures:
                raise RuntimeError(f"failure {self.calls}")
        return value


class HiddenJobQueueTest(unittest.TestCase):
    def test_shutdown_stops_every_worker(self):
        q = JobQueue(workers=4)
        q.submit(lambda: 1)
        within(5, q.shutdown)
        self.assertTrue(all(not t.is_alive() for t in q._threads))

    def test_shutdown_after_queued_jobs_runs_them_first(self):
        q = JobQueue(workers=3)
        done = []
        for i in range(30):
            q.submit(lambda i=i: (time.sleep(0.005), done.append(i)))
        within(10, q.shutdown)
        self.assertEqual(sorted(done), list(range(30)))

    def test_shutdown_without_waiting_returns_at_once(self):
        q = JobQueue(workers=2)
        q.submit(time.sleep, 0.3)
        started = time.monotonic()
        q.shutdown(wait=False)
        self.assertLess(time.monotonic() - started, 0.2)

    def test_a_failing_job_does_not_stop_the_others(self):
        q = JobQueue(workers=1)
        q.submit(lambda: 1 / 0)
        q.submit(lambda: "after")
        results = within(5, q.results)
        self.assertIsInstance(results[0].error, ZeroDivisionError)
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].attempts, 1)
        self.assertEqual((results[1].value, results[1].ok), ("after", True))
        within(5, q.shutdown)

    def test_many_failures_on_many_workers(self):
        q = JobQueue(workers=4)
        for i in range(40):
            q.submit((lambda i=i: i) if i % 3 else (lambda i=i: int("x")))
        results = within(10, q.results)
        self.assertEqual(len(results), 40)
        for i, r in enumerate(results):
            self.assertEqual(r.job_id, i)
            if i % 3:
                self.assertEqual(r.value, i)
            else:
                self.assertIsInstance(r.error, ValueError)
        within(5, q.shutdown)

    def test_retries_mean_extra_tries(self):
        self.assertEqual(attempts_allowed(0), 1)
        self.assertEqual(attempts_allowed(2), 3)
        q = JobQueue(workers=1)
        always = Flaky(failures=100)
        q.submit(always, "x", retries=2)
        twice = Flaky(failures=2)
        q.submit(twice, "y", retries=2)
        never = Flaky(failures=1)
        q.submit(never, "z")
        r = within(10, q.results)
        self.assertEqual((always.calls, r[0].attempts, str(r[0].error)), (3, 3, "failure 3"))
        self.assertEqual((twice.calls, r[1].attempts, r[1].value), (3, 3, "y"))
        self.assertEqual((never.calls, r[2].attempts, type(r[2].error)), (1, 1, RuntimeError))
        within(5, q.shutdown)

    def test_backoff_doubles_from_the_first_retry(self):
        self.assertEqual([backoff_seconds(a) for a in (2, 3, 4)], [0.01, 0.02, 0.04])
        self.assertEqual(backoff_seconds(30), 0.2)

    def test_results_come_in_submission_order(self):
        q = JobQueue(workers=4)
        for i in range(12):
            q.submit(lambda i=i: (time.sleep(0.002 * (12 - i)), i)[1])
        results = within(10, q.results)
        self.assertEqual([r.job_id for r in results], list(range(12)))
        self.assertEqual([r.value for r in results], list(range(12)))
        within(5, q.shutdown)

    def test_results_can_be_asked_again_after_more_jobs(self):
        q = JobQueue(workers=2)
        q.submit(lambda: "a")
        self.assertEqual([r.value for r in within(5, q.results)], ["a"])
        q.submit(lambda: "b")
        self.assertEqual([r.value for r in within(5, q.results)], ["a", "b"])
        within(5, q.shutdown)

    def test_job_result_is_unchanged(self):
        r = JobResult(3, value=1, attempts=1)
        self.assertTrue(r.ok)
        self.assertEqual((r.job_id, r.error), (3, None))


if __name__ == "__main__":
    unittest.main()
