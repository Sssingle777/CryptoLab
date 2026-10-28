"""Isolated simulated experiments used by real subprocess/spawn tests."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace

from cryptolab import track_run


def wait_at_barrier(barrier):
    barrier.wait(timeout=20)


def record(index, batch_id=None, *, fail=False, capture=False):
    arguments = {"batch_id": batch_id} if batch_id else {"project": "concurrency", "experiment": "standalone"}
    token = f"token-{index:04d}"
    with track_run(**arguments, params={"index": index, "rounds": index + 8}, capture_console=capture) as run:
        run.log(f"{token}|start")
        run.log_params({"actual_seed": index})
        run.log_metrics({"token": token, "score": index * 3})
        if capture:
            print(f"{token}|print")
        if index % 2 == 0:
            # No actual solver or environment is passed between workers.
            run.log_gurobi(SimpleNamespace(Status=9, SolCount=0, Runtime=0.001, IsMIP=1))
        with TemporaryDirectory() as directory:
            source = Path(directory) / "input.txt"
            source.write_text(token, encoding="utf-8")
            run.add_file(source)
        run.output_path("result.txt").write_text(token, encoding="utf-8")
        run.log(f"{token}|step")
        run.log_metrics({"items": [index, index + 1]})
        time.sleep(0.002)
        if fail:
            raise ValueError(f"{token}|expected failure")
        run.log(f"{token}|finish")
        return run.id, os.getpid(), threading.get_ident()


def group(start, count, batch_id=None, *, barrier=None, capture=False):
    if barrier is not None:
        barrier.wait(timeout=20)
    return [record(index, batch_id, capture=capture) for index in range(start, start + count)]


def process_job(index, batch_id, gate, fail=False):
    if not gate.wait(20):
        raise TimeoutError("test gate did not open")
    record(index, batch_id, fail=fail)


def long_running(batch_id, ready):
    with track_run(batch_id=batch_id, params={"index": 999}, heartbeat_interval_s=0.02) as run:
        run.log_metrics({"partial": 42})
        run.log("persist before kill")
        with TemporaryDirectory() as directory:
            source = Path(directory) / "saved.txt"
            source.write_text("committed attachment", encoding="utf-8")
            run.add_file(source)
        ready.send(run.id)
        ready.close()
        while True:
            time.sleep(1)


if __name__ == "__main__":
    start, count = int(sys.argv[1]), int(sys.argv[2])
    gate = Path(sys.argv[3])
    gate.with_name(f"ready-{start}").touch()
    deadline = time.monotonic() + 20
    while not gate.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("test gate did not open")
        time.sleep(0.01)
    group(start, count, capture=True)
