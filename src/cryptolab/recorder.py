"""Context lifecycles, with experiment exceptions taking precedence over I/O."""

import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
from typing import Any, Callable, TypeVar
from uuid import uuid4

from .adapters.gurobi import extract
from .artifacts import Artifacts
from .database import Database, snapshot, utc_now
from .heartbeat import Heartbeat, positive_seconds
from .logging_utils import (
    ConsoleCapture, close_logger, create_logger, release_run, report_error, reserve_run,
)


T = TypeVar("T")


def _status(error: BaseException | None) -> str:
    if error is None:
        return "COMPLETED"
    return "INTERRUPTED" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "FAILED"


def _metadata() -> dict[str, Any]:
    script = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    return {
        "hostname": socket.gethostname(),
        "process_id": os.getpid(),
        "thread_id": threading.get_ident(),
        "working_directory": str(Path.cwd()),
        "script_path": str(script.resolve()) if script is not None and script.is_file() else None,
    }


class Run:
    def __init__(self, *, project: str | None, experiment: str | None,
                 params: dict[str, Any] | None, batch_id: str | None,
                 capture_console: bool, heartbeat_interval_s: float = 30) -> None:
        if not isinstance(capture_console, bool):
            raise TypeError("capture_console must be a bool")
        self._id = str(uuid4())
        self._project = project
        self._experiment = experiment
        self._params = snapshot(params)
        self._batch_id = batch_id
        self._capture_console = capture_console
        self._heartbeat_interval_s = positive_seconds(heartbeat_interval_s, "heartbeat_interval_s")
        self._heartbeat: Heartbeat | None = None
        self._entered = False
        self._active = False
        self._owner_pid = os.getpid()
        self._lock = threading.RLock()
        self._recording_error: Exception | None = None
        self._logger: logging.Logger | None = None
        self._capture: ConsoleCapture | None = None
        self._files: Artifacts | None = None

    @property
    def id(self) -> str:
        return self._id

    def __getstate__(self) -> None:
        raise TypeError("Run objects cannot be shared across processes; pass batch_id instead")

    def _require_process(self) -> None:
        if self._owner_pid != os.getpid():
            raise RuntimeError("Run objects cannot be shared across processes; pass batch_id instead")

    def __enter__(self) -> "Run":
        self._require_process()
        if self._entered:
            raise RuntimeError("A Run context cannot be reused")
        self._entered = True
        reserve_run(self.id, self._capture_console)
        created = False
        try:
            self._database = Database()
            metadata = _metadata()
            self._started_clock = time.perf_counter()
            self._database.create_run(
                self.id, self._project, self._experiment, self._batch_id,
                self._params, utc_now(), metadata,
            )
            created = True
            self._files = Artifacts(self._database, self.id)
            self._logger = create_logger(self.id, self._files.output_path("run.log", internal=True))
            if self._capture_console:
                self._capture = ConsoleCapture(
                    self._files.output_path("stdout.log", internal=True),
                    self._files.output_path("stderr.log", internal=True),
                )
            self._active = True
            self._heartbeat = Heartbeat(self._database, self.id, self._heartbeat_interval_s)
            self._heartbeat.start()
            return self
        except BaseException as error:
            report_error(error)
            if created:
                self._finish(error)
            else:
                release_run(self.id)
            raise

    def _require_active(self) -> None:
        self._require_process()
        if not self._active:
            raise RuntimeError("Run methods must be called inside its with block")

    def _record(self, operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        # This lock belongs to one Run, never to the whole SDK. It also prevents
        # finalization from closing files while an explicit helper thread logs.
        with self._lock:
            self._require_active()
            try:
                return operation(*args, **kwargs)
            except Exception as error:
                self._recording_error = self._recording_error or error
                report_error(error)
                raise

    def log_metrics(self, values: dict[str, Any]) -> None:
        """Merge top-level metric keys; a later value replaces the whole key."""
        self._require_active()
        self._record(self._database.merge_values, self.id, "metrics_json", values)

    def log_params(self, values: dict[str, Any]) -> None:
        """Merge actual parameters while running; the final snapshot is frozen."""
        self._require_active()
        self._record(self._database.merge_values, self.id, "params_json", values)

    def log_gurobi(self, model: Any) -> None:
        """Save attributes under metrics['gurobi'] and separate solver columns."""
        self._require_active()
        metrics, solver = extract(model)
        self._record(self._database.merge_values, self.id, "metrics_json", metrics, solver)

    def log(self, message: str, level: str = "INFO") -> None:
        """Write immediately to this Run's file and once to the terminal."""
        self._require_active()
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        levels = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING,
                  "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}
        if level not in levels:
            raise ValueError(f"level must be one of {', '.join(levels)}")
        assert self._logger is not None
        self._record(self._logger.log, levels[level], message)

    def add_file(self, path: str | os.PathLike[str], name: str | None = None) -> Path:
        self._require_active()
        assert self._files is not None
        return self._record(self._files.add_file, path, name)

    def output_path(self, name: str) -> Path:
        self._require_active()
        assert self._files is not None
        return self._record(self._files.output_path, name)

    def __exit__(self, exc_type: Any, error: BaseException | None, tb: Any) -> bool:
        self._require_active()
        self._finish(error)
        return False

    def _finish(self, experiment_error: BaseException | None) -> None:
        with self._lock:
            self._finish_locked(experiment_error)

    def _finish_locked(self, experiment_error: BaseException | None) -> None:
        # End the experiment clock before hashing files and updating metadata.
        finished_at = utc_now()
        duration = time.perf_counter() - self._started_clock
        errors: list[Exception] = []
        if self._recording_error is not None:
            errors.append(self._recording_error)

        def attempt(operation: Callable[..., Any], *args: Any) -> None:
            try:
                operation(*args)
            except Exception as error:
                errors.append(error)
                report_error(error)

        traceback_path = None
        try:
            if self._heartbeat is not None:
                attempt(self._heartbeat.stop)
            if self._capture is not None:
                attempt(self._capture.close)
            if self._logger is not None:
                attempt(close_logger, self._logger)
            error = experiment_error if experiment_error is not None else (errors[0] if errors else None)
            if error is not None and self._files is not None:
                try:
                    path = self._files.output_path("traceback.txt", internal=True)
                    with path.open("x", encoding="utf-8") as file:
                        traceback.print_exception(type(error), error, error.__traceback__, file=file)
                    traceback_path = path.relative_to(self._database.home).as_posix()
                except Exception as failure:
                    errors.append(failure)
                    report_error(failure)
            if self._files is not None:
                for path in self._files.files:
                    attempt(self._files.register, path)
            error = experiment_error if experiment_error is not None else (errors[0] if errors else None)
            attempt(self._database.finish_run, self.id, _status(error), finished_at, duration,
                    error, traceback_path)
        finally:
            self._active = False
            release_run(self.id)
        # Never replace the user's exception, even if finalization also fails.
        if experiment_error is None and errors:
            raise errors[0]


class Batch:
    def __init__(self, *, project: str, experiment: str, name: str | None,
                 common_params: dict[str, Any] | None) -> None:
        self._id = str(uuid4())
        self._project = project
        self._experiment = experiment
        self._name = name
        self._params = snapshot(common_params)
        self._entered = False
        self._active = False
        self._owner_pid = os.getpid()

    @property
    def id(self) -> str:
        return self._id

    def __getstate__(self) -> None:
        raise TypeError("Pass batch.id to workers, not the Batch context object")

    def _require_process(self) -> None:
        if self._owner_pid != os.getpid():
            raise RuntimeError("Pass batch.id to workers, not the Batch context object")

    def __enter__(self) -> "Batch":
        self._require_process()
        if self._entered:
            raise RuntimeError("A Batch context cannot be reused")
        self._entered = True
        try:
            self._database = Database()
            self._database.create_batch(self.id, self._project, self._experiment,
                                        self._name, self._params, utc_now())
        except Exception as error:
            report_error(error)
            raise
        self._active = True
        return self

    def __exit__(self, exc_type: Any, error: BaseException | None, tb: Any) -> bool:
        self._require_process()
        if not self._active:
            raise RuntimeError("Batch is not active")
        self._active = False
        try:
            # Scope status is independent of the live get_batch_summary() counts.
            self._database.finish_batch(self.id, _status(error))
        except Exception as failure:
            report_error(failure)
            if error is None:
                raise
        return False
