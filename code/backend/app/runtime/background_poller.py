from __future__ import annotations

import threading
import time
from collections.abc import Callable


class BackgroundPoller:
    def __init__(self, *, name: str, interval_seconds: float, fn: Callable[[], object]) -> None:
        self._name = name
        self._interval_seconds = max(0.1, interval_seconds)
        self._fn = fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_seconds + 1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._fn()
            except Exception:
                # Keep pollers alive; failures surface through state drift/tests.
                pass
            self._stop.wait(self._interval_seconds)
