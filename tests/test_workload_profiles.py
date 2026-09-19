import json
import os

import httpx
import pytest

from app.experiments.workload import WorkloadRunner, load_profile

REPO_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "workloads.json")


def _write(tmp_path, data):
    f = tmp_path / "workloads.json"
    f.write_text(json.dumps(data))
    return str(f)


def test_unknown_target_uses_default_workload(tmp_path):
    assert load_profile("gateway", _write(tmp_path, {"job-tracker": {"base_url": "http://x", "requests": [{"path": "/"}]}})) is None


def test_missing_file_means_default(tmp_path):
    assert load_profile("anything", str(tmp_path / "nope.json")) is None


def test_invalid_profile_rejected(tmp_path):
    with pytest.raises(ValueError, match="base_url"):
        load_profile("a", _write(tmp_path, {"a": {"requests": [{"path": "/"}]}}))
    with pytest.raises(ValueError, match="bad request"):
        load_profile("a", _write(tmp_path, {"a": {"base_url": "http://x", "requests": [{"method": "DELETE", "path": "/x"}]}}))
    with pytest.raises(ValueError, match="bad request"):
        load_profile("a", _write(tmp_path, {"a": {"base_url": "http://x", "requests": [{"path": "x"}]}}))


def test_repository_profile_targets_only_the_job_tracker_and_is_valid():
    profile = load_profile("job-tracker", REPO_CONFIG)
    assert profile["base_url"] == "http://job-tracker:8000"
    assert load_profile("cpp-service", REPO_CONFIG) is None
    assert all(r["path"].startswith("/") for r in profile["requests"])


def test_runner_uses_profile_requests_and_records_real_statuses():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("x-trace-id")))
        return httpx.Response(503 if request.url.path == "/jobs" else 200)

    profile = {"base_url": "http://tracker", "requests": [{"method": "GET", "path": "/health"},
                                                          {"method": "POST", "path": "/jobs", "json": {"a": 1}}]}
    runner = WorkloadRunner(rps=1, profile=profile, trace_prefix="t")
    runner._client = httpx.Client(base_url="http://tracker", transport=httpx.MockTransport(handler))
    for _ in range(40):
        runner._one()
    stats = runner.stop()
    assert {(m, p) for m, p, _ in seen} == {("GET", "/health"), ("POST", "/jobs")}
    assert all(t.startswith("t-") for _, _, t in seen)
    assert stats["status_counts"].keys() == {"200", "503"}
    assert stats["completed"] == 40 and stats["failed"] == stats["status_counts"]["503"]


def test_runner_without_profile_keeps_the_gateway_behaviour():
    seen = []
    runner = WorkloadRunner(rps=1, n=7)
    runner._client = httpx.Client(base_url="http://gw", transport=httpx.MockTransport(
        lambda r: (seen.append((r.method, r.url.path, r.url.query)), httpx.Response(200))[1]))
    runner._one()
    assert seen == [("POST", "/api/jobs", b"n=7")]
