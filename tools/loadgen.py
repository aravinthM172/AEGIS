"""Steady traffic generator for the gateway (stdlib only).

    python tools/loadgen.py --rps 5 --seconds 60

Sends POST /api/jobs at a fixed rate from a small thread pool and prints one summary
line per second: ok / error counts, status histogram and p50/p95 latency.
"""
import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


def one_request(url: str, n: int, timeout: float, trace_prefix: str):
    trace = f"{trace_prefix}-{uuid.uuid4().hex[:8]}"
    req = urllib.request.Request(f"{url}/api/jobs?n={n}", method="POST", headers={"X-Trace-Id": trace})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception:
        status = 0  # connection error / client timeout
    return status, (time.perf_counter() - started) * 1000


def run(url: str, rps: float, seconds: float, n: int, timeout: float, quiet: bool = False,
        trace_prefix: str = "load") -> dict:
    lock = threading.Lock()
    buckets: dict[int, list] = {}
    start = time.time()

    def record(second: int, status: int, ms: float):
        with lock:
            buckets.setdefault(second, []).append((status, ms))

    def task(second: int):
        status, ms = one_request(url, n, timeout, trace_prefix)
        record(second, status, ms)

    total_sent = 0
    with ThreadPoolExecutor(max_workers=max(8, int(rps * 4))) as pool:
        interval = 1.0 / rps
        next_at = start
        while next_at - start < seconds:
            delay = next_at - time.time()
            if delay > 0:
                time.sleep(delay)
            pool.submit(task, int(next_at - start))
            total_sent += 1
            next_at += interval
        pool.shutdown(wait=True)

    summary = Counter()
    for second in sorted(buckets):
        rows = buckets[second]
        statuses = Counter(s for s, _ in rows)
        lat = sorted(ms for _, ms in rows)
        ok = sum(v for k, v in statuses.items() if 200 <= k < 300)
        summary["ok"] += ok
        summary["error"] += len(rows) - ok
        if not quiet:
            p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
            print(f"t+{second:3d}s  ok={ok:3d} err={len(rows) - ok:3d}  "
                  f"p50={statistics.median(lat):7.1f}ms p95={p95:7.1f}ms  {dict(statuses)}", flush=True)
    return {"sent": total_sent, "ok": summary["ok"], "error": summary["error"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8090")
    ap.add_argument("--rps", type=float, default=5)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--n", type=int, default=100000, help="prime-count bound sent to /api/jobs")
    ap.add_argument("--timeout", type=float, default=8.0)
    args = ap.parse_args()
    print(json.dumps(run(args.url, args.rps, args.seconds, args.n, args.timeout)))
