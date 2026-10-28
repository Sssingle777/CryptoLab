"""Low-frequency, best-effort liveness writes; no shared SQLite connection."""

import math
import threading

from .database import Database
from .logging_utils import report_error


def positive_seconds(value: float, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


class Heartbeat:
    def __init__(self, database: Database, run_id: str, interval_s: float) -> None:
        self._database = database  # Holds paths/settings only, never a connection.
        self._run_id = run_id
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"cryptolab-heartbeat-{run_id}", daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            # A heartbeat uses a 250 ms busy timeout, without foreground retries.
            self._thread.join()

    def _loop(self) -> None:
        reported = False
        while not self._stop.wait(self._interval_s):
            try:
                if not self._database.heartbeat(self._run_id):
                    return
                reported = False
            except Exception as error:
                if not reported:
                    report_error(RuntimeError(
                        f"Heartbeat for Run {self._run_id} failed: {type(error).__name__}: {error}; "
                        "will retry on the next tick"
                    ))
                    reported = True
