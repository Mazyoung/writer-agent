"""Ephemeral single-line TTY timer for synchronous workflow stages."""

from __future__ import annotations

import sys
import threading
import time
from typing import TextIO


LIVE_TIMER_REFRESH_SECONDS = 1.0
_active = threading.local()


class _TimerOutputProxy:
    """Serialize ordinary stdout writes with the timer's transient line."""

    def __init__(self, timer: "LiveStageTimer"):
        self._timer = timer

    def write(self, data: str) -> int:
        return self._timer.write_passthrough(data)

    def flush(self) -> None:
        self._timer.flush_passthrough()

    def __getattr__(self, name: str):
        return getattr(self._timer.output, name)


class LiveStageTimer:
    """Refresh elapsed seconds on a daemon UI thread without doing business work."""

    def __init__(
        self,
        chapter_index: int,
        message: str,
        *,
        output: TextIO | None = None,
        refresh_seconds: float = LIVE_TIMER_REFRESH_SECONDS,
    ):
        self.chapter_index = chapter_index
        self.message = message
        self.output = output or sys.stdout
        self.refresh_seconds = refresh_seconds
        self.started: float | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._proxy: _TimerOutputProxy | None = None
        self._line_visible = False
        self._passthrough_open = False
        self._tty_enabled = False

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def start(self) -> "LiveStageTimer":
        try:
            self.started = time.perf_counter()
        except Exception:
            self.started = None
        try:
            self._tty_enabled = bool(self.output.isatty())
        except Exception:
            self._tty_enabled = False
        if not self._tty_enabled:
            return self
        try:
            self._proxy = _TimerOutputProxy(self)
            if sys.stdout is self.output:
                sys.stdout = self._proxy
            self._thread = threading.Thread(
                target=self._run,
                name=f"chapter-{self.chapter_index}-live-timer",
                daemon=True,
            )
            self._thread.start()
        except Exception:
            self._disable_tty()
        return self

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.refresh_seconds):
                with self._lock:
                    self._draw_locked()
        except Exception:
            self._disable_tty()

    def _elapsed_seconds(self) -> float:
        if self.started is None:
            return 0.0
        try:
            return max(0.0, time.perf_counter() - self.started)
        except Exception:
            return 0.0

    def _clear_locked(self) -> None:
        if not self._line_visible:
            return
        self.output.write("\r\033[2K")
        self.output.flush()
        self._line_visible = False

    def _draw_locked(self) -> None:
        if not self._tty_enabled or self._stop.is_set() or self._passthrough_open:
            return
        elapsed = self._elapsed_seconds()
        self.output.write(
            "\r\033[2K"
            f"[Chapter {self.chapter_index}] 正在{self.message}... "
            f"已用时 {elapsed:.0f} 秒"
        )
        self.output.flush()
        self._line_visible = True

    def write_passthrough(self, data: str) -> int:
        with self._lock:
            try:
                self._clear_locked()
            except Exception:
                self._line_visible = False
                self._tty_enabled = False
                if self._proxy is not None and sys.stdout is self._proxy:
                    sys.stdout = self.output
            written = self.output.write(data)
            self._passthrough_open = bool(data) and not data.endswith(("\n", "\r"))
            if not self._passthrough_open:
                try:
                    self._draw_locked()
                except Exception:
                    self._line_visible = False
                    self._tty_enabled = False
                    if self._proxy is not None and sys.stdout is self._proxy:
                        sys.stdout = self.output
            return len(data) if written is None else written

    def flush_passthrough(self) -> None:
        with self._lock:
            self.output.flush()

    def _disable_tty(self) -> None:
        with self._lock:
            try:
                self._clear_locked()
            except Exception:
                pass
            if self._proxy is not None and sys.stdout is self._proxy:
                sys.stdout = self.output
            self._tty_enabled = False

    def finish(self) -> float:
        duration_ms = self._elapsed_seconds() * 1000
        try:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=max(2.0, self.refresh_seconds + 0.5))
            self._disable_tty()
        except Exception:
            self._disable_tty()
        if getattr(_active, "timer", None) is self:
            _active.timer = None
        return max(0.0, duration_ms)

    def cancel(self) -> None:
        self.finish()


def start_live_stage_timer(chapter_index: int, message: str) -> LiveStageTimer:
    cancel_active_stage_timer()
    timer = LiveStageTimer(chapter_index, message).start()
    _active.timer = timer
    return timer


def cancel_active_stage_timer() -> None:
    timer = getattr(_active, "timer", None)
    if timer is None:
        return
    try:
        timer.cancel()
    except Exception:
        pass
    finally:
        _active.timer = None
