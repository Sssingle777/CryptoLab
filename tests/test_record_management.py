from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
import socket
import sqlite3
import time

import pytest

from cryptolab import (confirm_run_stopped, create_manual_run, get_batch_summary,
                       restore_run, soft_delete_run, track_batch, track_run, update_run)
from cryptolab.database import Database
from cryptolab.records import (get_run, list_batches, list_experiments, list_projects,
                               list_runs, save_experiment, save_project)
from tests.workers import long_running


def stale_row(run_id, *, local=False):
    with Database().connect() as connection:
        connection.execute("""UPDATE runs SET status='RUNNING',last_heartbeat_at=?,
            finished_at=NULL,duration_s=NULL,raw_params_json=NULL,raw_metrics_json=NULL,
            hostname=?,process_id=? WHERE id=?""",
            ((datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat(),
             socket.gethostname() if local else "archived-host", os.getpid(), run_id))
    return get_run(run_id)


def confirm(row, **kwargs):
    return confirm_run_stopped(row["id"], expected_heartbeat_at=row["last_heartbeat_at"],
                               expected_updated_at=row["updated_at"], confirmed_stopped=True,
                               reason="已核实进程停止", **kwargs)


def test_manual_run_does_not_invent_execution_facts(home):
    run_id = create_manual_run(project="p", experiment="e", params={"rounds": 8}, metrics={"sat": True}, notes="只知道实验日期：2025-01-01")
    row = get_run(run_id)
    assert row["source"] == "manual" and row["status"] == "COMPLETED"
    for field in ("started_at", "finished_at", "duration_s", "last_heartbeat_at", "process_id", "thread_id", "hostname", "raw_params_json", "raw_metrics_json"):
        assert row[field] is None
    assert row["created_at"] == row["updated_at"]
    assert (home / "runs" / run_id).is_dir()
    assert row["changes"][0]["field_name"] == "created"


def test_manual_times_batch_merge_and_repeat_ids():
    with track_batch(project="p", experiment="e", common_params={"rounds": 1, "limit": 300}) as batch:
        pass
    arguments = dict(batch_id=batch.id, params={"rounds": 8}, started_at="2025-01-01T08:00:00+08:00",
                     finished_at="2025-01-01T08:00:05+08:00", solver_name="Gurobi", solver_status="TIME_LIMIT", solver_runtime_s=3)
    first, second = create_manual_run(**arguments), create_manual_run(**arguments)
    assert first != second
    row = get_run(first)
    assert row["params"] == {"rounds": 8, "limit": 300}
    assert row["started_at"] == "2025-01-01T00:00:00.000000+00:00"
    assert row["duration_s"] == 5 and row["solver_runtime_s"] == 3
    assert get_batch_summary(batch.id)["completed"] == 2


@pytest.mark.parametrize("arguments", [
    {"status": "RUNNING"}, {"metrics": {"x": float("nan")}}, {"duration_s": -1},
    {"started_at": "2025-01-01"}, {"started_at": "2025-01-02T00:00:00+00:00", "finished_at": "2025-01-01T00:00:00+00:00"},
    {"notes": 3}, {"solver_runtime_s": float("inf")}, {"reason": " "},
])
def test_invalid_manual_input_has_no_record(arguments, home):
    with pytest.raises((ValueError, TypeError)):
        create_manual_run(project="p", experiment="e", **arguments)
    assert not home.exists()


def test_manual_creation_failure_rolls_back_and_cleans_empty_directory(home, query):
    with pytest.raises(ValueError, match="Unknown batch_id"):
        create_manual_run(batch_id="missing")
    assert not query("SELECT * FROM runs")
    assert not list((home / "runs").iterdir())


def test_soft_delete_restore_audit_and_files_preserved(home):
    with track_run(project="p", experiment="e") as run:
        run.output_path("result.txt").write_text("keep me")
    original = get_run(run.id)
    soft_delete_run(run.id, reason="误录", expected_updated_at=original["updated_at"])
    deleted = get_run(run.id)
    assert deleted["deleted_at"] and not list_runs()
    assert list_runs(deleted="only")[0]["id"] == run.id
    assert (home / "runs" / run.id / "result.txt").read_text() == "keep me"
    with pytest.raises(ValueError, match="Restore"):
        update_run(run.id, {"notes": "invalid"}, reason="test")
    restore_run(run.id, reason="恢复误删", expected_updated_at=deleted["updated_at"])
    restored = get_run(run.id)
    assert restored["deleted_at"] is None and len(list_runs()) == 1
    assert restored["raw_metrics_json"] == original["raw_metrics_json"]
    assert [change["field_name"] for change in restored["changes"]] == ["deleted_at", "deleted_at"]


def test_active_run_cannot_be_deleted():
    with track_run(project="p", experiment="e") as run:
        with pytest.raises(ValueError, match="RUNNING"):
            soft_delete_run(run.id, reason="unsafe")


def test_manual_confirmation_is_audited_and_does_not_invent_finish_time():
    with track_run(project="p", experiment="e", params={"rounds": 8}) as run:
        run.log_metrics({"partial": 42})
    row = stale_row(run.id)
    confirm(row)
    result = get_run(run.id)
    assert result["status"] == "INTERRUPTED"
    assert result["finished_at"] is result["duration_s"] is None
    assert json.loads(result["raw_metrics_json"]) == {"partial": 42}
    assert "confirmed_stopped" in {item["field_name"] for item in result["changes"]}
    assert not Database().heartbeat(run.id)
    with pytest.raises(RuntimeError, match="not active"):
        Database().merge_values(run.id, "metrics_json", {"overwrite": True})


def test_live_local_pid_refuses_confirmation_even_with_old_heartbeat():
    run_id = create_manual_run(project="p", experiment="e")
    row = stale_row(run_id, local=True)
    with pytest.raises(ValueError, match="still alive"):
        confirm(row)
    assert get_run(run_id)["status"] == "RUNNING"


def test_confirmation_preserves_known_finish_time_and_rejects_future_time():
    run_id = create_manual_run(project="p", experiment="e", started_at="2025-01-01T00:00:00+00:00")
    row = stale_row(run_id)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        confirm(row, finished_at=future)
    assert get_run(run_id)["status"] == "RUNNING"
    confirm(row, finished_at="2025-01-01T08:00:10+08:00", status="FAILED")
    result = get_run(run_id)
    assert result["finished_at"] == "2025-01-01T00:00:10.000000+00:00"
    assert result["duration_s"] == 10 and result["status"] == "FAILED"
    assert "finished_at" in {item["field_name"] for item in result["changes"]}


def test_confirmation_rejects_missing_ack_new_heartbeat_new_edit_and_nonstale():
    run_id = create_manual_run(project="p", experiment="e")
    row = stale_row(run_id)
    with pytest.raises(ValueError, match="Explicit"):
        confirm_run_stopped(run_id, expected_heartbeat_at=row["last_heartbeat_at"], expected_updated_at=row["updated_at"],
                            confirmed_stopped=False, reason="test")
    Database().heartbeat(run_id)
    with pytest.raises(ValueError, match="Heartbeat changed"):
        confirm(row)
    with pytest.raises(ValueError, match="stale"):
        confirm(get_run(run_id))
    row = stale_row(run_id)
    Database().merge_values(run_id, "metrics_json", {"resumed": True})
    with pytest.raises(ValueError, match="Record changed"):
        confirm(row)


def test_killed_spawn_run_can_be_safely_confirmed():
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    with track_batch(project="p", experiment="kill confirm") as batch:
        process = context.Process(target=long_running, args=(batch.id, child))
        try:
            process.start()
            child.close()
            assert parent.poll(10)
            run_id = parent.recv()
            process.kill()
            process.join(10)
            assert process.exitcode is not None
            time.sleep(0.05)
            confirm(get_run(run_id), heartbeat_timeout_s=0.02)
            assert get_batch_summary(batch.id)["interrupted"] == 1
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            process.close()
            parent.close()
            child.close()


def test_audit_failure_rolls_back_delete_and_confirmation(query):
    run_id = create_manual_run(project="p", experiment="e")
    with Database().connect() as connection:
        connection.execute("CREATE TRIGGER fail_changes BEFORE INSERT ON run_changes BEGIN SELECT RAISE(ABORT,'blocked'); END")
    with pytest.raises(sqlite3.IntegrityError):
        soft_delete_run(run_id, reason="test")
    assert get_run(run_id)["deleted_at"] is None
    row = stale_row(run_id)
    with pytest.raises(sqlite3.IntegrityError):
        confirm(row)
    assert get_run(run_id)["status"] == "RUNNING"


def test_projects_rename_and_experiment_tags_keep_links():
    project = save_project("Project", description="description")
    experiment = save_experiment(project, "Plan", method="MILP", cipher_name="PRESENT")
    run_id = create_manual_run(project="Project", experiment="Plan")
    save_project("Renamed", project_id=project)
    save_experiment(project, "New plan", experiment_id=experiment, method="SAT", cipher_name="SKINNY")
    row = get_run(run_id)
    assert row["project_name"] == "Renamed" and row["experiment_name"] == "New plan"
    assert row["cipher_name"] == "SKINNY" and row["method"] == "SAT"
    assert len(list_projects()) == len(list_experiments(project)) == 1
    assert not list_batches(experiment)
    with pytest.raises(sqlite3.IntegrityError):
        save_project("Renamed")


def test_combined_filters_and_literal_keywords():
    p = save_project("P%_special")
    e = save_experiment(p, "E", cipher_name="PRESENT")
    first = create_manual_run(project="P%_special", experiment="E", params={"rounds": 8},
                              solver_status="TIME_LIMIT", started_at="2025-01-01T16:30:00+00:00")
    second = create_manual_run(project="P%_special", experiment="E", params={"rounds": 9}, status="FAILED")
    create_manual_run(project="Other", experiment="E", params={"rounds": 8})
    rows = list_runs(project_id=p, experiment_id=e, rounds=8, solver_status="TIME_LIMIT", source="manual",
                     cipher_name="PRESENT", keyword="%_", status="COMPLETED",
                     started_from="2025-01-02T00:00:00+08:00", started_before="2025-01-03T00:00:00+08:00")
    assert [row["id"] for row in rows] == [first]
    assert [row["id"] for row in list_runs(status="FAILED")] == [second]
    stale_row(second)
    assert [row["id"] for row in list_runs(status="UNCONFIRMED")] == [second]
    assert not list_runs(status="RUNNING")


def test_editor_optimistic_version_preserves_another_change():
    run_id = create_manual_run(project="p", experiment="e")
    before = get_run(run_id)
    update_run(run_id, {"metrics": {"version": 1}}, reason="first")
    with pytest.raises(ValueError, match="Record changed"):
        update_run(run_id, {"metrics": {"version": 0}}, reason="stale page", expected_updated_at=before["updated_at"])
    assert get_run(run_id)["metrics"] == {"version": 1}
