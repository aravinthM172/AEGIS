import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.experiments.campaign import CampaignRow, CampaignRunner, expand, validate_plan
from app.experiments.engine import ActiveExperimentExists, ValidationFailed

ITEM_A = {"target": "cpp-service", "fault_type": "stop_container", "duration_s": 5}
ITEM_B = {"target": "redis", "fault_type": "pause_container", "duration_s": 5}


class FakeEngine:
    """Experiments 'run' for a few polls, then finish with an analysis result."""

    def __init__(self, busy_first=0, ticks_to_finish=2, final_status="COMPLETED", analyse=True):
        self.created, self.aborted = [], []
        self._state = {}
        self.busy_first = busy_first
        self.ticks = ticks_to_finish
        self.final_status = final_status
        self.analyse = analyse

    def create(self, **body):
        if self.busy_first > 0:
            self.busy_first -= 1
            raise ActiveExperimentExists("someone-else")
        exp_id = f"exp-{len(self.created) + 1}"
        self.created.append(body)
        self._state[exp_id] = 0
        return {"id": exp_id}

    def get(self, exp_id):
        self._state[exp_id] += 1
        done = self._state[exp_id] > self.ticks
        result = {"status": "analyzed"} if done and self.analyse else None
        return {"id": exp_id, "status": self.final_status if done else "RUNNING", "result": result, "events": []}

    def abort(self, exp_id):
        self.aborted.append(exp_id)
        self.ticks = 0


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}", connect_args={"check_same_thread": False, "timeout": 15})
    CampaignRow.__table__.create(engine)
    return sessionmaker(bind=engine)


def runner(factory, engine, **kw):
    return CampaignRunner(engine, factory, poll_s=0.01, busy_retry_s=0.01, **kw)


def wait_status(r, cid, wanted, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        c = r.get(cid)
        if c["status"] in wanted:
            return c
        time.sleep(0.01)
    raise AssertionError(f"campaign stuck: {r.get(cid)}")


def test_runs_are_interleaved_repetition_major():
    runs = expand({"repetitions": 2, "experiments": [ITEM_A, ITEM_B]})
    assert [r["target"] for r in runs] == ["cpp-service", "redis", "cpp-service", "redis"]


def test_plan_validation():
    assert validate_plan({"experiments": [ITEM_A], "repetitions": 3}) == []
    assert validate_plan({"experiments": []})
    assert any("repetitions" in e for e in validate_plan({"experiments": [ITEM_A], "repetitions": 0}))
    assert any("needs target" in e for e in validate_plan({"experiments": [{"target": "x"}]}))
    assert any("limited to 60" in e for e in validate_plan({"experiments": [ITEM_A] * 7, "repetitions": 9}))


def test_campaign_runs_every_experiment_in_order_and_records_ids(factory):
    engine = FakeEngine()
    r = runner(factory, engine)
    started = r.start({"experiments": [ITEM_A, ITEM_B], "repetitions": 2}, cooldown_s=0)
    done = wait_status(r, started["id"], ("COMPLETED",))
    assert done["completed"] == 4 == done["total"]
    assert done["experiment_ids"] == ["exp-1", "exp-2", "exp-3", "exp-4"]
    assert [b["target"] for b in engine.created] == ["cpp-service", "redis", "cpp-service", "redis"]
    assert done["current_experiment_id"] is None and done["finished_at"]


def test_invalid_plan_is_rejected_before_anything_starts(factory):
    r = runner(factory, FakeEngine())
    with pytest.raises(ValidationFailed):
        r.start({"experiments": []})
    assert r.list_campaigns() == []


def test_waits_for_a_busy_slot_instead_of_failing(factory):
    engine = FakeEngine(busy_first=3)
    r = runner(factory, engine)
    done = wait_status(r, r.start({"experiments": [ITEM_A]}, cooldown_s=0)["id"], ("COMPLETED",))
    assert done["completed"] == 1


def test_abort_stops_the_campaign_and_the_experiment_in_flight(factory):
    engine = FakeEngine(ticks_to_finish=400)
    r = runner(factory, engine)
    cid = r.start({"experiments": [ITEM_A, ITEM_B], "repetitions": 3}, cooldown_s=0)["id"]
    deadline = time.monotonic() + 3
    while not r.get(cid)["current_experiment_id"] and time.monotonic() < deadline:
        time.sleep(0.01)
    r.abort(cid)
    done = wait_status(r, cid, ("ABORTED",))
    assert engine.aborted and done["completed"] < done["total"]


def test_a_rollback_failed_experiment_stops_the_campaign(factory):
    r = runner(factory, FakeEngine(final_status="ROLLBACK_FAILED"))
    done = wait_status(r, r.start({"experiments": [ITEM_A, ITEM_B]}, cooldown_s=0)["id"], ("FAILED",))
    assert "ROLLBACK_FAILED" in done["error"] and done["completed"] == 0


def test_analysis_that_never_arrives_does_not_hang_the_campaign(factory):
    r = runner(factory, FakeEngine(analyse=False), analysis_timeout_s=0.1)
    done = wait_status(r, r.start({"experiments": [ITEM_A]}, cooldown_s=0)["id"], ("COMPLETED",))
    assert done["completed"] == 1


def test_campaign_left_running_by_a_restart_is_marked_interrupted(factory):
    with factory() as db:
        db.add(CampaignRow(id="c1", name="x", status="RUNNING", plan={}, total=3, completed=1, experiment_ids=[],
                           cooldown_s=0, abort_requested=False))
        db.commit()
    r = runner(factory, FakeEngine())
    assert r.recover_interrupted() == ["c1"]
    assert r.get("c1")["status"] == "INTERRUPTED" and "restarted" in r.get("c1")["error"]
