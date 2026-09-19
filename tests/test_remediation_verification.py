from app.remediation.verification import (
    FALLBACK_P95_MS,
    judge_probes,
    latency_threshold,
    verify_recovery,
)


def probes(n=10, ok=10, latency=25.0, bad_latency=None):
    rows = [{"ok": True, "latency_ms": latency, "status": 200} for _ in range(ok)]
    rows += [{"ok": False, "latency_ms": bad_latency or 1000.0, "status": 502} for _ in range(n - ok)]
    return rows


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_threshold_follows_the_baseline_or_falls_back():
    assert latency_threshold(20.0) == 120.0    # baseline + 100ms dominates a small baseline
    assert latency_threshold(500.0) == 1000.0  # 2x dominates a large baseline
    assert latency_threshold(None) == FALLBACK_P95_MS


def test_healthy_probes_verify():
    r = judge_probes(probes(), reference_p95_ms=20.0)
    assert r["verified"] and r["success_rate"] == 1.0 and r["p95_ms"] == 25.0


def test_failing_or_slow_or_too_few_probes_do_not_verify():
    assert "success rate 80%" in judge_probes(probes(ok=8), 20.0)["reason"]
    assert "exceeds" in judge_probes(probes(latency=900.0), 20.0)["reason"]
    assert "only 3 probes" in judge_probes(probes(n=3, ok=3), 20.0)["reason"]
    assert judge_probes(probes(ok=0), 20.0)["p95_ms"] is None


def test_verification_reports_measured_recovery_time_after_a_delayed_recovery():
    clock = Clock()
    rounds = iter([probes(ok=0), probes(ok=0), probes(ok=9)])  # service comes back on the third round

    result = verify_recovery(lambda n: next(rounds), 20.0, settle_s=3, interval_s=3, max_wait_s=45,
                             clock=clock.now, sleep=clock.sleep)
    assert result["verified"] and result["rounds"] == 3
    assert result["recovery_time_s"] == 9.0  # 3s settle + two 3s waits: measured from the fake clock, not assumed


def test_verification_gives_up_and_says_so_when_the_service_never_recovers():
    clock = Clock()
    result = verify_recovery(lambda n: probes(ok=0), 20.0, settle_s=3, interval_s=5, max_wait_s=20,
                             clock=clock.now, sleep=clock.sleep)
    assert result["verified"] is False and result["recovery_time_s"] is None
    assert "not healthy after 20s" in result["reason"] and result["rounds"] >= 3


def test_verification_succeeds_immediately_when_already_healthy():
    clock = Clock()
    result = verify_recovery(lambda n: probes(), 20.0, settle_s=2, clock=clock.now, sleep=clock.sleep)
    assert result["verified"] and result["rounds"] == 1 and result["recovery_time_s"] == 2.0
