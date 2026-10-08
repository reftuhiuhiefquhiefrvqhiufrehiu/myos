import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QElapsedTimer, QTime, QUrl
from PySide6.QtWidgets import QApplication

from apps.browser.app import BrowserWindow
from apps.terminal.app import TerminalWindow
from apps.time_manager.app import TimeManagerWindow


class PhaseFiveApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def wait_for_ui(self, milliseconds: int) -> None:
        elapsed = QElapsedTimer()
        elapsed.start()
        while elapsed.elapsed() < milliseconds:
            self.app.processEvents()
            time.sleep(0.01)

    def test_time_manager_formats_clock_and_stopwatch_values(self) -> None:
        self.assertEqual(
            TimeManagerWindow.format_clock_time(QTime(9, 7, 5)),
            "09:07:05",
        )
        self.assertEqual(TimeManagerWindow.format_stopwatch(3_661_239), "01:01:01.23")
        self.assertEqual(TimeManagerWindow.format_stopwatch(-20), "00:00:00.00")

    def test_time_manager_stopwatch_starts_pauses_resumes_and_resets(self) -> None:
        manager = TimeManagerWindow()
        manager.toggle_stopwatch()
        self.assertTrue(manager._stopwatch_running)
        self.wait_for_ui(40)
        manager.toggle_stopwatch()
        paused_value = manager._stopwatch_accumulated_ms
        self.assertGreaterEqual(paused_value, 20)
        self.assertFalse(manager._stopwatch_running)
        self.assertEqual(manager.stopwatch_start_button.text(), "Weiter")
        manager.toggle_stopwatch()
        self.assertTrue(manager._stopwatch_running)
        manager.reset_stopwatch()
        self.assertFalse(manager._stopwatch_running)
        self.assertEqual(manager.stopwatch_display.text(), "00:00:00.00")
        manager.close()

    def test_time_manager_timer_counts_down_and_can_be_reset(self) -> None:
        manager = TimeManagerWindow()
        manager.timer_minutes.setValue(0)
        manager.timer_seconds.setValue(1)
        manager._update_timer()
        self.assertEqual(manager.timer_display.text(), "00:01")
        manager.start_timer()
        self.assertEqual(manager.timer_start_button.text(), "Pause")
        self.wait_for_ui(1100)
        self.assertEqual(manager.timer_display.text(), "00:00")
        self.assertEqual(manager.timer_start_button.text(), "Start")
        manager.reset_timer()
        self.assertEqual(manager.timer_display.text(), "00:01")
        manager.close()

    def test_time_manager_emits_one_completion_notification_signal(self) -> None:
        manager = TimeManagerWindow()
        finished = []
        manager.timer_finished.connect(lambda: finished.append(True))
        manager.timer_minutes.setValue(0)
        manager.timer_seconds.setValue(1)
        manager.start_timer()
        manager._timer_duration_ms = 0

        manager._update_timer()
        manager._update_timer()

        self.assertEqual(finished, [True])
        manager.close()

    def test_browser_normalizes_web_addresses_and_rejects_other_schemes(self) -> None:
        self.assertEqual(
            BrowserWindow.normalize_address("example.org"),
            QUrl("https://example.org"),
        )
        self.assertEqual(
            BrowserWindow.normalize_address("http://example.org/path"),
            QUrl("http://example.org/path"),
        )
        self.assertEqual(
            BrowserWindow.normalize_address("localhost:8080"),
            QUrl("https://localhost:8080"),
        )
        self.assertTrue(BrowserWindow.normalize_address("javascript:alert(1)").isEmpty())
        self.assertTrue(BrowserWindow.normalize_address("file:///etc/passwd").isEmpty())
        self.assertTrue(BrowserWindow.normalize_address(" ").isEmpty())

    def test_browser_starts_on_local_offline_home_and_has_navigation_controls(self) -> None:
        browser = BrowserWindow()
        self.assertIsInstance(browser.view.url(), QUrl)
        self.assertEqual(browser.windowTitle(), "Browser")
        self.assertEqual(browser.address_bar.text(), "")
        self.assertTrue(browser.address_bar.placeholderText().startswith("Adresse"))
        self.assertTrue(browser.home_button.isEnabled())
        self.assertTrue(browser.reload_button.isEnabled())
        browser.close()

    def test_terminal_uses_native_qterminal_and_requested_working_directory(self) -> None:
        directory = Path("/home/neonveil/Dokumente")
        program, arguments = TerminalWindow.terminal_command(directory)
        self.assertEqual(program, "qterminal")
        self.assertEqual(arguments, ["--workdir", str(directory)])

        terminal = TerminalWindow(directory)
        self.assertEqual(terminal.working_directory, directory)
        self.assertIn(str(directory), terminal.directory_label.text())
        self.assertTrue(terminal.launch_button.isEnabled())
        terminal.close()


if __name__ == "__main__":
    unittest.main()
