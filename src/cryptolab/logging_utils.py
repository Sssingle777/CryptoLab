"""Dedicated Run loggers and opt-in, process-global console capture."""

import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, TextIO


_guard = threading.Lock()
_active: dict[str, bool] = {}
_guard_pid = os.getpid()


def _check_fork() -> None:
    global _guard_pid, _guard
    if _guard_pid != os.getpid():
        # Inheriting live logger/capture resources is unsafe. In particular,
        # do not acquire a copied lock that another parent thread may hold.
        if _active:
            raise RuntimeError("Cannot fork with active Runs; use multiprocessing spawn instead")
        _guard = threading.Lock()
        _guard_pid = os.getpid()


def reserve_run(run_id: str, capture: bool) -> None:
    _check_fork()
    with _guard:
        if (capture and _active) or any(_active.values()):
            raise RuntimeError(
                "capture_console=True requires a single active Run in this process; "
                "use capture_console=False and run.log() for overlapping Runs"
            )
        _active[run_id] = capture


def release_run(run_id: str) -> None:
    _check_fork()
    with _guard:
        _active.pop(run_id, None)


class Tee:
    def __init__(self, terminal: TextIO, file: TextIO) -> None:
        self.terminal = terminal
        self.file = file

    def write(self, text: str) -> int:
        self.file.write(text)
        self.file.flush()
        self.terminal.write(text)
        self.terminal.flush()
        return len(text)

    def flush(self) -> None:
        self.file.flush()
        self.terminal.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.terminal, name)


class ConsoleCapture:
    def __init__(self, stdout_path: Path, stderr_path: Path) -> None:
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self.stdout_file = stdout_path.open("x", encoding="utf-8")
        try:
            self.stderr_file = stderr_path.open("x", encoding="utf-8")
        except BaseException:
            self.stdout_file.close()
            raise
        sys.stdout = Tee(self.original_stdout, self.stdout_file)
        sys.stderr = Tee(self.original_stderr, self.stderr_file)

    def close(self) -> None:
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        try:
            self.stdout_file.close()
        finally:
            self.stderr_file.close()


class StrictFileHandler(logging.FileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # Standard logging may swallow file errors. A recorder must report them.
        raise


def create_logger(run_id: str, path: Path) -> logging.Logger:
    # A standalone logger avoids global logging configuration and handler leaks.
    logger = logging.Logger(f"cryptolab.{run_id}", level=logging.DEBUG)
    logger.propagate = False
    file_handler = StrictFileHandler(path, mode="x", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    # Created before capture, so run.log() appears only once in the terminal.
    terminal_handler = logging.StreamHandler(sys.stdout)
    terminal_handler.setFormatter(formatter)
    logger.addHandler(terminal_handler)
    return logger


def close_logger(logger: logging.Logger) -> None:
    errors = []
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        try:
            try:
                handler.flush()
            finally:
                handler.close()
        except Exception as error:
            errors.append(error)
    if errors:
        raise errors[0]


def report_error(error: BaseException) -> None:
    """Best-effort diagnostics must never hide the experiment's exception."""
    for stream in (sys.stderr, sys.__stderr__):
        try:
            print(f"CryptoLab recording error: {type(error).__name__}: {error}", file=stream, flush=True)
            return
        except BaseException:
            pass
