import re
import unicodedata
import weakref
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QDateTime, QSettings, QUrl, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWebEngineWidgets import QWebEngineView


HOME_HTML = """
<!doctype html>
<html lang="de">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NeonVeil – Startseite</title>
<style>
  body { margin: 12vh auto; max-width: 42rem; padding: 0 1.5rem;
         color: #20333b; background: #f0f4f5;
         font: 18px system-ui, sans-serif; }
  h1 { color: #14596a; } p { line-height: 1.6; }
</style>
<h1>NeonVeil Browser</h1>
<p>Dies ist deine lokale Startseite. Für Webseiten benötigst du eine
Internetverbindung. Der Browser selbst startet ohne Netzwerkverbindung.</p>
</html>
"""

_DOWNLOAD_HANDLER_CONNECTED = False
_BROWSER_WINDOWS_BY_PAGE: weakref.WeakKeyDictionary[object, weakref.ReferenceType] = (
    weakref.WeakKeyDictionary()
)


def _dispatch_download_request(request: QWebEngineDownloadRequest) -> None:
    page = request.page()
    window_ref = _BROWSER_WINDOWS_BY_PAGE.get(page)
    window = window_ref() if window_ref is not None else None
    if window is None:
        request.cancel()
        return
    window._handle_download(request)


@dataclass
class DownloadRecord:
    url: str
    name: str
    path: str
    state: str
    status: str
    received_bytes: int = 0
    total_bytes: int = -1
    timestamp: str = ""
    terminal_emitted: bool = False

    @property
    def progress_text(self) -> str:
        received = format_bytes(self.received_bytes)
        if self.total_bytes >= 0:
            return f"{received} von {format_bytes(self.total_bytes)}"
        return f"{received} heruntergeladen"

    def display_text(self) -> str:
        return f"{self.name} · {self.status} · {self.progress_text}"


def format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


class BrowserWindow(QMainWindow):
    image_downloaded = Signal(str)
    download_finished = Signal(str, str)
    open_download_requested = Signal(str)
    open_download_folder_requested = Signal(str)

    def __init__(self, download_settings: QSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Browser")
        self.setMinimumSize(560, 380)
        self.resize(920, 660)
        self.downloads: list[DownloadRecord] = []
        self._active_downloads: dict[
            int,
            tuple[
                QWebEngineDownloadRequest,
                DownloadRecord,
                tuple[object, ...],
            ],
        ] = {}
        self._download_list: QListWidget | None = None
        self.download_settings = (
            download_settings
            if download_settings is not None
            else QSettings("neonveil", "neonveil")
        )
        self._history_load_error = ""
        self._history_needs_persist = False
        self._load_download_history()

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        navigation = QHBoxLayout()
        self.view = QWebEngineView()
        profile = self.view.page().profile()
        global _DOWNLOAD_HANDLER_CONNECTED
        if not _DOWNLOAD_HANDLER_CONNECTED:
            profile.downloadRequested.connect(_dispatch_download_request)
            _DOWNLOAD_HANDLER_CONNECTED = True
        _BROWSER_WINDOWS_BY_PAGE[self.view.page()] = weakref.ref(self)

        self.back_button = QPushButton("Zurück")
        self.back_button.clicked.connect(self.view.back)
        self.forward_button = QPushButton("Vor")
        self.forward_button.clicked.connect(self.view.forward)
        self.reload_button = QPushButton("Neu laden")
        self.reload_button.clicked.connect(self.view.reload)
        self.home_button = QPushButton("Startseite")
        self.home_button.clicked.connect(self.go_home)
        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("Adresse eingeben, z. B. https://example.org")
        self.address_bar.returnPressed.connect(self.navigate)
        self.go_button = QPushButton("Öffnen")
        self.go_button.clicked.connect(self.navigate)
        self.downloads_button = QPushButton("Downloads")
        self.downloads_button.clicked.connect(self.show_downloads)

        for widget in (
            self.back_button,
            self.forward_button,
            self.reload_button,
            self.home_button,
            self.address_bar,
            self.go_button,
            self.downloads_button,
        ):
            navigation.addWidget(widget)

        self.view.urlChanged.connect(self._update_address)
        self.view.titleChanged.connect(self._update_title)
        self.view.loadFinished.connect(self._update_navigation)
        self.view.loadProgress.connect(self._update_progress)
        layout.addLayout(navigation)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(content)
        self.go_home()
        if self._history_needs_persist:
            self._persist_download_history()
        if self._history_load_error:
            self.statusBar().showMessage(self._history_load_error, 10000)

    def closeEvent(self, event: QCloseEvent) -> None:
        _BROWSER_WINDOWS_BY_PAGE.pop(self.view.page(), None)
        for request, record, callbacks in tuple(self._active_downloads.values()):
            record.state = "cancelled"
            record.status = "Abgebrochen: Browser geschlossen"
            record.terminal_emitted = True
            for signal, callback in zip(
                (
                    request.stateChanged,
                    request.receivedBytesChanged,
                    request.totalBytesChanged,
                    request.interruptReasonChanged,
                ),
                callbacks,
            ):
                signal.disconnect(callback)
            request.cancel()
            self.download_finished.emit(record.path, record.state)
        if self._active_downloads:
            self._active_downloads.clear()
            self._persist_download_history()
        super().closeEvent(event)

    @staticmethod
    def normalize_address(text: str) -> QUrl:
        address = text.strip()
        if not address:
            return QUrl()
        typed_url = QUrl(address)
        scheme = typed_url.scheme().lower()
        if scheme and scheme not in {"http", "https"}:
            host_port = address.partition(":")
            if not host_port[2].split("/", 1)[0].isdigit():
                return QUrl()
            address = f"https://{address}"
        if "://" not in address:
            address = f"https://{address}"
        url = QUrl.fromUserInput(address)
        if url.scheme().lower() not in {"http", "https"}:
            return QUrl()
        return url

    def navigate(self) -> None:
        url = self.normalize_address(self.address_bar.text())
        if not url.isValid() or url.isEmpty():
            self.address_bar.setStyleSheet("border: 1px solid #b53c3c;")
            self.statusBar().showMessage("Ungültige Adresse. Erlaubt sind HTTP- und HTTPS-Webseiten.", 8000)
            return
        self.address_bar.setStyleSheet("")
        self.view.setUrl(url)

    def navigate_to_url(self, address: str) -> bool:
        url = self.normalize_address(address)
        if not url.isValid() or url.isEmpty():
            self.statusBar().showMessage(f"Ungültige Webadresse: {address}", 8000)
            return False
        self.address_bar.setStyleSheet("")
        self.view.setUrl(url)
        return True

    def go_home(self) -> None:
        self.address_bar.clear()
        self.view.setHtml(HOME_HTML, QUrl("https://neonveil.local/"))

    def _update_address(self, url: QUrl) -> None:
        if url.host() == "neonveil.local":
            self.address_bar.clear()
        else:
            self.address_bar.setText(url.toString())
        self._update_navigation()

    def _update_title(self, title: str) -> None:
        self.setWindowTitle(f"{title} — Browser" if title else "Browser")

    def _update_navigation(self, success: bool = True) -> None:
        history = self.view.history()
        self.back_button.setEnabled(history.canGoBack())
        self.forward_button.setEnabled(history.canGoForward())
        if not success:
            self.statusBar().showMessage(
                "Seite konnte nicht geladen werden. Prüfe Adresse und Internetverbindung.",
                8000,
            )

    def _update_progress(self, progress: int) -> None:
        if progress < 100:
            self.statusBar().showMessage(f"Seite wird geladen … {progress}%")
        else:
            self.statusBar().clearMessage()

    @staticmethod
    def default_download_directory() -> Path:
        return Path.home() / "Downloads"

    @staticmethod
    def safe_download_name(name: str) -> bool:
        return (
            bool(name)
            and name not in {".", ".."}
            and "/" not in name
            and "\\" not in name
            and not any(unicodedata.category(character) == "Cc" for character in name)
            and Path(name).name == name
        )

    @staticmethod
    def available_download_path(directory: Path, filename: str) -> Path:
        candidate = directory / filename
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        name = Path(filename)
        stem = name.stem or name.name
        suffix = name.suffix
        index = 1
        while True:
            candidate = directory / f"{stem} ({index}){suffix}"
            if not candidate.exists() and not candidate.is_symlink():
                return candidate
            index += 1

    @staticmethod
    def _state_value(state: object) -> int:
        return int(state.value) if hasattr(state, "value") else int(state)

    def _load_download_history(self) -> None:
        payload = self.download_settings.value("browser/download-history", "[]", type=str)
        try:
            entries = json.loads(payload)
        except json.JSONDecodeError as error:
            self._history_load_error = f"Download-Liste kann nicht gelesen werden: {error}"
            return
        if not isinstance(entries, list):
            self._history_load_error = "Download-Liste hat ein ungültiges Format."
            return
        for entry in entries[-100:]:
            if not isinstance(entry, dict):
                self._history_load_error = "Ein Eintrag in der Download-Liste ist ungültig."
                self.downloads.clear()
                return
            try:
                record = DownloadRecord(
                    url=str(entry["url"]),
                    name=str(entry["name"]),
                    path=str(entry["path"]),
                    state=str(entry["state"]),
                    status=str(entry["status"]),
                    received_bytes=int(entry["received_bytes"]),
                    total_bytes=int(entry["total_bytes"]),
                    timestamp=str(entry["timestamp"]),
                    terminal_emitted=bool(entry.get("terminal_emitted", False)),
                )
            except (KeyError, TypeError, ValueError) as error:
                self._history_load_error = f"Download-Liste enthält einen ungültigen Eintrag: {error}"
                self.downloads.clear()
                return
            if record.state == "in_progress":
                record.state = "interrupted"
                record.status = "Unterbrochen: Browser wurde beendet"
                record.terminal_emitted = True
                self._history_needs_persist = True
            self.downloads.append(record)

    def _persist_download_history(self) -> None:
        self.downloads = self.downloads[-100:]
        payload = json.dumps(
            [asdict(record) for record in self.downloads],
            ensure_ascii=False,
        )
        self.download_settings.setValue("browser/download-history", payload)
        self.download_settings.sync()
        if self.download_settings.status() != QSettings.Status.NoError:
            message = "Download-Liste konnte nicht dauerhaft gespeichert werden."
            self.statusBar().showMessage(message, 10000)
            QMessageBox.critical(self, "Download-Liste nicht gespeichert", message)

    def _handle_download(self, request: QWebEngineDownloadRequest) -> None:
        if request is None:
            return
        filename = request.downloadFileName()
        record = DownloadRecord(
            url=request.url().toString(),
            name=filename,
            path="",
            state="requested",
            status="Wartet auf Speicherort",
            timestamp=QDateTime.currentDateTime().toString("dd.MM.yyyy HH:mm:ss"),
        )
        self.downloads.append(record)
        if not self.safe_download_name(filename):
            record.state = "rejected"
            record.status = "Abgelehnt: unsicherer Dateiname"
            self._persist_download_history()
            request.cancel()
            self.statusBar().showMessage(record.status, 8000)
            QMessageBox.warning(
                self,
                "Download abgelehnt",
                "Der Dateiname enthält einen Pfadtrenner oder ein ungültiges Steuerzeichen.",
            )
            self._refresh_download_list()
            self.download_finished.emit("", record.state)
            return

        download_directory = self.default_download_directory()
        try:
            download_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            record.state = "failed"
            record.status = f"Fehlgeschlagen: Download-Ordner kann nicht erstellt werden ({error})"
            self._persist_download_history()
            request.cancel()
            self.statusBar().showMessage(record.status, 10000)
            QMessageBox.critical(self, "Download-Ordner nicht verfügbar", record.status)
            self._refresh_download_list()
            self.download_finished.emit("", record.state)
            return

        requested_path = self.available_download_path(download_directory, filename)
        selected_path_text, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Download speichern",
            str(requested_path),
            "Alle Dateien (*)",
        )
        if not selected_path_text:
            record.state = "cancelled"
            record.status = "Abgebrochen"
            self._persist_download_history()
            request.cancel()
            self.statusBar().showMessage(f"Download abgebrochen: {filename}", 5000)
            self._refresh_download_list()
            self.download_finished.emit("", record.state)
            return

        selected_path = Path(selected_path_text).expanduser().absolute()
        if not self.safe_download_name(selected_path.name):
            record.state = "rejected"
            record.status = "Abgelehnt: ungültiger Zieldateiname"
            self._persist_download_history()
            request.cancel()
            self.statusBar().showMessage(record.status, 8000)
            QMessageBox.warning(self, "Ungültiger Dateiname", record.status)
            self._refresh_download_list()
            self.download_finished.emit("", record.state)
            return
        try:
            selected_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            record.state = "failed"
            record.status = f"Fehlgeschlagen: Zielordner kann nicht erstellt werden ({error})"
            self._persist_download_history()
            request.cancel()
            self.statusBar().showMessage(record.status, 10000)
            QMessageBox.critical(self, "Download-Ordner nicht verfügbar", record.status)
            self._refresh_download_list()
            self.download_finished.emit("", record.state)
            return
        if selected_path.exists() or selected_path.is_symlink():
            selected_path = self.available_download_path(selected_path.parent, selected_path.name)
            self.statusBar().showMessage(
                f"Datei existierte bereits; gespeichert wird als {selected_path.name}.",
                8000,
            )

        record.name = selected_path.name
        record.path = str(selected_path)
        record.state = "in_progress"
        record.status = "Download läuft"
        request.setDownloadDirectory(str(selected_path.parent))
        request.setDownloadFileName(selected_path.name)
        callbacks = (
            lambda state: self._update_download_record(request, record, state),
            lambda: self._update_download_bytes(request, record),
            lambda: self._update_download_bytes(request, record),
            lambda: self._update_download_record(request, record, request.state()),
        )
        request.stateChanged.connect(callbacks[0])
        request.receivedBytesChanged.connect(callbacks[1])
        request.totalBytesChanged.connect(callbacks[2])
        request.interruptReasonChanged.connect(callbacks[3])
        self._active_downloads[id(record)] = (request, record, callbacks)
        self._persist_download_history()
        self._refresh_download_list()
        request.accept()
        self.statusBar().showMessage(f"Download läuft: {record.name}")

    def _update_download_bytes(
        self,
        request: QWebEngineDownloadRequest,
        record: DownloadRecord,
    ) -> None:
        if record.terminal_emitted:
            return
        record.received_bytes = int(request.receivedBytes())
        record.total_bytes = int(request.totalBytes())
        if record.state == "in_progress":
            record.status = f"Download läuft · {record.progress_text}"
        self.statusBar().showMessage(record.display_text())
        self._refresh_download_list()

    def _update_download_record(
        self,
        request: QWebEngineDownloadRequest,
        record: DownloadRecord,
        state: object,
    ) -> None:
        if record.terminal_emitted:
            return
        record.received_bytes = int(request.receivedBytes())
        record.total_bytes = int(request.totalBytes())
        state_value = self._state_value(state)
        terminal_states = {
            QWebEngineDownloadRequest.DownloadState.DownloadCompleted.value,
            QWebEngineDownloadRequest.DownloadState.DownloadCancelled.value,
            QWebEngineDownloadRequest.DownloadState.DownloadInterrupted.value,
        }
        if state_value == QWebEngineDownloadRequest.DownloadState.DownloadRequested.value:
            record.state = "requested"
            record.status = "Wartet"
        elif state_value == QWebEngineDownloadRequest.DownloadState.DownloadInProgress.value:
            record.state = "in_progress"
            record.status = f"Download läuft · {record.progress_text}"
        elif state_value == QWebEngineDownloadRequest.DownloadState.DownloadCompleted.value:
            if record.path and Path(record.path).is_file():
                record.state = "completed"
                record.status = "Abgeschlossen"
                self.statusBar().showMessage(
                    f"Download abgeschlossen: {record.name} · {record.progress_text}",
                    8000,
                )
                self.download_finished.emit(record.path, record.state)
                if Path(record.path).suffix.lower() in {".png", ".jpg", ".jpeg"}:
                    self.image_downloaded.emit(record.path)
            else:
                record.state = "failed"
                record.status = "Fehlgeschlagen: gespeicherte Datei nicht gefunden"
                self.statusBar().showMessage(record.status, 10000)
                self.download_finished.emit(record.path, record.state)
        elif state_value == QWebEngineDownloadRequest.DownloadState.DownloadCancelled.value:
            record.state = "cancelled"
            record.status = "Abgebrochen"
            self.statusBar().showMessage(f"Download abgebrochen: {record.name}", 8000)
            self.download_finished.emit(record.path, record.state)
        elif state_value == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted.value:
            reason = request.interruptReasonString() or "Unbekannter Fehler"
            if request.interruptReason() == QWebEngineDownloadRequest.DownloadInterruptReason.UserCanceled:
                record.state = "cancelled"
                record.status = "Abgebrochen"
                self.statusBar().showMessage(f"Download abgebrochen: {record.name}", 8000)
            else:
                record.state = "interrupted"
                record.status = f"Unterbrochen: {reason}"
                self.statusBar().showMessage(record.status, 10000)
            self.download_finished.emit(record.path, record.state)
        if state_value in terminal_states:
            record.terminal_emitted = True
            self._active_downloads.pop(id(record), None)
        self._persist_download_history()
        self._refresh_download_list()

    def show_downloads(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Downloads")
        dialog.setMinimumSize(440, 300)
        layout = QVBoxLayout(dialog)
        self._download_list = QListWidget(dialog)
        self._download_list.itemDoubleClicked.connect(
            lambda _item: self._open_selected_download()
        )
        layout.addWidget(self._download_list, 1)
        buttons = QHBoxLayout()
        open_file_button = QPushButton("Datei öffnen")
        open_file_button.clicked.connect(self._open_selected_download)
        open_folder_button = QPushButton("Ordner öffnen")
        open_folder_button.clicked.connect(self._open_selected_download_folder)
        close_button = QPushButton("Schließen")
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(open_file_button)
        buttons.addWidget(open_folder_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        dialog.finished.connect(lambda: setattr(self, "_download_list", None))
        self._refresh_download_list()
        dialog.exec()

    def _refresh_download_list(self) -> None:
        if self._download_list is None:
            return
        self._download_list.clear()
        for record in reversed(self.downloads):
            item = QListWidgetItem(record.display_text())
            item.setData(256, record.path)
            self._download_list.addItem(item)

    def _selected_download_path(self) -> Path | None:
        if self._download_list is None or self._download_list.currentItem() is None:
            return None
        path = self._download_list.currentItem().data(256)
        return Path(path) if path else None

    def _open_selected_download(self) -> None:
        path = self._selected_download_path()
        if path is None or not path.is_file():
            QMessageBox.information(self, "Datei nicht verfügbar", "Dieser Download ist noch nicht abgeschlossen.")
            return
        self.open_download_requested.emit(str(path))

    def _open_selected_download_folder(self) -> None:
        path = self._selected_download_path()
        folder = path.parent if path is not None else self.default_download_directory()
        self.open_download_folder_requested.emit(str(folder))
