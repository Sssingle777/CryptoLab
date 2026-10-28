from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from cryptolab import get_batch_summary, get_run_status, track_batch, track_run
from cryptolab.database import Database
from tests.workers import group, long_running, process_job, record, wait_at_barrier


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for test condition")


def check_records(query, home, count, *, batch_id=None, capture=False):
    rows = query("SELECT * FROM runs")
    assert len(rows) == len({row["id"] for row in rows}) == count
    assert len({json.loads(row["params_json"])["index"] for row in rows}) == count
    for row in rows:
        params, metrics = json.loads(row["params_json"]), json.loads(row["metrics_json"])
        index = params["index"]
        token = f"token-{index:04d}"
        assert row["batch_id"] == batch_id
        assert params["actual_seed"] == index and params["rounds"] == index + 8
        if batch_id:
            assert params["limit"] == 300
        assert metrics["token"] == token and metrics["score"] == index * 3
        assert metrics["items"] == [index, index + 1]
        assert row["status"] == "COMPLETED"
        assert row["raw_metrics_json"] == row["metrics_json"]
        assert row["raw_params_json"] == row["params_json"]
        assert row["duration_s"] > 0
        assert datetime.fromisoformat(row["finished_at"]) >= datetime.fromisoformat(row["started_at"])
        assert row["last_heartbeat_at"] is not None
        if index % 2 == 0:
            assert row["solver_status"] == "TIME_LIMIT"
            assert metrics["gurobi"]["ObjVal"] is None
        directory = home / "runs" / row["id"]
        lines = (directory / "run.log").read_text().splitlines()
        assert [line.split(" INFO ", 1)[1] for line in lines] == [f"{token}|{part}" for part in ("start", "step", "finish")]
        assert (directory / "input.txt").read_text() == token
        assert (directory / "result.txt").read_text() == token
        if capture:
            assert (directory / "stdout.log").read_text() == f"{token}|print\n"
        else:
            assert not (directory / "stdout.log").exists()
    for artifact in query("SELECT * FROM artifacts"):
        path = home / artifact["relative_path"]
        assert path.stat().st_size == artifact["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    assert query("PRAGMA integrity_check")[0]["integrity_check"] == "ok"
    assert not query("PRAGMA foreign_key_check")
    return rows


def test_four_independent_python_processes_cold_start(tmp_path, home, query):
    gate = tmp_path / "start"
    script = Path(__file__).with_name("workers.py")
    processes = [subprocess.Popen(
        [sys.executable, str(script), str(index * 5), "5", str(gate)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for index in range(4)]
    try:
        wait_until(lambda: len(list(tmp_path.glob("ready-*"))) == 4)
        assert not home.exists()
        gate.touch()
        for process in processes:
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0, output + error
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()
    rows = check_records(query, home, 20, capture=True)
    assert len({row["process_id"] for row in rows}) == 4
    assert len(query("SELECT * FROM projects")) == len(query("SELECT * FROM experiments")) == 1


@pytest.mark.parametrize("count", [40, 80])
def test_process_pool_counts_isolation_and_shared_batch(count, home, query):
    context = multiprocessing.get_context("spawn")
    with track_batch(project="parallel", experiment="processes", common_params={"limit": 300, "index": -1}) as batch:
        with ProcessPoolExecutor(max_workers=4, mp_context=context, initializer=wait_at_barrier,
                                 initargs=(context.Barrier(4),)) as pool:
            futures = [pool.submit(group, start, count // 4, batch.id) for start in range(0, count, count // 4)]
            groups = [future.result(timeout=30) for future in futures]
        assert all(len({result[1] for result in results}) == 1 for results in groups)
        assert len({results[0][1] for results in groups}) == 4
    rows = check_records(query, home, count, batch_id=batch.id)
    assert sorted({row["id"] for row in rows}) == sorted(result[0] for results in groups for result in results)
    summary = get_batch_summary(batch.id)
    assert summary["completed"] == summary["total"] == count
    assert summary["failed"] == summary["running"] == summary["unconfirmed"] == 0


@pytest.mark.parametrize("count", [60, 80])
def test_thread_pool_with_concurrent_reader(count, home, query):
    original_stdout = sys.stdout
    stop_reader = threading.Event()
    reader_errors = []
    observations = []
    with track_batch(project="parallel", experiment="threads", common_params={"limit": 300}) as batch:
        def read_while_running():
            try:
                while not stop_reader.is_set():
                    summary = get_batch_summary(batch.id)
                    assert summary["total"] == sum(summary[key] for key in ("completed", "failed", "interrupted", "running", "unconfirmed"))
                    observations.append(summary["total"])
                    stop_reader.wait(0.003)
            except BaseException as error:
                reader_errors.append(error)
        reader = threading.Thread(target=read_while_running)
        reader.start()
        barrier = threading.Barrier(4)
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(group, start, count // 4, batch.id, barrier=barrier)
                           for start in range(0, count, count // 4)]
                groups = [future.result(timeout=30) for future in futures]
        finally:
            stop_reader.set()
            reader.join(timeout=5)
        assert not reader.is_alive() and not reader_errors
        assert observations and any(0 < value < count for value in observations)
    assert sys.stdout is original_stdout
    assert len({results[0][2] for results in groups}) == 4
    rows = check_records(query, home, count, batch_id=batch.id)
    assert {row["process_id"] for row in rows} == {os.getpid()}


def test_spawn_process_failure_does_not_change_other_records(query, home):
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    with track_batch(project="p", experiment="failure") as batch:
        processes = [context.Process(target=process_job, args=(index, batch.id, gate, index == 1)) for index in range(4)]
        try:
            for process in processes:
                process.start()
            gate.set()
            for process in processes:
                process.join(timeout=30)
            assert [process.exitcode for process in processes] == [0, 1, 0, 0]
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                    process.join()
                process.close()
    summary = get_batch_summary(batch.id)
    assert summary["completed"] == 3 and summary["failed"] == 1 and summary["total"] == 4
    rows = query("SELECT * FROM runs")
    for row in rows:
        index = json.loads(row["params_json"])["index"]
        assert json.loads(row["metrics_json"])["score"] == index * 3
        assert row["finished_at"] and row["duration_s"] > 0
        if index == 1:
            assert row["error_type"] == "ValueError"
            assert "expected failure" in (home / row["traceback_path"]).read_text()
        else:
            assert row["error_type"] is None


def test_killed_process_keeps_commits_and_is_only_unconfirmed(home, query):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    with track_batch(project="p", experiment="kill") as batch:
        sibling = record(0, batch.id)[0]
        before = query("SELECT * FROM runs WHERE id=?", (sibling,))[0]
        process = context.Process(target=long_running, args=(batch.id, child))
        try:
            process.start()
            child.close()
            assert parent.poll(10)
            run_id = parent.recv()
            first = get_run_status(run_id)["last_heartbeat_at"]
            wait_until(lambda: get_run_status(run_id)["last_heartbeat_at"] > first)
            process.kill()
            process.join(timeout=10)
            assert process.exitcode is not None and process.exitcode < 0
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            process.close()
            parent.close()
            child.close()
        wait_until(lambda: get_run_status(run_id, heartbeat_timeout_s=0.05)["display_status"] == "UNCONFIRMED")
        Database()  # Reopening the store must not delete or reclassify anything.
        row = query("SELECT * FROM runs WHERE id=?", (run_id,))[0]
        assert row["status"] == "RUNNING"
        assert row["finished_at"] is None and row["duration_s"] is None
        assert row["raw_metrics_json"] is None
        assert json.loads(row["metrics_json"]) == {"partial": 42}
        assert (home / "runs" / run_id / "run.log").read_text().endswith("persist before kill\n")
        artifact = query("SELECT * FROM artifacts WHERE run_id=?", (run_id,))[0]
        assert (home / artifact["relative_path"]).read_text() == "committed attachment"
        assert query("SELECT * FROM runs WHERE id=?", (sibling,))[0] == before
        summary = get_batch_summary(batch.id, heartbeat_timeout_s=0.05)
        assert summary["unconfirmed"] == 1 and summary["completed"] == 1 and summary["failed"] == 0
        record(2, batch.id)
    assert get_batch_summary(batch.id, heartbeat_timeout_s=0.05)["total"] == 3
    assert query("PRAGMA integrity_check")[0]["integrity_check"] == "ok"


def test_run_and_batch_objects_cannot_be_pickled_or_used_by_another_process(monkeypatch):
    with track_batch(project="p", experiment="e") as batch:
        with track_run(batch_id=batch.id) as run:
            for value in (run, batch, run._database):
                with pytest.raises(TypeError, match="process|batch.id"):
                    pickle.dumps(value)
            with monkeypatch.context() as patch:
                patch.setattr(run, "_owner_pid", -1)
                with pytest.raises(RuntimeError, match="across processes"):
                    run.log("forbidden")
            with monkeypatch.context() as patch:
                patch.setattr(run._database, "_owner_pid", -1)
                with pytest.raises(RuntimeError, match="across processes"):
                    with run._database.connect():
                        pass


def test_same_run_explicit_thread_updates_do_not_lose_keys(query):
    with track_run(project="p", experiment="shared-explicit") as run:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: run.log_metrics({f"key-{i}": i}), range(40)))
    assert json.loads(query("SELECT metrics_json FROM runs")[0]["metrics_json"]) == {f"key-{i}": i for i in range(40)}


def test_capture_rejected_in_another_thread_without_changing_stdout():
    original = sys.stdout
    with track_run(project="p", experiment="e"):
        def capture():
            with track_run(project="p", experiment="e", capture_console=True):
                pass
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(RuntimeError, match="run.log"):
                pool.submit(capture).result(timeout=10)
    assert sys.stdout is original
