from __future__ import annotations

from contextlib import contextmanager
import shutil
import sys
import threading
import time
from typing import TextIO

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
REDRAW_SECONDS = 0.1
MAX_BAR_WIDTH = 30


class Progress:
    def __init__(self, stream: TextIO | None):
        self._stream = stream
        self._lock = threading.Lock()
        self._title = ""
        self._activity = ""
        self._fraction_done: float | None = None
        self._started = time.monotonic()
        self._stopped = threading.Event()
        self._spinner: threading.Thread | None = None

    @classmethod
    def on_terminal(cls) -> Progress:
        return cls(sys.stderr if sys.stderr.isatty() else None)

    def __enter__(self) -> Progress:
        if self._stream is not None:
            self._spinner = threading.Thread(target=self._spin, daemon=True)
            self._spinner.start()
        return self

    def __exit__(self, *exc_info):
        self._stopped.set()
        if self._spinner is not None:
            self._spinner.join()
        with self._lock:
            self._erase()

    def begin(self, title: str):
        with self._lock:
            self._title, self._started = title, time.monotonic()
            self._activity, self._fraction_done = "", None

    def stage(self, activity: str):
        with self._lock:
            self._activity, self._fraction_done = activity, None

    def count(self, activity: str, done: int, total: int, unit: str):
        done = min(done, total)
        with self._lock:
            self._activity = f"{activity} {done}/{total} {unit}"
            self._fraction_done = done / total if total else 1.0

    def end(self, symbol: str, outcome: str):
        with self._lock:
            if self._stream is None:
                return
            self._erase()
            self._stream.write(_fit(f"{symbol} {self._title}  {outcome}  {self._elapsed()}") + "\n")
            self._stream.flush()
            self._title, self._activity, self._fraction_done = "", "", None

    def _spin(self):
        frame = 0
        while not self._stopped.wait(REDRAW_SECONDS):
            with self._lock:
                if self._title:
                    self._draw(SPINNER_FRAMES[frame % len(SPINNER_FRAMES)])
            frame += 1

    def _draw(self, spinner: str):
        assert self._stream is not None
        line = f"{spinner} {self._title}  {self._activity}"
        elapsed = f"  {self._elapsed()}"
        if self._fraction_done is not None:
            bar_width = min(MAX_BAR_WIDTH, _columns() - len(line) - len(elapsed) - 1)
            if bar_width >= 5:
                filled = round(bar_width * self._fraction_done)
                line += " " + "█" * filled + "░" * (bar_width - filled)
        self._stream.write("\r\033[K" + _fit(line + elapsed))
        self._stream.flush()

    def _erase(self):
        if self._stream is not None:
            self._stream.write("\r\033[K")
            self._stream.flush()

    def _elapsed(self) -> str:
        minutes, seconds = divmod(int(time.monotonic() - self._started), 60)
        return f"{minutes}:{seconds:02d}"


@contextmanager
def waiting_for(title: str):
    with Progress.on_terminal() as progress:
        progress.begin(title)
        yield


def _columns() -> int:
    return shutil.get_terminal_size().columns - 1


def _fit(line: str) -> str:
    width = _columns()
    return line if len(line) <= width else line[: width - 1] + "…"
