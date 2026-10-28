from datetime import datetime, timedelta, timezone
import json
import threading
import time

import pytest

from cryptolab import get_batch_summary, get_run_status, track_batch, track_run
from cryptolab.database import Database
from tests.test_concurrency import wait_until


def test_heartbeat_updates_during_computation_and_stops_on_exit(query):
    owner_thread = threading.get_ident()
    with track_run(project="p", experiment="long", heartbeat_interval_s=0.03) as run:
        initial = get_run_status(run.id)
        wait_until(lambda: get_run_status(run.id)["last_heartbeat_at"] > initial["last_heartbeat_at"])
        second = get_run_status(run.id)
        wait_until(lambda: get_run_status(run.id)["last_heartbeat_at"] > second["last_heartbeat_at"])
        live = get_run_status(run.id)
        assert live["display_status"] == "RUNNING"
        assert live["thread_id"] == owner_thread
        assert live["finished_at"] is None and live["duration_s"] is None
    final = get_run_status(run.id)
    time.sleep(0.08)
    assert get_run_status(run.id) == final
    assert final["status"] == final["display_status"] == "COMPLETED"
    assert final["duration_s"] >= 0.06 and final["finished_at"]
    assert not any(thread.name == f"cryptolab-heartbeat-{run.id}" for thread in threading.enumerate())


def test_heartbeat_error_is_best_effort_then_recovers(monkeypatch, capsys):
    original = Database.heartbeat
    attempts = []
    def fail_twice(database, run_id):
        attempts.append(run_id)
        if len(attempts) <= 2:
            raise OSError("temporary heartbeat outage")
        return original(database, run_id)
    monkeypatch.setattr(Database, "heartbeat", fail_twice)
    with track_run(project="p", experiment="long", heartbeat_interval_s=0.02) as run:
        initial = get_run_status(run.id)["last_heartbeat_at"]
        wait_until(lambda: len(attempts) >= 3 and get_run_status(run.id)["last_heartbeat_at"] > initial)
    assert get_run_status(run.id)["status"] == "COMPLETED"
    assert capsys.readouterr().err.count("temporary heartbeat outage") == 1


def test_stale_status_is_read_only_and_resolves_after_next_heartbeat(query):
    with track_run(project="p", experiment="e", heartbeat_interval_s=60) as run:
        stale = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        with Database().connect() as connection:
            connection.execute("UPDATE runs SET last_heartbeat_at=? WHERE id=?", (stale, run.id))
        assert get_run_status(run.id)["display_status"] == "UNCONFIRMED"
        assert query("SELECT status,finished_at,duration_s FROM runs")[0] == {
            "status": "RUNNING", "finished_at": None, "duration_s": None,
        }
        Database().heartbeat(run.id)
        assert get_run_status(run.id)["display_status"] == "RUNNING"
    assert not Database().heartbeat(run.id)


def test_old_database_rows_without_heartbeat_remain_readable(query):
    with track_run(project="p", experiment="e", heartbeat_interval_s=60) as run:
        stale = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        with Database().connect() as connection:
            connection.execute("UPDATE runs SET last_heartbeat_at=NULL,started_at=? WHERE id=?", (stale, run.id))
        status = get_run_status(run.id)
        assert status["last_heartbeat_at"] is None and status["display_status"] == "UNCONFIRMED"


def test_summary_counts_every_status_without_overwriting_batch(query):
    with track_batch(project="p", experiment="e") as batch:
        assert get_batch_summary(batch.id)["total"] == 0
        with track_run(batch_id=batch.id):
            pass
        for error in (ValueError("failure"), KeyboardInterrupt()):
            with pytest.raises(type(error)):
                with track_run(batch_id=batch.id):
                    raise error
        with track_run(batch_id=batch.id) as live:
            with track_run(batch_id=batch.id, heartbeat_interval_s=60) as stale:
                with Database().connect() as connection:
                    connection.execute("UPDATE runs SET last_heartbeat_at=? WHERE id=?",
                                       ("2000-01-01T00:00:00+00:00", stale.id))
                summary = get_batch_summary(batch.id)
                assert summary == {"batch_id": batch.id, "batch_status": "RUNNING", "total": 5,
                                   "completed": 1, "failed": 1, "interrupted": 1, "running": 1, "unconfirmed": 1}
                assert get_run_status(live.id)["status"] == "RUNNING"
    assert get_batch_summary(batch.id)["batch_status"] == "COMPLETED"
    assert get_batch_summary(batch.id)["failed"] == 1


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "30"])
def test_invalid_monitoring_intervals_rejected(value, home):
    with pytest.raises(ValueError):
        track_run(project="p", experiment="e", heartbeat_interval_s=value)
    with pytest.raises(ValueError):
        get_run_status("missing", heartbeat_timeout_s=value)
    with pytest.raises(ValueError):
        get_batch_summary("missing", heartbeat_timeout_s=value)
    assert not home.exists()


def test_unknown_ids_are_explicit_errors():
    with pytest.raises(ValueError, match="Unknown run_id"):
        get_run_status("missing")
    with pytest.raises(ValueError, match="Unknown batch_id"):
        get_batch_summary("missing")


def test_exception_stops_heartbeat_preserves_original_and_partial_metrics(query):
    original = RuntimeError("long calculation failed")
    with pytest.raises(RuntimeError) as caught:
        with track_run(project="p", experiment="e", heartbeat_interval_s=0.02) as run:
            run.log_metrics({"partial": [1, 2]})
            initial = get_run_status(run.id)["last_heartbeat_at"]
            wait_until(lambda: get_run_status(run.id)["last_heartbeat_at"] > initial)
            raise original
    assert caught.value is original
    row = query("SELECT * FROM runs")[0]
    assert row["status"] == "FAILED" and row["finished_at"] and row["duration_s"] > 0
    assert json.loads(row["raw_metrics_json"]) == {"partial": [1, 2]}
    assert not any(thread.name == f"cryptolab-heartbeat-{run.id}" for thread in threading.enumerate())
