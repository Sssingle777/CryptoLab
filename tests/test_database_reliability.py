from contextlib import contextmanager
import sqlite3
import threading
import time

import pytest

from cryptolab import get_run_status, track_run
from cryptolab.database import Database


def test_complete_transaction_is_rolled_back_and_replayed_with_fresh_connection(query):
    database = Database(busy_timeout_ms=10, max_retries=2)
    connections = []
    def operation(connection):
        connections.append(connection)
        connection.execute("INSERT INTO projects(id,name,created_at) VALUES (?,?,?)", ("fixed-id", "p", "now"))
        if len(connections) == 1:
            error = sqlite3.OperationalError("snapshot conflict")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
            raise error
    database._write(operation)
    assert len(connections) == 2 and connections[0] is not connections[1]
    assert len(query("SELECT * FROM projects")) == 1
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("code", [sqlite3.SQLITE_ERROR, sqlite3.SQLITE_FULL, sqlite3.SQLITE_READONLY])
def test_non_lock_errors_are_never_retried(code):
    database = Database(max_retries=3)
    attempts = []
    def operation(connection):
        attempts.append(1)
        error = sqlite3.OperationalError("non-lock failure")
        error.sqlite_errorcode = code
        raise error
    with pytest.raises(sqlite3.OperationalError, match="non-lock failure"):
        database._write(operation)
    assert len(attempts) == 1


def hold_write_lock(database, ready, release, errors):
    try:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            ready.set()
            if not release.wait(10):
                raise TimeoutError("test writer was not released")
    except BaseException as error:
        errors.append(error)


def count_connections(database, monkeypatch):
    original = database.connect
    calls = []
    @contextmanager
    def counted(**kwargs):
        calls.append(1)
        with original(**kwargs) as connection:
            yield connection
    monkeypatch.setattr(database, "connect", counted)
    return calls


def test_real_writer_lock_retries_then_succeeds(monkeypatch, query):
    database = Database(busy_timeout_ms=10, max_retries=4)
    locker = Database()
    ready, release = threading.Event(), threading.Event()
    errors = []
    thread = threading.Thread(target=hold_write_lock, args=(locker, ready, release, errors))
    thread.start()
    timer = threading.Timer(0.2, release.set)
    try:
        assert ready.wait(5)
        calls = count_connections(database, monkeypatch)
        timer.start()
        database.create_batch("batch-id", "project", "experiment", "name", {}, "2026-01-01T00:00:00+00:00")
        assert 2 <= len(calls) <= 5
    finally:
        release.set()
        thread.join(timeout=5)
        timer.cancel()
        timer.join()
    assert not errors
    assert len(query("SELECT * FROM batches")) == len(query("SELECT * FROM projects")) == 1
    assert not query("PRAGMA foreign_key_check")


def test_real_writer_lock_exhausts_finite_budget_and_leaves_no_partial_record(monkeypatch, query):
    database = Database(busy_timeout_ms=10, max_retries=2)
    locker = Database()
    ready, release = threading.Event(), threading.Event()
    errors = []
    thread = threading.Thread(target=hold_write_lock, args=(locker, ready, release, errors))
    thread.start()
    try:
        assert ready.wait(5)
        calls = count_connections(database, monkeypatch)
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError) as caught:
            database.create_batch("b", "p", "e", None, {}, "2026-01-01T00:00:00+00:00")
        assert caught.value.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
        assert len(calls) == 3
        assert time.monotonic() - started < 3
        assert not query("SELECT * FROM projects")
    finally:
        release.set()
        thread.join(timeout=5)
    assert not errors
    database.create_batch("b", "p", "e", None, {}, "2026-01-01T00:00:00+00:00")
    assert len(query("SELECT * FROM batches")) == 1


def test_live_readers_and_heartbeat_have_bounded_wait_under_writer_lock(capsys):
    database = Database()
    ready, release = threading.Event(), threading.Event()
    errors = []
    with track_run(project="p", experiment="e", heartbeat_interval_s=0.02) as run:
        thread = threading.Thread(target=hold_write_lock, args=(database, ready, release, errors))
        thread.start()
        try:
            assert ready.wait(5)
            started = time.monotonic()
            assert get_run_status(run.id)["status"] == "RUNNING"
            assert time.monotonic() - started < 1
            with pytest.raises(sqlite3.OperationalError):
                database.heartbeat(run.id)
            assert time.monotonic() - started < 1.5
        finally:
            release.set()
            thread.join(timeout=5)
    assert not errors
    assert get_run_status(run.id)["status"] == "COMPLETED"


def test_sqlite_connections_remain_thread_local():
    database = Database()
    errors = []
    with database.connect() as connection:
        def misuse():
            try:
                connection.execute("SELECT 1")
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=misuse)
        thread.start()
        thread.join(timeout=5)
    assert len(errors) == 1 and isinstance(errors[0], sqlite3.ProgrammingError)
