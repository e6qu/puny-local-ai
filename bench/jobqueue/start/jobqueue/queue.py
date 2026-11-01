"""A small pool of worker threads that runs jobs and collects their results."""

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from .retry import attempts_allowed, backoff_seconds

_STOP = object()


@dataclass
class JobResult:
    """The outcome of one job: its value, or the exception of its last try."""
    job_id: int
    value: Any = None
    error: Optional[BaseException] = None
    attempts: int = 0

    @property
    def ok(self):
        return self.error is None


class JobQueue:
    """Runs submitted jobs on `workers` threads.

    submit(fn, *args, retries=0) queues fn(*args) and returns its job id, counting from 0.
        A job that raises is tried again, up to `retries` more times, waiting
        backoff_seconds between tries; the last exception is kept in its result.
    results() waits for every submitted job and returns their JobResults in submission order.
    shutdown(wait=True) stops the workers once the queued jobs are done; with
        wait=True it returns only when every worker has stopped.
    """

    def __init__(self, workers=4):
        self._jobs = queue.Queue()
        self._results = []
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending = 0
        self._all_done = threading.Condition(self._lock)
        self._threads = [threading.Thread(target=self._work, daemon=True) for _ in range(workers)]
        for thread in self._threads:
            thread.start()

    def submit(self, fn, *args, retries=0):
        with self._lock:
            job_id = self._next_id
            self._next_id += 1
            self._pending += 1
        self._jobs.put((job_id, fn, args, retries))
        return job_id

    def _work(self):
        while True:
            item = self._jobs.get()
            if item is _STOP:
                return
            job_id, fn, args, retries = item
            result = self._run(job_id, fn, args, retries)
            with self._lock:
                self._results.append(result)
                self._pending -= 1
                if self._pending == 0:
                    self._all_done.notify_all()

    def _run(self, job_id, fn, args, retries):
        attempt = 0
        while True:
            attempt += 1
            try:
                return JobResult(job_id, value=fn(*args), attempts=attempt)
            except Exception:
                if attempt >= attempts_allowed(retries):
                    raise
                time.sleep(backoff_seconds(attempt + 1))

    def results(self):
        with self._lock:
            while self._pending:
                self._all_done.wait()
            return list(self._results)

    def shutdown(self, wait=True):
        self._jobs.put(_STOP)
        if wait:
            for thread in self._threads:
                thread.join()
