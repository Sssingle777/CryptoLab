from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cryptolab import track_run
from cryptolab.database import Database


def test_complete_run_live_values_and_frozen_snapshot(home, query):
    before = datetime.now(timezone.utc)
    params = {"rounds": 12, "options": {"seed": 1}}
    context = track_run(project="PRESENT", experiment="SAT", params=params)
    params["options"]["seed"] = 99
    assert not home.exists()
    with context as run:
        active = query("SELECT * FROM runs WHERE id=?", (run.id,))[0]
        assert active["status"] == "RUNNING"
        assert active["finished_at"] is None
        assert active["raw_params_json"] is None
        assert json.loads(active["params_json"])["options"]["seed"] == 1
        run.log_params({"seed": 42})
        run.log_metrics({"sat": True, "score": 0.25, "trail": [1, None, {"name": "示例"}]})
        run.log_metrics({"score": 0.5})
        assert json.loads(query("SELECT metrics_json FROM runs")[0]["metrics_json"])["score"] == 0.5
    row = query("SELECT * FROM runs")[0]
    assert row["status"] == "COMPLETED"
    assert row["source"] == "auto"
    assert before <= datetime.fromisoformat(row["started_at"]) <= datetime.fromisoformat(row["finished_at"])
    for field in ("started_at", "finished_at", "created_at", "updated_at"):
        assert row[field].endswith("+00:00")
    assert row["duration_s"] >= 0
    assert row["raw_params_json"] == row["params_json"]
    assert row["raw_metrics_json"] == row["metrics_json"]
    assert row["process_id"] == os.getpid()
    assert row["thread_id"] is not None and row["hostname"]
    assert row["command_json"] is None
    assert datetime.fromisoformat(row["last_heartbeat_at"]) >= datetime.fromisoformat(row["started_at"])


def test_repeated_parameters_create_distinct_runs_and_reuse_names(query):
    ids = []
    for _ in range(2):
        with track_run(project="PRESENT", experiment="MILP", params={"rounds": 8}) as run:
            ids.append(run.id)
    assert len(set(ids)) == 2
    assert len(query("SELECT * FROM runs")) == 2
    assert len(query("SELECT * FROM projects")) == len(query("SELECT * FROM experiments")) == 1


@pytest.mark.parametrize("error, status", [
    (ValueError("原始错误"), "FAILED"), (KeyboardInterrupt(), "INTERRUPTED"),
    (SystemExit(0), "INTERRUPTED"),
])
def test_original_exception_and_traceback(error, status, home, query):
    with pytest.raises(type(error)) as caught:
        with track_run(project="p", experiment="e") as run:
            run.log_metrics({"partial": 1})
            raise error
    assert caught.value is error
    row = query("SELECT * FROM runs")[0]
    assert row["status"] == status
    assert row["error_type"] == type(error).__name__
    assert row["error_message"] == str(error)
    assert row["raw_metrics_json"] == row["metrics_json"]
    trace = (home / row["traceback_path"]).read_text()
    assert type(error).__name__ in trace
    assert "raise error" in trace
    assert query("SELECT * FROM artifacts WHERE relative_path=?", (row["traceback_path"],))


def test_elapsed_time_uses_monotonic_clock(monkeypatch, query):
    values = iter([100.0, 107.25])
    monkeypatch.setattr("cryptolab.recorder.time.perf_counter", lambda: next(values))
    with track_run(project="p", experiment="e"):
        pass
    assert query("SELECT duration_s FROM runs")[0]["duration_s"] == 7.25


@pytest.mark.parametrize("bad", [
    {"x": {1, 2}}, {"x": Path("file")}, {"x": float("nan")},
    {"x": float("inf")}, {1: "bad key"}, {"x": {2: "nested key"}},
    {"x": (1, 2)}, [1, 2],
])
def test_bad_initial_json_is_rejected_without_creating_database(bad, home):
    with pytest.raises((TypeError, ValueError)):
        track_run(project="p", experiment="e", params=bad)
    assert not home.exists()


def test_bad_metrics_do_not_overwrite_valid_data(query):
    with pytest.raises(TypeError):
        with track_run(project="p", experiment="e") as run:
            run.log_metrics({"score": 1})
            run.log_metrics({"score": object()})
    row = query("SELECT * FROM runs")[0]
    assert json.loads(row["metrics_json"]) == {"score": 1}
    assert row["status"] == "FAILED"


def test_nested_metrics_are_replaced_and_input_is_not_aliased(query):
    with track_run(project="p", experiment="e") as run:
        metrics = {"nested": {"a": 1, "b": 2}}
        run.log_metrics(metrics)
        metrics["nested"]["a"] = 99
        assert json.loads(query("SELECT metrics_json FROM runs")[0]["metrics_json"])["nested"]["a"] == 1
        run.log_metrics({"nested": {"c": 3}})
    assert json.loads(query("SELECT metrics_json FROM runs")[0]["metrics_json"]) == {"nested": {"c": 3}}


def test_methods_only_inside_context_and_no_reentry():
    run = track_run(project="p", experiment="e")
    with pytest.raises(RuntimeError, match="with block"):
        run.log_metrics({})
    with run:
        run.log_params({"x": 1})
    for operation in (lambda: run.log_params({}), lambda: run.log_metrics({}),
                      lambda: run.output_path("a"), lambda: run.add_file("a"),
                      lambda: run.log("a"), lambda: run.log_gurobi(object())):
        with pytest.raises(RuntimeError, match="with block"):
            operation()
    with pytest.raises(RuntimeError, match="reused"):
        with run:
            pass


def test_recording_failure_does_not_mask_experiment_exception(monkeypatch, capsys, query):
    original = ValueError("experiment failed")
    def fail(*args, **kwargs):
        raise OSError("database unavailable")
    with pytest.raises(ValueError) as caught:
        with track_run(project="p", experiment="e"):
            monkeypatch.setattr(Database, "finish_run", fail)
            raise original
    assert caught.value is original
    assert "database unavailable" in capsys.readouterr().err
    assert query("SELECT status FROM runs")[0]["status"] == "RUNNING"


def test_finalization_error_is_visible_and_marks_failure(monkeypatch, capsys, query):
    with pytest.raises(OSError, match="metadata failed"):
        with track_run(project="p", experiment="e"):
            def fail(*args):
                raise OSError("metadata failed")
            monkeypatch.setattr(Database, "save_artifact", fail)
    assert query("SELECT status FROM runs")[0]["status"] == "FAILED"
    assert "metadata failed" in capsys.readouterr().err


def test_caught_recording_failure_is_not_reported_as_success(query):
    with pytest.raises(TypeError):
        with track_run(project="p", experiment="e") as run:
            try:
                run.log_metrics({"bad": object()})
            except TypeError:
                pass
    assert query("SELECT status FROM runs")[0]["status"] == "FAILED"


def test_core_import_and_run_without_gurobi(home):
    code = """
import sys
class BlockGurobi:
    def find_spec(self, fullname, *args):
        if fullname.startswith('gurobipy'):
            raise AssertionError('core tried to import Gurobi')
sys.meta_path.insert(0, BlockGurobi())
from cryptolab import track_run
with track_run(project='plain', experiment='python') as run:
    run.log_metrics({'ok': True})
assert 'gurobipy' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (home / "cryptolab.db").exists()
