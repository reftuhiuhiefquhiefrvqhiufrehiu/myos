from __future__ import annotations

import subprocess
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QProcess, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop.version import APP_VERSION
from update_manager import CHANNELS, UpdateError, UpdateManager, UpdateRelease


CHANNEL_LABELS = {
    "stable": "Stable",
    "beta": "Beta",
    "developer": "Developer",
}

LOGGER = logging.getLogger("myos.update.ui")


@dataclass(frozen=True)
class UpdateActionResult:
    action: str
    process: subprocess.CompletedProcess[str]


class UpdateWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.operation = operation

    def run(self) -> None:
        try:
            self.completed.emit(self.operation())
        except UpdateError as error:
            self.failed.emit(error.user_message)
        except (OSError, subprocess.SubprocessError) as error:
            self.failed.emit(f"Der Updatevorgang ist fehlgeschlagen: {error}")
        except Exception:
            LOGGER.exception("Unerwarteter Fehler im Update-Worker.")
            self.failed.emit(
                "Der Updatevorgang ist fehlgeschlagen. Technische Details stehen im Update-Log."
            )


class UpdateManagerWindow(QMainWindow):
    def __init__(self, manager: UpdateManager | None = None) -> None:
        super().__init__()
        self.setWindowTitle("MyOS Update Manager")
        self.setMinimumSize(360, 260)
        self.resize(620, 570)
        self.manager = manager or UpdateManager(current_version=APP_VERSION)
        self.release: UpdateRelease | None = None
        self.worker: UpdateWorker | None = None
        self._install_after_download = False

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        heading = QLabel("MyOS Update Manager")
        heading.setObjectName("heading")
        subtitle = QLabel("Updates prüfen, sicher herunterladen und verwalten.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(heading)
        layout.addWidget(subtitle)

        info_box = QGroupBox("Systemversion")
        info_layout = QGridLayout(info_box)
        info_layout.addWidget(QLabel("Installierte Version"), 0, 0)
        self.installed_version = QLabel(self.manager.current_version)
        info_layout.addWidget(self.installed_version, 0, 1)
        info_layout.addWidget(QLabel("Update-Kanal"), 1, 0)
        self.channel_combo = QComboBox()
        for channel in CHANNELS:
            self.channel_combo.addItem(CHANNEL_LABELS[channel], channel)
        self.channel_combo.setCurrentIndex(
            max(0, self.channel_combo.findData(self.manager.channel))
        )
        self.channel_combo.currentIndexChanged.connect(self._change_channel)
        info_layout.addWidget(self.channel_combo, 1, 1)
        layout.addWidget(info_box)

        release_box = QGroupBox("Verfügbare Version")
        release_layout = QGridLayout(release_box)
        release_layout.addWidget(QLabel("Version"), 0, 0)
        self.available_version = QLabel("Noch nicht geprüft")
        release_layout.addWidget(self.available_version, 0, 1)
        release_layout.addWidget(QLabel("Veröffentlicht"), 1, 0)
        self.release_date = QLabel("—")
        release_layout.addWidget(self.release_date, 1, 1)
        release_layout.addWidget(QLabel("Größe"), 2, 0)
        self.release_size = QLabel("—")
        release_layout.addWidget(self.release_size, 2, 1)
        self.changelog = QTextEdit()
        self.changelog.setReadOnly(True)
        self.changelog.setPlaceholderText("Der Changelog wird nach der Updateprüfung angezeigt.")
        self.changelog.setMaximumHeight(130)
        release_layout.addWidget(self.changelog, 3, 0, 1, 2)
        layout.addWidget(release_box)

        preferences_box = QGroupBox("Automatische Updates")
        preferences_layout = QVBoxLayout(preferences_box)
        self.auto_check = QCheckBox("Automatisch nach Updates suchen")
        self.auto_download = QCheckBox("Verfügbare Updates automatisch herunterladen")
        self.auto_install = QCheckBox(
            "Nach Hinweis automatisch installieren (Neustart bleibt manuell)"
        )
        self.auto_check.setChecked(self.manager.preferences["auto_check"])
        self.auto_download.setChecked(self.manager.preferences["auto_download"])
        self.auto_install.setChecked(self.manager.preferences["auto_install"])
        self.auto_check.toggled.connect(self._save_auto_preferences)
        self.auto_download.toggled.connect(self._save_auto_preferences)
        self.auto_install.toggled.connect(self._save_auto_preferences)
        preferences_layout.addWidget(self.auto_check)
        preferences_layout.addWidget(self.auto_download)
        preferences_layout.addWidget(self.auto_install)
        warning = QLabel(
            "Automatische Installation ist standardmäßig aus. Bei Aktivierung "
            "wird vor dem Installieren eine Systembenachrichtigung angezeigt."
        )
        warning.setWordWrap(True)
        preferences_layout.addWidget(warning)
        layout.addWidget(preferences_box)

        actions = QGridLayout()
        self.check_button = QPushButton("Nach Updates suchen")
        self.check_button.clicked.connect(self.check_for_updates)
        self.download_button = QPushButton("Update herunterladen")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.download_update)
        self.install_button = QPushButton("Update installieren")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install_update)
        self.reboot_button = QPushButton("Neustart")
        self.reboot_button.setEnabled(False)
        self.reboot_button.clicked.connect(self.reboot)
        actions.addWidget(self.check_button, 0, 0)
        actions.addWidget(self.download_button, 0, 1)
        actions.addWidget(self.install_button, 1, 0)
        actions.addWidget(self.reboot_button, 1, 1)
        layout.addLayout(actions)

        status_box = QGroupBox("Letztes Update")
        status_layout = QVBoxLayout(status_box)
        self.status_label = QLabel(self.manager.status().get("message", "Noch kein Updatevorgang."))
        self.status_label.setWordWrap(True)
        self.rollback_button = QPushButton("Vorherige Version wiederherstellen…")
        self.rollback_button.setEnabled(self._has_previous_version())
        self.rollback_button.clicked.connect(self.rollback)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.rollback_button, 0)
        layout.addWidget(status_box)

        history_box = QGroupBox("Update-Historie")
        history_layout = QVBoxLayout(history_box)
        self.history_list = QListWidget()
        for entry in reversed(self.manager.history()):
            self.history_list.addItem(
                f"{entry['version']} · {CHANNEL_LABELS.get(entry['channel'], entry['channel'])}"
                f" · {entry['status']} · {entry['date']}"
            )
        if self.history_list.count() == 0:
            self.history_list.addItem("Noch keine Updates installiert.")
        history_layout.addWidget(self.history_list)
        layout.addWidget(history_box)
        self.scroll_area.setWidget(content)
        self.setCentralWidget(self.scroll_area)

    def _has_previous_version(self) -> bool:
        releases = self.manager.system_root / "releases"
        try:
            return any(
                path.is_dir()
                and not path.is_symlink()
                and path.name != self.manager.current_version
                for path in releases.iterdir()
            )
        except OSError:
            return False

    def _change_channel(self, _index: int) -> None:
        selected = self.channel_combo.currentData()
        if selected == self.manager.channel:
            return
        if selected != "stable":
            answer = QMessageBox.warning(
                self,
                "Update-Kanal wechseln",
                f"{CHANNEL_LABELS[selected]}-Versionen können Fehler enthalten. Möchtest du den Kanal wirklich wechseln?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.channel_combo.blockSignals(True)
                self.channel_combo.setCurrentIndex(
                    max(0, self.channel_combo.findData(self.manager.channel))
                )
                self.channel_combo.blockSignals(False)
                return
        self.manager.save_preferences(channel=selected)
        self.release = None
        self._clear_release()
        self.status_label.setText(f"Update-Kanal: {CHANNEL_LABELS[selected]}")

    def _save_auto_preferences(self, _checked: bool = False) -> None:
        self.manager.save_preferences(
            auto_check=self.auto_check.isChecked(),
            auto_download=self.auto_download.isChecked(),
            auto_install=self.auto_install.isChecked(),
        )

    def _clear_release(self) -> None:
        self.available_version.setText("Noch nicht geprüft")
        self.release_date.setText("—")
        self.release_size.setText("—")
        self.changelog.clear()
        self.download_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.reboot_button.setEnabled(False)

    def _start_worker(self, operation: Callable[[], object]) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self._set_busy(True)
        self.worker = UpdateWorker(operation)
        self.worker.completed.connect(self._worker_completed)
        self.worker.failed.connect(self._worker_failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _worker_finished(self) -> None:
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.check_button.setEnabled(not busy)
        self.channel_combo.setEnabled(not busy)
        self.download_button.setEnabled(not busy and self.release is not None)

    def check_for_updates(self) -> None:
        self.status_label.setText("Suche nach Updates…")

        def operation() -> tuple[UpdateRelease | None, bool]:
            release = self.manager.check_for_updates()
            downloaded = False
            if release is not None and self.manager.preferences["auto_download"]:
                self.manager.download(release)
                downloaded = True
            return release, downloaded

        self._start_worker(operation)

    def download_update(self) -> None:
        if self.release is None:
            return
        release = self.release
        self.status_label.setText("Update wird heruntergeladen und geprüft…")
        self._start_worker(lambda: self.manager.download(release))

    def _worker_completed(self, result: object) -> None:
        self.status_label.setText(self.manager.status().get("message", "Updatevorgang abgeschlossen."))
        if isinstance(result, tuple) and len(result) == 2:
            release, downloaded = result
            if release is None:
                self.release = None
                self._clear_release()
                self.available_version.setText("MyOS ist auf dem neuesten Stand.")
                self.status_label.setText("MyOS ist auf dem neuesten Stand.")
                return
            self.release = release
            self._show_release(release)
            if downloaded:
                self.status_label.setText(f"Update {release.version} heruntergeladen und verifiziert.")
                self.install_button.setEnabled(True)
            else:
                self.download_button.setEnabled(True)
        elif isinstance(result, Path):
            self.status_label.setText(f"Update heruntergeladen und verifiziert: {result.name}")
            self.install_button.setEnabled(True)
            if self._install_after_download:
                self._install_after_download = False
                QTimer.singleShot(0, self.install_update)
        elif isinstance(result, UpdateActionResult):
            if result.action == "rollback":
                self.status_label.setText(
                    "Die vorherige Version wurde wiederhergestellt. Bitte starte MyOS neu."
                )
                self.reboot_button.setEnabled(True)
            else:
                self._handle_install_result(result.process)

    def _show_release(self, release: UpdateRelease) -> None:
        self.available_version.setText(f"MyOS {release.version}")
        self.release_date.setText(release.release_date)
        self.release_size.setText(self._format_size(release.size))
        self.changelog.setPlainText(release.changelog or "Für diese Version wurde kein Changelog angegeben.")
        self.download_button.setEnabled(not self.manager.downloaded_file(release).is_file())
        self.install_button.setEnabled(self.manager.downloaded_file(release).is_file())

    def show_release(self, release: UpdateRelease) -> None:
        self.release = release
        self._show_release(release)
        self.status_label.setText(
            f"MyOS {release.version} ist verfügbar. SHA-256 wird beim Download geprüft."
        )

    def install_release(self, release: UpdateRelease) -> None:
        self.show_release(release)
        if self.manager.downloaded_file(release).is_file():
            self.install_update()
            return
        self._install_after_download = True
        self.download_update()

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("Byte", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"

    def install_update(self) -> None:
        if self.release is None or not self.manager.downloaded_file(self.release).is_file():
            return
        answer = QMessageBox.warning(
            self,
            "MyOS-Update installieren",
            "Vor der Installation werden MyOS-Einstellungen und Profile gesichert. "
            "Persönliche Dateien bleiben unberührt. Das Update erfordert anschließend "
            "einen Neustart. Jetzt installieren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.status_label.setText("Warte auf Systemberechtigung und installiere das Update…")

        def operation() -> subprocess.CompletedProcess[str]:
            if not Path("/usr/bin/pkexec").is_file():
                raise UpdateError("Das Werkzeug für die Systemberechtigung ist nicht installiert.")
            try:
                result = subprocess.run(
                    ["/usr/bin/pkexec", "/usr/bin/myos-update", "install"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as error:
                raise UpdateError("Die Systemberechtigung konnte nicht angefordert werden.", str(error)) from error
            if result.returncode != 0:
                detail = result.stderr.strip()
                if result.returncode == 126:
                    raise UpdateError("Die Installation wurde nicht autorisiert.")
                raise UpdateError("Die Installation ist fehlgeschlagen. Die bisherige Version bleibt aktiv.", detail)
            return UpdateActionResult("rollback", result)

        self._start_worker(operation)

    def _handle_install_result(self, _result: subprocess.CompletedProcess[str]) -> None:
        self.status_label.setText(
            "Update installiert. Bitte starte MyOS neu, damit die neue Version aktiv wird."
        )
        self.install_button.setEnabled(False)
        self.reboot_button.setEnabled(True)
        self.rollback_button.setEnabled(True)

    def _worker_failed(self, message: str) -> None:
        self.manager.record_status("error", message)
        self.status_label.setText(message)

    def reboot(self) -> None:
        answer = QMessageBox.question(
            self,
            "MyOS neu starten",
            "Möchtest du das System jetzt neu starten?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not QProcessStartDetached.start("systemctl", ["reboot"]):
            self.status_label.setText("Der Neustart konnte nicht gestartet werden.")

    def rollback(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Vorherige Version wiederherstellen",
            "Die vorherige MyOS-Version wird aktiviert. Persönliche Dateien und "
            "Einstellungen bleiben erhalten. Ein Neustart ist erforderlich. Fortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def operation() -> subprocess.CompletedProcess[str]:
            if not Path("/usr/bin/pkexec").is_file():
                raise UpdateError("Das Werkzeug für die Systemberechtigung ist nicht installiert.")
            result = subprocess.run(
                ["/usr/bin/pkexec", "/usr/bin/myos-update", "rollback"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise UpdateError("Die vorherige Version konnte nicht wiederhergestellt werden.", result.stderr)
            return UpdateActionResult("install", result)

        self._start_worker(operation)


class QProcessStartDetached:
    @staticmethod
    def start(program: str, arguments: list[str]) -> bool:
        started, _process_id = QProcess.startDetached(program, arguments)
        return started
