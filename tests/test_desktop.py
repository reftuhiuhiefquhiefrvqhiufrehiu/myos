import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from unittest.mock import patch
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QListWidget,
    QMessageBox,
    QToolButton,
)

from apps.settings.app import SettingsWindow
from main import create_code_studio_window, create_settings_window
from shell import DesktopShell
from taskbar import Taskbar


class DesktopShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_desktop_is_borderless_and_stays_below_app_windows(self) -> None:
        desktop = DesktopShell()
        flags = desktop.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnBottomHint)
        self.assertEqual(desktop.windowTitle(), "NeonVeil")
        desktop.close()

    def test_desktop_shortcuts_open_on_double_click(self) -> None:
        desktop = DesktopShell()
        desktop.show()
        launched = []
        desktop.application_requested.connect(launched.append)

        self.assertIsInstance(desktop.shortcuts, QListWidget)
        self.assertEqual(desktop.shortcuts.count(), 8)
        item = desktop.shortcuts.item(0)
        desktop.shortcuts.itemDoubleClicked.emit(item)
        self.app.processEvents()

        self.assertEqual(launched, ["files"])
        desktop.close()
        desktop.deleteLater()
        self.app.processEvents()

    def test_taskbar_start_menu_contains_programs_files_and_settings(self) -> None:
        taskbar = Taskbar()
        menu = taskbar.build_start_menu()
        program_menu = taskbar._program_menu
        self.assertIsNotNone(program_menu)
        self.assertEqual(
            [action.text() for action in program_menu.actions() if action.text()],
            [
                "Programme suchen",
                "Bilder",
                "Browser",
                "Code Studio",
                "Dateien",
                "Downloads",
                "Einstellungen",
                "Musik",
                "NeonVeil Update Manager",
                "Papierkorb",
                "Raspberry-Pi-Werkzeuge",
                "Screenshot",
                "Terminal",
                "Texteditor",
                "Uhr",
                "Über NeonVeil",
            ],
        )
        self.assertEqual(
            [action.text() for action in menu.actions()],
            [
                "Profil · Gast",
                "Zuletzt verwendet",
                "Angeheftete Programme",
                "Programme anheften",
                "Programme",
                "Dateien",
                "Downloads",
                "Einstellungen",
                "Benachrichtigungen",
                "Über NeonVeil",
                "",
                "Ein/Aus",
            ],
        )

        launched = []
        taskbar.application_requested.connect(launched.append)
        taskbar._program_actions["editor"].trigger()
        menu.actions()[5].trigger()
        menu.actions()[7].trigger()
        self.assertEqual(launched, ["editor", "files", "settings"])
        taskbar.close()
        taskbar.deleteLater()
        self.app.processEvents()

    def test_settings_launcher_connects_wallpaper_updates_to_desktop(self) -> None:
        desktop = DesktopShell()
        with patch.object(desktop, "set_wallpaper") as apply_wallpaper:
            window = create_settings_window(desktop)
            self.assertIsInstance(window, SettingsWindow)
            window.wallpaper_changed.emit("graphite", "")
            apply_wallpaper.assert_called_once_with("graphite", "")
        window.close()
        desktop.close()

    def test_code_studio_opened_with_a_file_can_open_project_url(self) -> None:
        opened_urls = []
        window = create_code_studio_window(opened_urls.append)
        window.open_local_url_requested.emit("http://localhost:3000")
        self.assertEqual(opened_urls, ["http://localhost:3000"])
        window.close()

    def test_taskbar_focus_styles_use_visible_control_borders(self) -> None:
        taskbar = Taskbar()
        stylesheet = taskbar.styleSheet()
        self.assertIn("QPushButton:focus, QToolButton:focus", stylesheet)
        self.assertIn("border: 2px solid #176c67", stylesheet)
        self.assertIn(
            "QPushButton:focus, QToolButton:focus, QComboBox:focus",
            DesktopShell.window_stylesheet(),
        )
        self.assertIn("border: 2px solid #176c67", DesktopShell.window_stylesheet())
        taskbar.close()

    def test_taskbar_clock_and_open_window_button(self) -> None:
        taskbar = Taskbar()
        window = QMainWindow()
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.setWindowTitle("Testfenster")
        taskbar.add_window(window)
        taskbar.show()
        window.show()
        self.app.processEvents()

        self.assertRegex(taskbar.clock.text(), r"^\d{2}:\d{2}$")
        buttons = taskbar.findChildren(QToolButton)
        window_button = next(button for button in buttons if button.text() == "Testfenster")
        self.assertTrue(window_button.isChecked())
        window.showMinimized()
        self.app.processEvents()
        self.assertTrue(window.isMinimized())
        self.assertFalse(window_button.isChecked())
        window_button.click()
        self.app.processEvents()
        self.assertTrue(window.isVisible())
        self.assertFalse(window.isMinimized())
        self.assertTrue(window_button.isChecked())
        window.close()
        self.app.processEvents()
        self.assertNotIn(window, taskbar._task_windows)
        taskbar.close()
        taskbar.deleteLater()
        self.app.processEvents()

    def test_power_actions_require_confirmation_and_report_command_failure(self) -> None:
        calls = []
        taskbar = Taskbar(
            power_command=lambda command: (
                calls.append(command) or (False, "permission denied")
            )
        )
        with patch(
            "taskbar.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ), patch("taskbar.QMessageBox.critical") as show_error:
            taskbar._confirm_power_action("poweroff")
        self.assertEqual(calls, [])
        show_error.assert_not_called()

        with patch(
            "taskbar.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch("taskbar.QMessageBox.critical") as show_error:
            taskbar._confirm_power_action("reboot")
        self.assertEqual(calls, ["reboot"])
        self.assertIn("permission denied", show_error.call_args.args[2])

        with patch("taskbar.QMessageBox.critical") as show_error:
            taskbar._confirm_power_action("arbitrary")
        self.assertEqual(calls, ["reboot"])
        self.assertIn("Ungültige Systemaktion", show_error.call_args.args[2])
        taskbar.close()


if __name__ == "__main__":
    unittest.main()
