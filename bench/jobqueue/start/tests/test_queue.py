import unittest

from jobqueue import JobQueue


class JobQueueTest(unittest.TestCase):
    def test_runs_jobs(self):
        q = JobQueue(workers=1)
        ids = [q.submit(pow, 2, n) for n in range(5)]
        self.assertEqual(ids, [0, 1, 2, 3, 4])
        self.assertEqual([r.value for r in q.results()], [1, 2, 4, 8, 16])
        q.shutdown()


if __name__ == "__main__":
    unittest.main()
