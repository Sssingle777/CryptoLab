"""Read-only liveness views. A stale heartbeat is uncertainty, not failure."""

from datetime import datetime, timezone
import sqlite3
from typing import Any

from .database import Database
from .heartbeat import positive_seconds


def _display_status(row: sqlite3.Row, timeout_s: float, now: datetime) -> str:
    if row["status"] != "RUNNING":
        return row["status"]
    # Legacy phase-1 records have no heartbeat: fall back to their start/creation.
    last_seen = row["last_heartbeat_at"] or row["started_at"] or row["created_at"]
    try:
        timestamp = datetime.fromisoformat(last_seen)
        age = (now - timestamp).total_seconds()
    except (TypeError, ValueError):
        return "UNCONFIRMED"
    return "UNCONFIRMED" if age > timeout_s else "RUNNING"


def get_run_status(run_id: str, *, heartbeat_timeout_s: float = 120) -> dict[str, Any]:
    """Return persisted status and display_status; UNCONFIRMED never changes the DB."""
    timeout = positive_seconds(heartbeat_timeout_s, "heartbeat_timeout_s")

    def read(connection: sqlite3.Connection) -> dict[str, Any]:
        row = connection.execute(
            """SELECT id,status,started_at,finished_at,duration_s,last_heartbeat_at,
               process_id,thread_id,created_at FROM runs WHERE id=?""", (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown run_id: {run_id}")
        return dict(row) | {"display_status": _display_status(row, timeout, datetime.now(timezone.utc))}

    return Database().read(read)


def get_batch_summary(batch_id: str, *, heartbeat_timeout_s: float = 120) -> dict[str, Any]:
    """Read scope status and mutually exclusive child counts from one DB snapshot."""
    timeout = positive_seconds(heartbeat_timeout_s, "heartbeat_timeout_s")

    def read(connection: sqlite3.Connection) -> dict[str, Any]:
        batch = connection.execute("SELECT status FROM batches WHERE id=?", (batch_id,)).fetchone()
        if batch is None:
            raise ValueError(f"Unknown batch_id: {batch_id}")
        result = {"batch_id": batch_id, "batch_status": batch["status"], "total": 0,
                  "completed": 0, "failed": 0, "interrupted": 0, "running": 0, "unconfirmed": 0}
        now = datetime.now(timezone.utc)
        for row in connection.execute(
            """SELECT status,last_heartbeat_at,started_at,created_at FROM runs
               WHERE batch_id=? AND deleted_at IS NULL""", (batch_id,),
        ):
            result[_display_status(row, timeout, now).lower()] += 1
            result["total"] += 1
        return result

    return Database().read(read)
