"""In-memory timing totals scoped to one top-level CLI invocation."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class CommandTimingSession:
    """Accumulate only stages that actually run during the current command."""

    totals_ms: dict[str, float] = field(default_factory=dict)

    def record(self, event_type: str, duration_ms: float) -> None:
        duration = max(0.0, float(duration_ms))
        self.totals_ms[event_type] = self.totals_ms.get(event_type, 0.0) + duration


_CURRENT_SESSION: ContextVar[CommandTimingSession | None] = ContextVar(
    "writer_command_timing_session", default=None
)


@contextmanager
def command_timing_session() -> Iterator[CommandTimingSession]:
    """Create a fresh accumulator for one CLI command, including its resumes."""
    session = CommandTimingSession()
    token = _CURRENT_SESSION.set(session)
    try:
        yield session
    finally:
        _CURRENT_SESSION.reset(token)


def current_command_timing() -> CommandTimingSession | None:
    return _CURRENT_SESSION.get()


def record_command_timing(event_type: str, duration_ms: float) -> None:
    session = current_command_timing()
    if session is not None:
        session.record(event_type, duration_ms)
