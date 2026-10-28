"""Small public API; construction is lazy and does not create a database."""

from typing import Any

from .database import Database
from .recorder import Batch, Run


def track_run(*, project: str | None = None, experiment: str | None = None,
              params: dict[str, Any] | None = None, batch_id: str | None = None,
              capture_console: bool = False, heartbeat_interval_s: float = 30) -> Run:
    """Create a fresh Run on context entry; project/experiment may come from Batch."""
    return Run(project=project, experiment=experiment, params=params,
               batch_id=batch_id, capture_console=capture_console, heartbeat_interval_s=heartbeat_interval_s)


def track_batch(*, project: str, experiment: str, name: str | None = None,
                common_params: dict[str, Any] | None = None) -> Batch:
    """Group Runs under a shared parameter snapshot; does not schedule any work."""
    return Batch(project=project, experiment=experiment, name=name, common_params=common_params)


def update_run(run_id: str, changes: dict[str, Any], *, reason: str) -> None:
    """Manually correct a finished Run with an audit trail; raw snapshots stay frozen."""
    Database().update_run(run_id, changes, reason)
