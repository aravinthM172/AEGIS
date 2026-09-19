"""Background traffic during an experiment, so blast radius has data to measure.

Sends POST /api/jobs to the workload entry point (the gateway) at a fixed rate. The
requests are ordinary traffic: the services emit their own telemetry for them, so this
module records nothing except how much it sent.
"""
import logging
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx

logger = logging.getLogger("experiments.workload")

WORKLOAD_URL = os.getenv("WORKLOAD_URL", "http://gateway:8090")
MAX_SAMPLES = 5000  # 25 rps for 200s; beyond this only the counters keep growing


class WorkloadRunner:
    def __init__(self, rps: float, base_url: str = WORKLOAD_URL, n: int = 100000,
                 timeout_s: float = 8.0, trace_prefix: str = "exp"):
        self.rps = rps
        self.n = n
        self.timeout_s = timeout_s
        self.trace_prefix = trace_prefix
        self._client = httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout_s, connect=2.0))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status = Counter()
        self._sent = 0
        self._samples: list[list] = []  # [epoch_ts, latency_ms, status]
        self._stats: dict | None = None
        self._pool: ThreadPoolExecutor | None = None

    def start(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max(8, int(self.rps * 4)))
        self._thread = threading.Thread(target=self._loop, name="experiment-workload", daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        """Stop sending (idempotent) and return what was sent. Does not wait for in-flight requests."""
        if self._stats is not None:
            return self._stats
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            ok = sum(v for k, v in self._status.items() if 200 <= k < 300)
            self._stats = {"target_rps": self.rps, "sent": self._sent, "completed": sum(self._status.values()),
                           "ok": ok, "failed": sum(self._status.values()) - ok,
                           "status_counts": {str(k): v for k, v in sorted(self._status.items())},
                           "samples": list(self._samples)}
        self._client.close()
        return self._stats

    def _loop(self) -> None:
        interval = 1.0 / self.rps
        next_at = time.monotonic()
        while not self._stop.is_set():
            delay = next_at - time.monotonic()
            if delay > 0 and self._stop.wait(delay):
                break
            try:
                self._pool.submit(self._one)
                with self._lock:
                    self._sent += 1
            except RuntimeError:
                break  # pool already shut down
            next_at += interval

    def _one(self) -> None:
        started = time.perf_counter()
        try:
            status = self._client.post(
                "/api/jobs", params={"n": self.n},
                headers={"X-Trace-Id": f"{self.trace_prefix}-{time.time_ns() % 10**9:09d}"}).status_code
        except Exception:
            status = 0  # connection error or client timeout
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        with self._lock:
            self._status[status] += 1
            if len(self._samples) < MAX_SAMPLES:
                self._samples.append([round(time.time(), 3), latency_ms, status])
