import os
import time
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QCoreApplication, QElapsedTimer, QEvent, QSettings, QUrl
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWidgets import QApplication

from apps.browser.app import BrowserWindow, _dispatch_download_request


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback) -> None:
        self.callbacks.remove(callback)

    def emit(self, *args) -> None:
        for callback in self.callbacks:
            callback(*args)


class FakeDownloadRequest:
    def __init__(self, name: str = "photo.png") -> None:
        self._name = name
        self._url = QUrl("https://example.org/" + name)
        self._state = QWebEngineDownloadRequest.DownloadState.DownloadRequested
        self._received = 0
        self._total = 100
        self._reason = "Network disconnected"
        self._interrupt_reason = (
            QWebEngineDownloadRequest.DownloadInterruptReason.NetworkDisconnected
        )
        self.accepted = False
        self.cancelled = False
        self.directory = ""
        self.saved_name = ""
        self.stateChanged = FakeSignal()
        self.receivedBytesChanged = FakeSignal()
        self.totalBytesChanged = FakeSignal()
        self.interruptReasonChanged = FakeSignal()

    def downloadFileName(self) -> str:
        return self._name

    def url(self) -> QUrl:
        return self._url

    def setDownloadDirectory(self, directory: str) -> None:
        self.directory = directory

    def setDownloadFileName(self, name: str) -> None:
        self.saved_name = name

    def accept(self) -> None:
        self.accepted = True

    def cancel(self) -> None:
        self.cancelled = True

    def receivedBytes(self) -> int:
        return self._received

    def totalBytes(self) -> int:
        return self._total

    def state(self):
        return self._state

    def interruptReasonString(self) -> str:
        return self._reason

    def interruptReason(self):
        return self._interrupt_reason

    def transition(
        self,
        state,
        received: int = 0,
        reason: str | None = None,
        interrupt_reason=None,
    ) -> None:
        self._state = state
        self._received = received
        if reason is not None:
            self._reason = reason
        if interrupt_reason is not None:
            self._interrupt_reason = interrupt_reason
        self.stateChanged.emit(state)


class BrowserDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.download_dir = Path(self.temp_dir.name) / "Downloads"
        self.settings_path = Path(self.temp_dir.name) / "browser-settings.ini"
        self.settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        self._closed_window_ids: set[int] = set()
        self.window = BrowserWindow(self.settings)
        self.addCleanup(self.close_window)

    def close_window(self, window=None) -> None:
        window = window or self.window
        if id(window) in self._closed_window_ids:
            return
        self._closed_window_ids.add(id(window))
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def begin_download(self, request: FakeDownloadRequest, target: Path) -> None:
        with patch.object(
            BrowserWindow, "default_download_directory", return_value=self.download_dir
        ), patch(
            "apps.browser.app.QFileDialog.getSaveFileName",
            return_value=(str(target), "Alle Dateien (*)"),
        ):
            self.window._handle_download(request)

    def test_download_is_accepted_with_chosen_destination_and_safe_name(self) -> None:
        request = FakeDownloadRequest("photo.png")
        self.download_dir.mkdir(parents=True)
        target = self.download_dir / "photo.png"
        with patch.object(
            BrowserWindow, "default_download_directory", return_value=self.download_dir
        ), patch(
            "apps.browser.app.QFileDialog.getSaveFileName",
            return_value=(str(target), "Alle Dateien (*)"),
        ) as save_dialog:
            self.window._handle_download(request)

        self.assertTrue(request.accepted)
        self.assertEqual(request.directory, str(target.parent))
        self.assertEqual(request.saved_name, "photo.png")
        self.assertEqual(self.window.downloads[0].path, str(target))
        self.assertEqual(self.window.downloads[0].state, "in_progress")
        self.assertTrue(target.parent.is_dir())
        self.assertEqual(save_dialog.call_args.args[2], str(target))

    def test_browser_loads_local_website_and_downloads_a_file(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/report.txt":
                    payload = b"real local browser download"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header(
                        "Content-Disposition",
                        'attachment; filename="report.txt"',
                    )
                else:
                    payload = (
                        b'<html><title>neonveil local web test</title>'
                        b'<a id="download" href="/report.txt">Download</a></html>'
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(server_thread.join, timeout=2)

        url = f"http://127.0.0.1:{server.server_port}/"
        self.assertTrue(self.window.navigate_to_url(url))
        timer = QElapsedTimer()
        timer.start()
        while (
            self.window.windowTitle() != "neonveil local web test — Browser"
            and timer.elapsed() < 5000
        ):
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(
            self.window.windowTitle(),
            "neonveil local web test — Browser",
        )

        target = self.download_dir / "report.txt"
        with patch.object(
            BrowserWindow,
            "default_download_directory",
            return_value=self.download_dir,
        ), patch(
            "apps.browser.app.QFileDialog.getSaveFileName",
            return_value=(str(target), "Alle Dateien (*)"),
        ):
            self.window.view.page().runJavaScript(
                "document.getElementById('download').click()"
            )
            timer.restart()
            while (
                not self.window.downloads
                or self.window.downloads[-1].state
                not in {"completed", "failed", "cancelled", "interrupted"}
            ) and timer.elapsed() < 10000:
                self.app.processEvents()
                time.sleep(0.01)

        self.assertEqual(self.window.downloads[-1].state, "completed")
        self.assertEqual(target.read_bytes(), b"real local browser download")

    def test_profile_dispatch_routes_request_to_owning_browser_page(self) -> None:
        class PageRequest:
            def page(inner_self):
                return self.window.view.page()

            def cancel(inner_self) -> None:
                raise AssertionError("request should be routed to its browser")

        with patch.object(self.window, "_handle_download") as handle_download:
            request = PageRequest()
            _dispatch_download_request(request)
        handle_download.assert_called_once_with(request)

    def test_unwritable_download_directory_is_reported_and_not_accepted(self) -> None:
        request = FakeDownloadRequest()
        with patch.object(
            BrowserWindow, "default_download_directory", return_value=self.download_dir
        ), patch.object(Path, "mkdir", side_effect=PermissionError("permission denied")), patch(
            "apps.browser.app.QMessageBox.critical"
        ) as show_error, patch(
            "apps.browser.app.QFileDialog.getSaveFileName"
        ) as save_dialog:
            self.window._handle_download(request)

        self.assertFalse(request.accepted)
        self.assertTrue(request.cancelled)
        self.assertEqual(self.window.downloads[-1].state, "failed")
        self.assertIn("permission denied", self.window.downloads[-1].status)
        show_error.assert_called_once()
        save_dialog.assert_not_called()

    def test_existing_filename_gets_collision_safe_name_without_overwrite(self) -> None:
        self.download_dir.mkdir(parents=True)
        original = self.download_dir / "photo.png"
        original.write_bytes(b"old")
        suggested = BrowserWindow.available_download_path(self.download_dir, "photo.png")
        self.assertEqual(suggested.name, "photo (1).png")
        request = FakeDownloadRequest("photo.png")
        self.begin_download(request, suggested)

        self.assertEqual(request.saved_name, "photo (1).png")
        self.assertEqual(original.read_bytes(), b"old")

    def test_dangling_symlink_is_treated_as_a_collision(self) -> None:
        self.download_dir.mkdir(parents=True)
        link = self.download_dir / "photo.png"
        link.symlink_to("missing-target")

        self.assertEqual(
            BrowserWindow.available_download_path(self.download_dir, "photo.png").name,
            "photo (1).png",
        )

    def test_unsafe_names_are_rejected_without_opening_a_save_dialog(self) -> None:
        for unsafe in ("../photo.png", "dir\\photo.png", "bad\nname.js", "bad\u0085name.js"):
            with self.subTest(unsafe=unsafe):
                request = FakeDownloadRequest(unsafe)
                with patch.object(
                    BrowserWindow,
                    "default_download_directory",
                    return_value=self.download_dir,
                ), patch("apps.browser.app.QFileDialog.getSaveFileName") as dialog, patch(
                    "apps.browser.app.QMessageBox.warning"
                ):
                    self.window._handle_download(request)
                self.assertFalse(request.accepted)
                self.assertTrue(request.cancelled)
                self.assertEqual(self.window.downloads[-1].state, "rejected")
                dialog.assert_not_called()

    def test_user_cancellation_is_retained_as_a_separate_record(self) -> None:
        request = FakeDownloadRequest()
        with patch.object(
            BrowserWindow, "default_download_directory", return_value=self.download_dir
        ), patch("apps.browser.app.QFileDialog.getSaveFileName", return_value=("", "")):
            self.window._handle_download(request)

        record = self.window.downloads[-1]
        self.assertTrue(request.cancelled)
        self.assertEqual(record.state, "cancelled")
        self.assertEqual(record.status, "Abgebrochen")

    def test_byte_progress_and_success_emit_image_handoff_and_completion(self) -> None:
        request = FakeDownloadRequest()
        target = self.download_dir / "photo.png"
        self.begin_download(request, target)
        record = self.window.downloads[-1]
        request._received = 42
        request.receivedBytesChanged.emit()
        self.assertEqual(record.received_bytes, 42)
        self.assertIn("42 B von 100 B", record.display_text())

        target.write_bytes(b"image")
        opened_images = []
        finished = []
        self.window.image_downloaded.connect(opened_images.append)
        self.window.download_finished.connect(lambda path, state: finished.append((path, state)))
        request.transition(
            QWebEngineDownloadRequest.DownloadState.DownloadCompleted,
            received=100,
        )

        self.assertEqual(record.state, "completed")
        self.assertEqual(opened_images, [str(target)])
        self.assertEqual(finished, [(str(target), "completed")])

    def test_completed_request_without_a_file_is_reported_as_failed(self) -> None:
        request = FakeDownloadRequest("document.txt")
        target = self.download_dir / "document.txt"
        self.begin_download(request, target)
        request.transition(QWebEngineDownloadRequest.DownloadState.DownloadCompleted)

        self.assertEqual(self.window.downloads[-1].state, "failed")
        self.assertIn("nicht gefunden", self.window.downloads[-1].status)

    def test_interrupted_download_is_not_reported_as_success_or_user_cancel(self) -> None:
        request = FakeDownloadRequest("document.txt")
        self.begin_download(request, self.download_dir / "document.txt")
        request.transition(
            QWebEngineDownloadRequest.DownloadState.DownloadInterrupted,
            received=12,
            reason="Network disconnected",
        )

        record = self.window.downloads[-1]
        self.assertEqual(record.state, "interrupted")
        self.assertIn("Network disconnected", record.status)

    def test_user_cancel_interrupt_reason_maps_to_cancelled_state(self) -> None:
        request = FakeDownloadRequest("document.txt")
        self.begin_download(request, self.download_dir / "document.txt")
        request.transition(
            QWebEngineDownloadRequest.DownloadState.DownloadInterrupted,
            reason="User canceled",
            interrupt_reason=QWebEngineDownloadRequest.DownloadInterruptReason.UserCanceled,
        )

        self.assertEqual(self.window.downloads[-1].state, "cancelled")
        self.assertEqual(self.window.downloads[-1].status, "Abgebrochen")

    def test_qt_cancelled_download_state_is_kept_distinct_from_interruption(self) -> None:
        request = FakeDownloadRequest("document.txt")
        self.begin_download(request, self.download_dir / "document.txt")
        request.transition(
            QWebEngineDownloadRequest.DownloadState.DownloadCancelled,
            received=12,
        )

        record = self.window.downloads[-1]
        self.assertEqual(record.state, "cancelled")
        self.assertEqual(record.status, "Abgebrochen")

    def test_download_finished_signal_does_not_claim_success_for_interruptions(self) -> None:
        request = FakeDownloadRequest("document.txt")
        self.begin_download(request, self.download_dir / "document.txt")
        finished = []
        self.window.download_finished.connect(lambda path, state: finished.append((path, state)))
        request.transition(
            QWebEngineDownloadRequest.DownloadState.DownloadInterrupted,
            reason="Network timeout",
        )
        request.interruptReasonChanged.emit()
        self.assertEqual(finished, [(str(self.download_dir / "document.txt"), "interrupted")])

    def test_closing_browser_cancels_and_persists_active_download(self) -> None:
        request = FakeDownloadRequest("document.txt")
        target = self.download_dir / "document.txt"
        self.begin_download(request, target)
        record = self.window.downloads[-1]

        self.close_window()

        self.assertTrue(request.cancelled)
        self.assertEqual(record.state, "cancelled")
        self.assertEqual(record.status, "Abgebrochen: Browser geschlossen")
        self.assertFalse(self.window._active_downloads)
        reloaded = BrowserWindow(
            QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        )
        self.addCleanup(self.close_window, reloaded)
        self.assertEqual(reloaded.downloads[-1].state, "cancelled")
        self.assertEqual(
            reloaded.downloads[-1].status,
            "Abgebrochen: Browser geschlossen",
        )

    def test_failure_cancellation_and_interruption_records_survive_browser_restart(self) -> None:
        failed = FakeDownloadRequest("missing.txt")
        self.begin_download(failed, self.download_dir / "missing.txt")
        failed.transition(QWebEngineDownloadRequest.DownloadState.DownloadCompleted)

        cancelled = FakeDownloadRequest("cancelled.txt")
        self.begin_download(cancelled, self.download_dir / "cancelled.txt")
        cancelled.transition(QWebEngineDownloadRequest.DownloadState.DownloadCancelled)

        interrupted = FakeDownloadRequest("interrupted.txt")
        self.begin_download(interrupted, self.download_dir / "interrupted.txt")
        interrupted.transition(
            QWebEngineDownloadRequest.DownloadState.DownloadInterrupted,
            reason="Network timeout",
        )

        settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        reloaded = BrowserWindow(settings)
        self.addCleanup(self.close_window, reloaded)
        self.assertEqual(
            [record.state for record in reloaded.downloads],
            ["failed", "cancelled", "interrupted"],
        )
        self.assertIn("nicht gefunden", reloaded.downloads[0].status)
        self.assertEqual(reloaded.downloads[1].status, "Abgebrochen")
        self.assertIn("Network timeout", reloaded.downloads[2].status)


if __name__ == "__main__":
    unittest.main()
