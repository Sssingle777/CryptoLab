"""SQLite storage. Every operation owns a connection and a short transaction."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import time
from typing import Any, Callable, Iterator, TypeVar
from uuid import uuid4


SCHEMA_VERSION = 1
T = TypeVar("T")

# Phase 2 uses the existing heartbeat/audit columns; no schema migration is needed.
SCHEMA = (
    """CREATE TABLE projects (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        description TEXT, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE experiments (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        name TEXT NOT NULL, method TEXT, cipher_name TEXT, description TEXT,
        created_at TEXT NOT NULL, UNIQUE(project_id, name)
    )""",
    """CREATE TABLE batches (
        id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
        name TEXT, common_params_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','FAILED','INTERRUPTED')),
        started_at TEXT, finished_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(id, experiment_id)
    )""",
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
        batch_id TEXT REFERENCES batches(id),
        params_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
        notes TEXT, source TEXT NOT NULL CHECK(source IN ('auto','manual')),
        raw_params_json TEXT, raw_metrics_json TEXT,
        status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','FAILED','INTERRUPTED')),
        solver_name TEXT, solver_version TEXT, solver_status TEXT,
        started_at TEXT, finished_at TEXT, duration_s REAL, solver_runtime_s REAL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_heartbeat_at TEXT,
        hostname TEXT, process_id INTEGER, thread_id INTEGER,
        script_path TEXT, git_commit TEXT, command_json TEXT, working_directory TEXT,
        error_type TEXT, error_message TEXT, traceback_path TEXT, deleted_at TEXT,
        FOREIGN KEY(batch_id, experiment_id) REFERENCES batches(id, experiment_id)
    )""",
    """CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
        kind TEXT, original_name TEXT, relative_path TEXT NOT NULL UNIQUE,
        size_bytes INTEGER, sha256 TEXT, created_at TEXT
    )""",
    """CREATE TABLE run_changes (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
        changed_at TEXT NOT NULL, field_name TEXT NOT NULL,
        old_value_json TEXT, new_value_json TEXT, reason TEXT,
        actor TEXT DEFAULT 'local_user'
    )""",
    "CREATE INDEX runs_experiment_started ON runs(experiment_id, started_at)",
    "CREATE INDEX runs_batch_started ON runs(batch_id, started_at)",
    "CREATE INDEX runs_status_started ON runs(status, started_at)",
    "CREATE INDEX artifacts_run ON artifacts(run_id)",
    "CREATE INDEX run_changes_run ON run_changes(run_id)",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def encode_dict(value: dict[str, Any]) -> str:
    """Accept JSON objects without silently converting keys or nonfinite floats."""
    if not isinstance(value, dict):
        raise TypeError("params/metrics must be a dict with string keys")
    # This also rejects cycles, NaN, infinity and unsupported object types.
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)

    def check(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise TypeError("Only JSON scalars, lists and dictionaries are supported")

    check(value)
    return encoded


def snapshot(value: dict[str, Any] | None) -> dict[str, Any]:
    return json.loads(encode_dict({} if value is None else value))


def data_home() -> Path:
    configured = os.environ.get("CRYPTOLAB_HOME")
    return (Path(configured).expanduser() if configured else Path.home() / ".cryptolab").resolve()


class Database:
    def __init__(self, *, busy_timeout_ms: int = 5000, max_retries: int = 3) -> None:
        if type(busy_timeout_ms) is not int or busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be a nonnegative integer")
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be an integer from 0 to 10")
        self._owner_pid = os.getpid()
        self.busy_timeout_ms = busy_timeout_ms
        self.max_retries = max_retries
        self.home = data_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "cryptolab.db"
        self._retry(self._initialize)

    def __getstate__(self) -> None:
        raise TypeError("Create Database in each process; do not pass database objects or connections")

    @contextmanager
    def connect(self, *, busy_timeout_ms: int | None = None) -> Iterator[sqlite3.Connection]:
        if self._owner_pid != os.getpid():
            raise RuntimeError("Database cannot be used across processes; create it in the worker")
        timeout = self.busy_timeout_ms if busy_timeout_ms is None else busy_timeout_ms
        connection = sqlite3.connect(self.path, timeout=timeout / 1000)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            # Integer configuration only; SQLite PRAGMA assignments cannot bind parameters.
            connection.execute(f"PRAGMA busy_timeout = {timeout:d}")
            with connection:
                yield connection
        finally:
            connection.close()

    def _retry(self, operation: Callable[[], T]) -> T:
        for attempt in range(self.max_retries + 1):
            try:
                return operation()
            except sqlite3.OperationalError as error:
                # Include extended BUSY codes (e.g. BUSY_SNAPSHOT), but never
                # retry disk errors, invalid SQL, or constraint failures.
                code = getattr(error, "sqlite_errorcode", 0) & 0xFF
                if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or attempt == self.max_retries:
                    raise
                time.sleep(min(0.05 * 2 ** attempt, 1.0) + random.uniform(0, 0.05))
        raise AssertionError("unreachable")

    def _write(self, operation: Callable[[sqlite3.Connection], T], *,
               retry: bool = True, busy_timeout_ms: int | None = None) -> T:
        def transaction() -> T:
            with self.connect(busy_timeout_ms=busy_timeout_ms) as connection:
                connection.execute("BEGIN IMMEDIATE")
                return operation(connection)
        # The connection context rolls back AND closes before another attempt.
        # Only database work is replayed; never file copying or experiment code.
        return self._retry(transaction) if retry else transaction()

    def read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        def query() -> T:
            with self.connect() as connection:
                connection.execute("BEGIN")
                return operation(connection)
        return self._retry(query)

    def _initialize(self) -> None:
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema {version} is newer than supported {SCHEMA_VERSION}")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                connection.execute("PRAGMA journal_mode = WAL")
            if version == SCHEMA_VERSION:
                return
            connection.execute("BEGIN IMMEDIATE")
            # Another process may have initialized while we waited for the lock.
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")
            elif version != SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported database schema {version}")

    @staticmethod
    def _experiment(connection: sqlite3.Connection, project: str | None,
                    experiment: str | None, now: str) -> str:
        for label, value in (("project", project), ("experiment", experiment)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a nonempty string without batch_id")
        connection.execute(
            "INSERT INTO projects(id,name,created_at) VALUES (?,?,?) ON CONFLICT(name) DO NOTHING",
            (str(uuid4()), project, now),
        )
        project_id = connection.execute("SELECT id FROM projects WHERE name=?", (project,)).fetchone()[0]
        connection.execute(
            """INSERT INTO experiments(id,project_id,name,created_at) VALUES (?,?,?,?)
               ON CONFLICT(project_id,name) DO NOTHING""",
            (str(uuid4()), project_id, experiment, now),
        )
        return connection.execute(
            "SELECT id FROM experiments WHERE project_id=? AND name=?", (project_id, experiment),
        ).fetchone()[0]

    def create_batch(self, batch_id: str, project: str, experiment: str,
                     name: str | None, params: dict[str, Any], now: str) -> None:
        encoded = encode_dict(params)
        def write(connection: sqlite3.Connection) -> None:
            experiment_id = self._experiment(connection, project, experiment, now)
            connection.execute(
                """INSERT INTO batches(id,experiment_id,name,common_params_json,status,
                   started_at,created_at,updated_at) VALUES (?,?,?,?,'RUNNING',?,?,?)""",
                (batch_id, experiment_id, name, encoded, now, now, now),
            )
        self._write(write)

    def create_run(self, run_id: str, project: str | None, experiment: str | None,
                   batch_id: str | None, params: dict[str, Any], now: str,
                   metadata: dict[str, Any]) -> None:
        params = snapshot(params)
        def write(connection: sqlite3.Connection) -> None:
            if batch_id is not None:
                batch = connection.execute(
                    """SELECT b.experiment_id,b.common_params_json,p.name AS project_name,
                       e.name AS experiment_name FROM batches b
                       JOIN experiments e ON e.id=b.experiment_id
                       JOIN projects p ON p.id=e.project_id WHERE b.id=?""", (batch_id,),
                ).fetchone()
                if batch is None:
                    raise ValueError(f"Unknown batch_id: {batch_id}")
                if project is not None and project != batch["project_name"]:
                    raise ValueError("project does not match batch_id")
                if experiment is not None and experiment != batch["experiment_name"]:
                    raise ValueError("experiment does not match batch_id")
                experiment_id = batch["experiment_id"]
                effective_params = json.loads(batch["common_params_json"]) | params
            else:
                experiment_id = self._experiment(connection, project, experiment, now)
                effective_params = params
            connection.execute(
                """INSERT INTO runs(id,experiment_id,batch_id,params_json,metrics_json,source,
                   status,started_at,created_at,updated_at,hostname,process_id,thread_id,
                   script_path,working_directory,last_heartbeat_at)
                   VALUES (?,?,?,?,'{}','auto','RUNNING',?,?,?,?,?,?,?,?,?)""",
                (run_id, experiment_id, batch_id, encode_dict(effective_params), now, now, now,
                 metadata["hostname"], metadata["process_id"], metadata["thread_id"],
                 metadata["script_path"], metadata["working_directory"], now),
            )
        self._write(write)

    def merge_values(self, run_id: str, field: str, values: dict[str, Any],
                     solver: dict[str, Any] | None = None) -> None:
        if field not in ("params_json", "metrics_json"):
            raise ValueError("Invalid JSON field")
        values = json.loads(encode_dict(values))
        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                f"SELECT {field},status FROM runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None or row["status"] != "RUNNING":
                raise RuntimeError("Run is not active; completed snapshots cannot be changed")
            merged = json.loads(row[field]) | values
            # The column name is allowlisted above; all values are bound parameters.
            connection.execute(
                f"UPDATE runs SET {field}=?,updated_at=? WHERE id=?",
                (encode_dict(merged), utc_now(), run_id),
            )
            if solver is not None:
                connection.execute(
                    """UPDATE runs SET solver_name=?,solver_version=?,solver_status=?,
                       solver_runtime_s=? WHERE id=?""",
                    (solver["solver_name"], solver["solver_version"], solver["solver_status"],
                     solver["solver_runtime_s"], run_id),
                )
        self._write(write)

    def finish_run(self, run_id: str, status: str, finished_at: str, duration_s: float,
                   error: BaseException | None, traceback_path: str | None) -> None:
        def write(connection: sqlite3.Connection) -> None:
            cursor = connection.execute(
                """UPDATE runs SET status=?,finished_at=?,duration_s=?,updated_at=?,
                   raw_params_json=params_json,raw_metrics_json=metrics_json,
                   error_type=?,error_message=?,traceback_path=? WHERE id=? AND status='RUNNING'""",
                (status, finished_at, duration_s, utc_now(),
                 type(error).__name__ if error is not None else None,
                 str(error) if error is not None else None, traceback_path, run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Run could not be finalized")
        self._write(write)

    def finish_batch(self, batch_id: str, status: str) -> None:
        now = utc_now()
        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                "UPDATE batches SET status=?,finished_at=?,updated_at=? WHERE id=?",
                (status, now, now, batch_id),
            )
        self._write(write)

    def save_artifact(self, run_id: str, kind: str, original_name: str,
                      relative_path: str, size: int, sha256: str) -> None:
        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """INSERT INTO artifacts(id,run_id,kind,original_name,relative_path,
                   size_bytes,sha256,created_at) VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(relative_path) DO UPDATE SET
                   size_bytes=excluded.size_bytes,sha256=excluded.sha256""",
                (str(uuid4()), run_id, kind, original_name, relative_path, size, sha256, utc_now()),
            )
        self._write(write)

    def heartbeat(self, run_id: str) -> bool:
        def write(connection: sqlite3.Connection) -> bool:
            now = utc_now()
            return connection.execute(
                "UPDATE runs SET last_heartbeat_at=?,updated_at=? WHERE id=? AND status='RUNNING'",
                (now, now, run_id),
            ).rowcount == 1
        # Heartbeats are best effort and must not delay context cleanup for the
        # full foreground retry budget. The next tick retries naturally.
        return self._write(write, retry=False, busy_timeout_ms=250)

    def update_run(self, run_id: str, changes: dict[str, Any], reason: str) -> None:
        """Correct a finished record and append audit rows in the same transaction."""
        fields = {"params": "params_json", "metrics": "metrics_json", "notes": "notes",
                  "started_at": "started_at", "finished_at": "finished_at", "duration_s": "duration_s"}
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A nonempty reason is required for a manual correction")
        if not isinstance(changes, dict) or not changes or any(key not in fields for key in changes):
            raise ValueError(f"changes must contain only: {', '.join(fields)}")
        values = {}
        for key, value in changes.items():
            if key in ("params", "metrics"):
                value = encode_dict(value)
            elif key in ("started_at", "finished_at") and value is not None:
                if not isinstance(value, str):
                    raise TypeError("Times must be timezone-aware ISO 8601 strings or None")
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError("Times must include a timezone")
                value = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
            elif key == "duration_s" and value is not None:
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("duration_s must be a finite nonnegative number or None")
            elif key == "notes" and value is not None and not isinstance(value, str):
                raise TypeError("notes must be a string or None")
            values[fields[key]] = value

        def write(connection: sqlite3.Connection) -> None:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown run_id: {run_id}")
            if row["status"] == "RUNNING":
                raise ValueError("Cannot manually edit a RUNNING Run, including unconfirmed Runs")
            updated = dict(row) | values
            start, end = updated["started_at"], updated["finished_at"]
            elapsed = None
            if start is not None and end is not None:
                elapsed = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
                if elapsed < 0:
                    raise ValueError("finished_at must not precede started_at")
            effective = dict(values)
            if {"started_at", "finished_at"} & values.keys() and "duration_s" not in values:
                effective["duration_s"] = elapsed
            now = utc_now()
            for field, value in effective.items():
                old = row[field]
                if field in ("params_json", "metrics_json"):
                    old_json, new_json = json.loads(old), json.loads(value)
                else:
                    old_json, new_json = old, value
                if json.dumps(old_json, sort_keys=True) == json.dumps(new_json, sort_keys=True):
                    continue
                # field is selected exclusively from the fixed mapping above.
                connection.execute(f"UPDATE runs SET {field}=?,updated_at=? WHERE id=?", (value, now, run_id))
                connection.execute(
                    """INSERT INTO run_changes(id,run_id,changed_at,field_name,
                       old_value_json,new_value_json,reason,actor) VALUES (?,?,?,?,?,?,?,'local_user')""",
                    (str(uuid4()), run_id, now, field,
                     json.dumps(old_json, ensure_ascii=False, allow_nan=False),
                     json.dumps(new_json, ensure_ascii=False, allow_nan=False), reason),
                )
        self._write(write)
