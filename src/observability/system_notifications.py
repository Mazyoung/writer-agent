"""Best-effort runtime notifications outside workflow and story state."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import base64
import html
import subprocess
import sys
from typing import Callable, Iterator


NotificationBackend = Callable[[str, str], None]


def _windows_toast(title: str, message: str) -> None:
    """Start a Windows toast sender without waiting for it to finish."""
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] > $null; "
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        f"$xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{safe_title}</text><text>{safe_message}</text>"
        "</binding></visual></toast>'); "
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; "
        "[Windows.UI.Notifications.ToastNotificationManager]"
        "::CreateToastNotifier('Writer-Agent').Show($toast)"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive",
            "-WindowStyle", "Hidden", "-EncodedCommand", encoded,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def send_system_notification(title: str, message: str) -> None:
    """Use Windows Toast when available; other platforms intentionally no-op."""
    if sys.platform != "win32":
        return
    _windows_toast(title, message)


@dataclass
class NotificationSession:
    backend: NotificationBackend = send_system_notification
    waiting_keys: set[tuple[str, int, str]] = field(default_factory=set)
    terminal_notified: bool = False

    def _safe_send(self, title: str, message: str) -> None:
        try:
            self.backend(title, message)
        except Exception:
            # Runtime UI must never replace or modify a workflow outcome.
            return

    def waiting_human(
        self, novel_id: str, chapter: int, review_label: str
    ) -> None:
        key = (novel_id, chapter, review_label)
        if key in self.waiting_keys:
            return
        self.waiting_keys.add(key)
        self._safe_send(
            "Writer-Agent needs your action",
            f"{novel_id} Chapter {chapter} - {review_label}",
        )

    def finished(self, novel_id: str, chapter: int) -> None:
        if self.terminal_notified:
            return
        self.terminal_notified = True
        self._safe_send(
            "Writer-Agent run completed",
            f"{novel_id} Chapter {chapter} completed",
        )

    def error(self, novel_id: str, chapter: int, stage: str) -> None:
        if self.terminal_notified:
            return
        self.terminal_notified = True
        self._safe_send(
            "Writer-Agent run failed",
            f"{novel_id} Chapter {chapter} - {stage}",
        )


_CURRENT: ContextVar[NotificationSession | None] = ContextVar(
    "writer_notification_session", default=None
)


@contextmanager
def notification_session(
    backend: NotificationBackend | None = None,
) -> Iterator[NotificationSession]:
    session = NotificationSession(backend or send_system_notification)
    token = _CURRENT.set(session)
    try:
        yield session
    finally:
        _CURRENT.reset(token)


def current_notification_session() -> NotificationSession | None:
    return _CURRENT.get()
