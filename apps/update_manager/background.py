from __future__ import annotations

import subprocess
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Slot

from desktop.notifications import NotificationCenter
from desktop.version import APP_VERSION
from update_manager import UpdateError, UpdateManager, UpdateRelease

from .app import UpdateActionResult, UpdateWorker


class BackgroundUpdateWatcher(QObject):
    def __init__(self, notifications: NotificationCenter) -> None:
        super().__init__()
        self.notifications = notifications
        self.manager = UpdateManager(current_version=APP_VERSION)
        self.worker: UpdateWorker | None = None
        self.open_details: Callable[[UpdateRelease], None] | None = None
        self.open_install: Callable[[UpdateRelease], None] | None = None
        self._auto_install_cancelled = False

    def check(self) -> None:
        self.manager.reload_preferences()
        if not self.manager.preferences["auto_check"]:
            return
        if self.worker is not None and self.worker.isRunning():
            return

        def operation():
            release = self.manager.check_for_updates()
            downloaded = False
            if release is not None and (
                self.manager.preferences["auto_download"]
                or self.manager.preferences["auto_install"]
            ):
                self.manager.download(release)
                downloaded = True
            return release, downloaded

        self.worker = UpdateWorker(operation)
        self.worker.completed.connect(self._check_completed)
        self.worker.failed.connect(self._check_failed)
        self.worker.start()

    @Slot(object)
    def _check_completed(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) != 2:
            return
        release, downloaded = result
        if release is None:
            return
        detail = f"MyOS {release.version} ist verfügbar."
        if downloaded:
            detail += " Das Update wurde heruntergeladen und geprüft."
        auto_install = self.manager.preferences["auto_install"] and downloaded
        if auto_install:
            detail += " Die automatische Installation beginnt in Kürze; „Später“ bricht sie ab."
            self._auto_install_cancelled = False
        self.notifications.notify_update(
            release.version,
            details=lambda: self._open_release(release, install=False),
            install=lambda: self._open_release(release, install=True),
            later=self._cancel_auto_install,
            message=detail,
        )
        if auto_install:
            QTimer.singleShot(6000, self._install_after_notice)

    def _cancel_auto_install(self) -> None:
        self._auto_install_cancelled = True

    def _open_release(self, release: UpdateRelease, *, install: bool) -> None:
        if install:
            self._cancel_auto_install()
        callback = self.open_install if install else self.open_details
        if callback is not None:
            callback(release)

    @Slot(str)
    def _check_failed(self, message: str) -> None:
        self.manager.record_status("error", message)
        self.notifications.notify("Updates nicht geprüft", message, "MyOS Update")

    def _install_after_notice(self) -> None:
        if self._auto_install_cancelled:
            return

        def operation() -> UpdateActionResult:
            try:
                result = subprocess.run(
                    ["/usr/bin/pkexec", "/usr/bin/myos-update", "install"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as error:
                raise UpdateError(
                    "Die automatische Installation konnte nicht gestartet werden.",
                    str(error),
                ) from error
            if result.returncode != 0:
                raise UpdateError(
                    "Die automatische Installation ist fehlgeschlagen. "
                    "Die bisherige Version bleibt aktiv.",
                    result.stderr,
                )
            return UpdateActionResult("install", result)

        self.worker = UpdateWorker(operation)
        self.worker.completed.connect(self._install_completed)
        self.worker.failed.connect(self._check_failed)
        self.worker.start()

    @Slot(object)
    def _install_completed(self, _result: object) -> None:
        self.notifications.notify(
            "MyOS-Update installiert",
            "Das Update ist bereit. Starte MyOS neu, um die neue Version zu verwenden.",
            "MyOS Update",
        )
