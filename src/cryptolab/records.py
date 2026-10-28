"""Management operations on the existing Database transaction/connection layer."""

from datetime import datetime, timezone
import errno
import json
import math
import os
import socket
import sqlite3
from typing import Any
from uuid import uuid4

from .artifacts import Artifacts
from .database import Database, audit_change, encode_dict, snapshot, utc_now, utc_timestamp
from .heartbeat import positive_seconds
from .monitoring import _display_status


FINAL_STATUSES = {"COMPLETED", "FAILED", "INTERRUPTED"}


def require_reason(reason: str) -> None:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A nonempty reason is required")


def _text(value: str | None, name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")
    return value


def _duration(value: float | None) -> float | None:
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise ValueError("Duration must be finite and nonnegative, or None")
    return value


def _times(start: str | None, end: str | None, duration: float | None):
    start, end, duration = utc_timestamp(start), utc_timestamp(end), _duration(duration)
    if start is not None and end is not None:
        elapsed = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        if elapsed < 0:
            raise ValueError("finished_at must not precede started_at")
        if duration is None:
            duration = elapsed
    return start, end, duration


def require_run(connection: sqlite3.Connection, run_id: str, *, finished: bool = False,
                expected_updated_at: str | None = None, allow_deleted: bool = False) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown run_id: {run_id}")
    if not allow_deleted and row["deleted_at"] is not None:
        raise ValueError("Restore the deleted Run before modifying it")
    if finished and row["status"] == "RUNNING":
        raise ValueError("Finish or confirm the RUNNING Run before modifying it")
    if expected_updated_at is not None and row["updated_at"] != expected_updated_at:
        raise ValueError("Record changed; reload before saving")
    return row


def create_manual_run(*, project: str | None = None, experiment: str | None = None,
                      batch_id: str | None = None, params: dict | None = None,
                      metrics: dict | None = None, status: str = "COMPLETED",
                      started_at: str | None = None, finished_at: str | None = None,
                      duration_s: float | None = None, notes: str | None = None,
                      solver_name: str | None = None, solver_status: str | None = None,
                      solver_version: str | None = None, solver_runtime_s: float | None = None,
                      reason: str = "手动录入历史实验") -> str:
    require_reason(reason)
    if status not in FINAL_STATUSES:
        raise ValueError("A historical Run must be COMPLETED, FAILED or INTERRUPTED")
    params, metrics = snapshot(params), snapshot(metrics)
    start, end, duration = _times(started_at, finished_at, duration_s)
    for label, value in (("notes", notes), ("solver_name", solver_name),
                         ("solver_status", solver_status), ("solver_version", solver_version)):
        _text(value, label)
    _duration(solver_runtime_s)
    database = Database()
    run_id, now = str(uuid4()), utc_now()
    files = Artifacts(database, run_id)

    def write(connection):
        experiment_id, effective = database.resolve_run(connection, project, experiment, batch_id, params, now)
        connection.execute(
            """INSERT INTO runs(id,experiment_id,batch_id,params_json,metrics_json,source,status,
               started_at,finished_at,duration_s,notes,solver_name,solver_status,solver_version,
               solver_runtime_s,created_at,updated_at)
               VALUES (?,?,?,?,?,'manual',?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, experiment_id, batch_id, encode_dict(effective), encode_dict(metrics),
             status, start, end, duration, notes, solver_name, solver_status, solver_version,
             solver_runtime_s, now, now),
        )
        # Do not invent an execution PID, heartbeat, or automatic raw snapshot.
        audit_change(connection, run_id, "created", None, {"source": "manual", "status": status}, reason, now)
    try:
        database._write(write)
    except BaseException:
        try:
            files.root.rmdir()  # This newly allocated directory is still empty.
        except OSError:
            pass  # Preserve the original database/validation exception.
        raise
    return run_id


def _apply_changes(connection, row, changes: dict, reason: str) -> None:
    allowed = {"deleted_at", "status", "finished_at", "duration_s", "raw_params_json", "raw_metrics_json"}
    if not changes.keys() <= allowed:
        raise ValueError("Invalid lifecycle change")
    now = utc_now()
    for field, value in changes.items():
        old = row[field]
        if old == value:
            continue
        connection.execute(f"UPDATE runs SET {field}=?,updated_at=? WHERE id=?", (value, now, row["id"]))
        if field.endswith("_json"):
            old = json.loads(old) if old is not None else None
            new = json.loads(value) if value is not None else None
        else:
            new = value
        audit_change(connection, row["id"], field, old, new, reason, now)


def soft_delete_run(run_id: str, *, reason: str, expected_updated_at: str | None = None) -> None:
    require_reason(reason)
    def write(connection):
        row = require_run(connection, run_id, finished=True, expected_updated_at=expected_updated_at, allow_deleted=True)
        if row["deleted_at"] is None:
            _apply_changes(connection, row, {"deleted_at": utc_now()}, reason)
    Database()._write(write)


def restore_run(run_id: str, *, reason: str, expected_updated_at: str | None = None) -> None:
    require_reason(reason)
    def write(connection):
        row = require_run(connection, run_id, expected_updated_at=expected_updated_at, allow_deleted=True)
        _apply_changes(connection, row, {"deleted_at": None}, reason)
    Database()._write(write)


def _check_process_stopped(row) -> None:
    if row["hostname"] != socket.gethostname() or row["process_id"] is None:
        return  # Imported/old records require explicit human confirmation below.
    pid = row["process_id"]
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError("Invalid recorded PID; cannot safely confirm this Run")
    try:
        os.kill(pid, 0)  # Probe only. Never send a terminating signal.
    except ProcessLookupError:
        return
    except OSError as error:
        if error.errno == errno.ESRCH:
            return
        raise ValueError("Cannot verify the recorded process is stopped") from error
    raise ValueError("The recorded process is still alive; refusing to finish its Run")


def confirm_run_stopped(run_id: str, *, expected_heartbeat_at: str | None,
                        expected_updated_at: str, confirmed_stopped: bool, reason: str,
                        status: str = "INTERRUPTED", finished_at: str | None = None,
                        heartbeat_timeout_s: float = 120) -> None:
    require_reason(reason)
    if confirmed_stopped is not True:
        raise ValueError("Explicit confirmation that the experiment has stopped is required")
    if not isinstance(expected_updated_at, str):
        raise ValueError("A fresh record version is required")
    if status not in {"INTERRUPTED", "FAILED"}:
        raise ValueError("Confirmation may set only INTERRUPTED or FAILED")
    timeout = positive_seconds(heartbeat_timeout_s, "heartbeat_timeout_s")
    end = utc_timestamp(finished_at)

    def write(connection):
        row = require_run(connection, run_id, expected_updated_at=expected_updated_at)
        if row["last_heartbeat_at"] != expected_heartbeat_at:
            raise ValueError("Heartbeat changed; reload before confirming")
        if row["status"] != "RUNNING" or _display_status(row, timeout, datetime.now(timezone.utc)) != "UNCONFIRMED":
            raise ValueError("Only a stale, unconfirmed RUNNING Run can be confirmed")
        _check_process_stopped(row)
        if end is not None and datetime.fromisoformat(end) > datetime.now(timezone.utc):
            raise ValueError("A confirmed finish time must not be in the future")
        _, _, duration = _times(row["started_at"], end, None)
        changes = {"status": status, "finished_at": end, "duration_s": duration}
        if row["source"] == "auto":
            # Freeze the last committed facts, never replace an existing snapshot.
            if row["raw_params_json"] is None:
                changes["raw_params_json"] = row["params_json"]
            if row["raw_metrics_json"] is None:
                changes["raw_metrics_json"] = row["metrics_json"]
        _apply_changes(connection, row, changes, reason)
        audit_change(connection, run_id, "confirmed_stopped", None,
                     {"last_heartbeat_at": row["last_heartbeat_at"], "status": status,
                      "finished_at_known": end is not None}, reason, utc_now())
    Database()._write(write)


def list_projects() -> list[dict]:
    return Database().read(lambda connection: [dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY name")])


def list_experiments(project_id: str | None = None) -> list[dict]:
    return Database().read(lambda connection: [dict(row) for row in connection.execute(
        """SELECT e.*,p.name AS project_name FROM experiments e JOIN projects p ON p.id=e.project_id
           WHERE (? IS NULL OR e.project_id=?) ORDER BY p.name,e.name""", (project_id, project_id),
    )])


def list_batches(experiment_id: str | None = None) -> list[dict]:
    return Database().read(lambda connection: [dict(row) for row in connection.execute(
        "SELECT * FROM batches WHERE (? IS NULL OR experiment_id=?) ORDER BY created_at DESC", (experiment_id, experiment_id),
    )])


def save_project(name: str, *, description: str | None = None, project_id: str | None = None) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Project name must not be empty")
    _text(description, "description")
    identifier = project_id or str(uuid4())
    def write(connection):
        if project_id is None:
            connection.execute("INSERT INTO projects(id,name,description,created_at) VALUES (?,?,?,?)",
                               (identifier, name.strip(), description, utc_now()))
        elif connection.execute("UPDATE projects SET name=?,description=? WHERE id=?",
                                (name.strip(), description, identifier)).rowcount != 1:
            raise ValueError("Unknown project")
    Database()._write(write)
    return identifier


def save_experiment(project_id: str, name: str, *, method: str | None = None,
                    cipher_name: str | None = None, description: str | None = None,
                    experiment_id: str | None = None) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Experiment name must not be empty")
    for label, value in (("method", method), ("cipher_name", cipher_name), ("description", description)):
        _text(value, label)
    identifier = experiment_id or str(uuid4())
    def write(connection):
        if experiment_id is None:
            connection.execute(
                "INSERT INTO experiments(id,project_id,name,method,cipher_name,description,created_at) VALUES (?,?,?,?,?,?,?)",
                (identifier, project_id, name.strip(), method, cipher_name, description, utc_now()),
            )
        elif connection.execute(
            "UPDATE experiments SET name=?,method=?,cipher_name=?,description=? WHERE id=? AND project_id=?",
            (name.strip(), method, cipher_name, description, identifier, project_id),
        ).rowcount != 1:
            raise ValueError("Unknown experiment in this project")
    Database()._write(write)
    return identifier


RUN_SELECT = """SELECT r.*,e.name AS experiment_name,e.project_id,e.cipher_name,e.method,
                p.name AS project_name,b.name AS batch_name FROM runs r
                JOIN experiments e ON e.id=r.experiment_id JOIN projects p ON p.id=e.project_id
                LEFT JOIN batches b ON b.id=r.batch_id"""


def _decode(row, timeout: float, now: datetime) -> dict:
    result = dict(row)
    result["params"] = json.loads(row["params_json"])
    result["metrics"] = json.loads(row["metrics_json"])
    result["display_status"] = _display_status(row, timeout, now)
    return result


def get_run(run_id: str, *, heartbeat_timeout_s: float = 120) -> dict:
    timeout = positive_seconds(heartbeat_timeout_s, "heartbeat_timeout_s")
    def read(connection):
        row = connection.execute(RUN_SELECT + " WHERE r.id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown run_id: {run_id}")
        result = _decode(row, timeout, datetime.now(timezone.utc))
        result["artifacts"] = [dict(item) for item in connection.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at,id", (run_id,))]
        result["changes"] = [dict(item) for item in connection.execute("SELECT * FROM run_changes WHERE run_id=? ORDER BY changed_at,rowid", (run_id,))]
        return result
    return Database().read(read)


def list_runs(*, project_id: str | None = None, experiment_id: str | None = None,
              batch_id: str | None = None, rounds: float | None = None,
              started_from: str | None = None, started_before: str | None = None,
              status: str | None = None, solver_status: str | None = None,
              source: str | None = None, cipher_name: str | None = None,
              keyword: str = "", deleted: str = "exclude", heartbeat_timeout_s: float = 120) -> list[dict]:
    timeout = positive_seconds(heartbeat_timeout_s, "heartbeat_timeout_s")
    if deleted not in {"exclude", "only", "include"}:
        raise ValueError("deleted must be exclude, only or include")
    if status is not None and status not in FINAL_STATUSES | {"RUNNING", "UNCONFIRMED"}:
        raise ValueError("Invalid status filter")
    if source is not None and source not in {"auto", "manual"}:
        raise ValueError("Invalid source filter")
    conditions, values = [], []
    if deleted != "include":
        conditions.append("r.deleted_at IS " + ("NOT NULL" if deleted == "only" else "NULL"))
    for column, value in (("e.project_id", project_id), ("r.experiment_id", experiment_id),
                          ("r.batch_id", batch_id), ("r.solver_status", solver_status),
                          ("r.source", source), ("e.cipher_name", cipher_name)):
        if value is not None:
            conditions.append(f"{column}=?")
            values.append(value)
    if rounds is not None:
        if type(rounds) not in (int, float) or not math.isfinite(rounds):
            raise ValueError("rounds must be a finite number")
        conditions.append("json_extract(r.params_json,'$.rounds')=?")
        values.append(rounds)
    start, end = utc_timestamp(started_from), utc_timestamp(started_before)
    if start is not None and end is not None and start >= end:
        raise ValueError("Date range must have a positive length")
    for operator, value in ((">=", start), ("<", end)):
        if value is not None:
            conditions.append(f"julianday(r.started_at){operator}julianday(?)")
            values.append(value)
    if keyword:
        literal = "%" + keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        columns = ("r.id", "p.name", "e.name", "r.notes", "r.params_json")
        conditions.append("(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in columns) + ")")
        values.extend([literal] * len(columns))
    sql = RUN_SELECT + (" WHERE " + " AND ".join(conditions) if conditions else "")
    sql += " ORDER BY r.started_at DESC,r.created_at DESC,r.id"
    def read(connection):
        now = datetime.now(timezone.utc)
        result = []
        for row in connection.execute(sql, values):
            decoded = _decode(row, timeout, now)
            if status is None or decoded["display_status"] == status:
                result.append(decoded)
        return result
    return Database().read(read)
