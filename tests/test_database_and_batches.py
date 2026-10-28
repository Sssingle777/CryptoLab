import json
import sqlite3

import pytest

from cryptolab import track_batch, track_run
from cryptolab.database import Database, data_home


def test_schema_pragmas_indexes_and_persistence(query):
    database = Database()
    with database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"projects", "experiments", "batches", "runs", "artifacts", "run_changes"}
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"runs_experiment_started", "runs_batch_started", "runs_status_started",
                "artifacts_run", "run_changes_run"} <= indexes
    with track_run(project="p", experiment="e"):
        pass
    Database()
    assert len(query("SELECT * FROM runs")) == 1


def test_newer_schema_is_rejected_without_recreating_database():
    database = Database()
    with database.connect() as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer"):
        Database()
    with database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_home_override_and_default_do_not_create_default_directory(tmp_path, monkeypatch, home):
    assert data_home() == home
    monkeypatch.delenv("CRYPTOLAB_HOME")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "fake-user")
    assert data_home() == tmp_path / "fake-user" / ".cryptolab"
    assert not data_home().exists()


def test_batch_five_runs_complete_merged_snapshots(query):
    common = {"time_limit": 300, "rounds": 1, "config": {"old": True}}
    context = track_batch(project="p", experiment="e", name="8-12", common_params=common)
    common["config"]["old"] = False
    with context as batch:
        assert query("SELECT status FROM batches")[0]["status"] == "RUNNING"
        for rounds in range(8, 13):
            with track_run(batch_id=batch.id, params={"rounds": rounds, "config": {"new": True}}) as run:
                run.log_metrics({"sat": True, "weight": rounds * 2})
    rows = query("SELECT * FROM runs ORDER BY started_at")
    assert len(rows) == 5
    assert len({row["id"] for row in rows}) == 5
    assert {row["batch_id"] for row in rows} == {batch.id}
    assert {row["experiment_id"] for row in rows} == {query("SELECT experiment_id FROM batches")[0]["experiment_id"]}
    for row, rounds in zip(rows, range(8, 13)):
        assert json.loads(row["params_json"]) == {"time_limit": 300, "rounds": rounds, "config": {"new": True}}
        assert row["raw_params_json"] == row["params_json"]
        assert row["status"] == "COMPLETED"
    batch_row = query("SELECT * FROM batches")[0]
    assert json.loads(batch_row["common_params_json"])["config"] == {"old": True}
    assert batch_row["status"] == "COMPLETED" and batch_row["finished_at"]
    with pytest.raises(RuntimeError, match="reused"):
        with batch:
            pass


@pytest.mark.parametrize("arguments", [{"project": "wrong"}, {"experiment": "wrong"}])
def test_batch_explicit_names_must_match(arguments, query):
    with track_batch(project="p", experiment="e") as batch:
        with pytest.raises(ValueError, match="does not match"):
            with track_run(batch_id=batch.id, **arguments):
                pass
    assert not query("SELECT * FROM runs")


def test_unknown_batch_and_missing_names(query):
    for arguments in ({"batch_id": "missing"}, {}, {"project": "p"}, {"project": "", "experiment": "e"}):
        with pytest.raises(ValueError):
            with track_run(**arguments):
                pass
    assert not query("SELECT * FROM runs")
    assert not query("SELECT * FROM projects")


def test_batch_exception_does_not_rollback_finished_children(query):
    original = RuntimeError("batch failed")
    with pytest.raises(RuntimeError) as caught:
        with track_batch(project="p", experiment="e") as batch:
            with track_run(batch_id=batch.id):
                pass
            raise original
    assert caught.value is original
    assert query("SELECT status FROM batches")[0]["status"] == "FAILED"
    assert query("SELECT status FROM runs")[0]["status"] == "COMPLETED"


def test_batch_status_describes_scope_not_caught_child_failure(query):
    with track_batch(project="p", experiment="e") as batch:
        with pytest.raises(ValueError):
            with track_run(batch_id=batch.id):
                raise ValueError("child error")
    assert query("SELECT status FROM batches")[0]["status"] == "COMPLETED"
    assert query("SELECT status FROM runs")[0]["status"] == "FAILED"


def test_batch_database_failure_preserves_original(monkeypatch, capsys):
    original = ValueError("user failure")
    def fail(*args):
        raise OSError("batch finalization failed")
    with pytest.raises(ValueError) as caught:
        with track_batch(project="p", experiment="e"):
            monkeypatch.setattr(Database, "finish_batch", fail)
            raise original
    assert caught.value is original
    assert "batch finalization failed" in capsys.readouterr().err


def test_database_foreign_keys_enforce_batch_experiment_match(query):
    with track_batch(project="p", experiment="first") as batch:
        pass
    with track_run(project="p", experiment="second") as run:
        pass
    with pytest.raises(sqlite3.IntegrityError):
        with Database().connect() as connection:
            connection.execute("UPDATE runs SET batch_id=? WHERE id=?", (batch.id, run.id))
    assert query("SELECT batch_id FROM runs")[0]["batch_id"] is None


def test_no_transaction_is_held_during_user_code():
    with track_run(project="p", experiment="e"):
        with Database().connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE projects SET description=?", ("independent writer",))


def test_json_extract_numbers_strings_booleans_and_sql_values(query):
    unusual_name = "PRESENT'); DROP TABLE projects; --"
    with track_run(project=unusual_name, experiment="e", params={"rounds": 12, "method": "SAT"}) as run:
        run.log_metrics({"satisfiable": True})
    rows = query(
        """SELECT id FROM runs WHERE json_extract(params_json,'$.rounds') >= ?
           AND json_extract(params_json,'$.method') = ?
           AND json_extract(metrics_json,'$.satisfiable') = ?""", (8, "SAT", True),
    )
    assert rows == [{"id": run.id}]
    assert query("SELECT name FROM projects")[0]["name"] == unusual_name
