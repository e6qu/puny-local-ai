import threading
import time
import unittest

from jobqueue import JobQueue


def finishes(fn, seconds=5):
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    thread.join(seconds)
    return not thread.is_alive()


class JobQueueTest(unittest.TestCase):
    def test_runs_jobs(self):
        q = JobQueue(workers=1)
        ids = [q.submit(pow, 2, n) for n in range(5)]
        self.assertEqual(ids, [0, 1, 2, 3, 4])
        self.assertEqual([r.value for r in q.results()], [1, 2, 4, 8, 16])
        q.shutdown()

    def test_shutdown_stops_every_worker(self):
        q = JobQueue(workers=3)
        self.assertTrue(finishes(q.shutdown))

    def test_failing_job_is_recorded_and_others_run(self):
        q = JobQueue(workers=1)
        q.submit(lambda: 1 / 0)
        q.submit(lambda: 2)
        out = []
        self.assertTrue(finishes(lambda: out.extend(q.results())))
        self.assertIsInstance(out[0].error, ZeroDivisionError)
        self.assertEqual(out[1].value, 2)
        q.shutdown()

    def test_retries_count_extra_tries(self):
        calls = []

        def fail():
            calls.append(1)
            raise RuntimeError

        q = JobQueue(workers=1)
        q.submit(fail, retries=2)
        self.assertEqual(q.results()[0].attempts, 3)
        self.assertEqual(len(calls), 3)
        q.shutdown()

    def test_results_in_submission_order(self):
        q = JobQueue(workers=3)
        for i in range(6):
            q.submit(lambda i=i: (time.sleep(0.01 * (6 - i)), i)[1])
        self.assertEqual([r.value for r in q.results()], list(range(6)))
        q.shutdown()


if __name__ == "__main__":
    unittest.main()
