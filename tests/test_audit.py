from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from cryptolab import track_run, update_run
from cryptolab.database import Database


def test_manual_corrections_are_audited_and_raw_snapshots_remain_frozen(query):
    with track_run(project="p", experiment="e", params={"rounds": 8}) as run:
        run.log_metrics({"score": 1})
    original = query("SELECT * FROM runs")[0]
    update_run(run.id, {"params": {"rounds": 9}, "metrics": {"score": 2}, "notes": "校正",
                        "started_at": "2026-01-01T08:00:00+08:00", "finished_at": "2026-01-01T08:00:03+08:00"},
               reason="核对原始实验记录")
    row = query("SELECT * FROM runs")[0]
    assert row["raw_params_json"] == original["raw_params_json"]
    assert row["raw_metrics_json"] == original["raw_metrics_json"]
    assert json.loads(row["params_json"]) == {"rounds": 9}
    assert row["started_at"] == "2026-01-01T00:00:00.000000+00:00"
    assert row["duration_s"] == 3
    changes = query("SELECT * FROM run_changes ORDER BY rowid")
    assert {change["field_name"] for change in changes} == {
        "params_json", "metrics_json", "notes", "started_at", "finished_at", "duration_s",
    }
    assert all(change["actor"] == "local_user" and change["reason"] == "核对原始实验记录" for change in changes)
    param_change = next(change for change in changes if change["field_name"] == "params_json")
    assert json.loads(param_change["old_value_json"]) == {"rounds": 8}
    assert json.loads(param_change["new_value_json"]) == {"rounds": 9}
    update_run(run.id, {"finished_at": None}, reason="结束时间未知")
    assert query("SELECT duration_s FROM runs")[0]["duration_s"] is None


def test_audit_failure_rolls_back_record_change(query):
    with track_run(project="p", experiment="e") as run:
        run.log_metrics({"score": 1})
    original = query("SELECT * FROM runs")[0]
    with Database().connect() as connection:
        connection.execute("""CREATE TRIGGER reject_audit BEFORE INSERT ON run_changes
                              BEGIN SELECT RAISE(ABORT, 'audit blocked'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="audit blocked"):
        update_run(run.id, {"metrics": {"score": 2}}, reason="correction")
    assert query("SELECT * FROM runs")[0] == original
    assert not query("SELECT * FROM run_changes")


@pytest.mark.parametrize("edits,expected_duration,expected_fields", [
    ({"started_at": "2025-01-01T00:00:02+00:00"}, 8, {"started_at", "duration_s"}),
    ({"finished_at": "2025-01-01T08:00:20+08:00"}, 20, {"finished_at", "duration_s"}),
    ({"started_at": None}, None, {"started_at", "duration_s"}),
    ({"finished_at": None}, None, {"finished_at", "duration_s"}),
    ({"finished_at": "2025-01-01T00:00:20+00:00", "duration_s": 7.5}, 7.5, {"finished_at", "duration_s"}),
    ({"finished_at": "2025-01-01T00:00:20+00:00", "duration_s": 0}, 0, {"finished_at", "duration_s"}),
    ({"finished_at": "2025-01-01T00:00:20+00:00", "duration_s": None}, None, {"finished_at", "duration_s"}),
    ({"finished_at": None, "duration_s": 7.5}, 7.5, {"finished_at", "duration_s"}),
    ({"notes": "只修改备注"}, 12.5, {"notes"}),
])
def test_time_corrections_keep_duration_audit_and_raw_snapshots(edits, expected_duration, expected_fields, query):
    with track_run(project="p", experiment="e", params={"rounds": 8}) as run:
        run.log_metrics({"weight": 16})
    update_run(run.id, {"started_at": "2025-01-01T00:00:00+00:00",
                        "finished_at": "2025-01-01T00:00:10+00:00", "duration_s": 12.5}, reason="原始时间")
    before = query("SELECT * FROM runs")[0]
    update_run(run.id, edits, reason="时间复核", expected_updated_at=before["updated_at"])
    after = query("SELECT * FROM runs")[0]
    assert after["duration_s"] == expected_duration
    assert after["raw_params_json"] == before["raw_params_json"]
    assert after["raw_metrics_json"] == before["raw_metrics_json"]
    changes = query("SELECT * FROM run_changes WHERE reason=?", ("时间复核",))
    assert {change["field_name"] for change in changes} == expected_fields
    for change in changes:
        field = change["field_name"]
        assert json.loads(change["old_value_json"]) == before[field]
        assert json.loads(change["new_value_json"]) == after[field]
        assert change["actor"] == "local_user"


def test_duration_audit_failure_rolls_back_time_and_derived_duration(query):
    with track_run(project="p", experiment="e") as run:
        pass
    before = query("SELECT * FROM runs")[0]
    with Database().connect() as connection:
        connection.execute("""CREATE TRIGGER reject_duration_audit BEFORE INSERT ON run_changes
                              WHEN NEW.field_name='duration_s'
                              BEGIN SELECT RAISE(ABORT, 'duration audit blocked'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="duration audit blocked"):
        update_run(run.id, {"started_at": "2025-01-01T00:00:00+00:00",
                            "finished_at": "2025-01-01T00:00:10+00:00"}, reason="时间复核")
    assert query("SELECT * FROM runs")[0] == before
    assert not query("SELECT * FROM run_changes")


def test_concurrent_corrections_keep_a_continuous_audit_chain(query):
    with track_run(project="p", experiment="e") as run:
        run.log_metrics({"value": -1})
    def correct(index):
        update_run(run.id, {"metrics": {"value": index}}, reason=f"correction {index}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(correct, range(20)))
    changes = query("SELECT * FROM run_changes ORDER BY rowid")
    previous = {"value": -1}
    for change in changes:
        assert json.loads(change["old_value_json"]) == previous
        previous = json.loads(change["new_value_json"])
    assert len(changes) == 20
    assert json.loads(query("SELECT metrics_json FROM runs")[0]["metrics_json"]) == previous
    assert json.loads(query("SELECT raw_metrics_json FROM runs")[0]["raw_metrics_json"]) == {"value": -1}


@pytest.mark.parametrize("changes", [
    {"raw_metrics_json": "{}"}, {"status": "FAILED"}, {"params": None},
    {"duration_s": -1}, {"duration_s": float("nan")}, {"duration_s": True},
    {"started_at": "2026-01-01T08:00:00"}, {"notes": 42},
    {"started_at": "2026-02-01T00:00:00+00:00", "finished_at": "2026-01-01T00:00:00+00:00"},
])
def test_invalid_manual_edits_do_not_modify_record(changes, query):
    with track_run(project="p", experiment="e") as run:
        pass
    original = query("SELECT * FROM runs")[0]
    with pytest.raises((ValueError, TypeError)):
        update_run(run.id, changes, reason="test")
    assert query("SELECT * FROM runs")[0] == original
    assert not query("SELECT * FROM run_changes")


def test_live_or_unconfirmed_runs_cannot_be_manually_changed():
    with track_run(project="p", experiment="e", heartbeat_interval_s=60) as run:
        with pytest.raises(ValueError, match="RUNNING"):
            update_run(run.id, {"metrics": {}}, reason="not allowed")
        with Database().connect() as connection:
            connection.execute("UPDATE runs SET last_heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run.id,))
        with pytest.raises(ValueError, match="RUNNING"):
            update_run(run.id, {"notes": "not allowed"}, reason="not allowed")


def test_no_op_has_no_audit_but_bool_and_number_differ(query):
    with track_run(project="p", experiment="e") as run:
        run.log_metrics({"value": 1})
    update_run(run.id, {"metrics": {"value": 1}}, reason="same")
    assert not query("SELECT * FROM run_changes")
    update_run(run.id, {"metrics": {"value": True}}, reason="different JSON type")
    assert len(query("SELECT * FROM run_changes")) == 1
    assert json.loads(query("SELECT metrics_json FROM runs")[0]["metrics_json"])["value"] is True


def test_reason_is_required_and_unknown_run_rejected():
    with pytest.raises(ValueError, match="reason"):
        update_run("missing", {"notes": "x"}, reason=" ")
    with pytest.raises(ValueError, match="Unknown run_id"):
        update_run("missing", {"notes": "x"}, reason="test")
