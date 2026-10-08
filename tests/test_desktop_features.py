import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLineEdit

from apps.settings.app import SettingsStore, SettingsWindow
from notifications import NotificationPopup, NotificationStore
from profiles import ProfileChooser, ProfileStore
from system_info import SystemInfoProvider
from taskbar import Taskbar


ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / ".desktop-features-test.ini"


class DesktopFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        SETTINGS_FILE.unlink(missing_ok=True)
        self.addCleanup(SETTINGS_FILE.unlink, missing_ok=True)

    def settings(self) -> QSettings:
        return QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)

    def test_profiles_validate_and_profile_chooser_creates_and_signs_in(self) -> None:
        store = ProfileStore(self.settings())
        self.assertEqual(store.create("  Ada   Lovelace "), (True, ""))
        self.assertFalse(store.create("ada lovelace")[0])
        self.assertFalse(store.create("")[0])
        self.assertEqual(store.profiles()[0].username, "Ada Lovelace")

        settings = QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)
        settings.remove("profiles/list")
        chooser = ProfileChooser(ProfileStore(settings))
        chooser.username.setText("Grace")
        chooser.create_profile()
        self.assertEqual(chooser.result(), ProfileChooser.DialogCode.Accepted)
        self.assertEqual(chooser.profile.username, "Grace")
        chooser.close()

    def test_appearance_is_persisted_and_settings_exposes_light_dark_choices(self) -> None:
        settings = self.settings()
        store = SettingsStore(settings)
        store.set_appearance("dark")
        window = SettingsWindow(
            store,
            runner=lambda arguments, **_kwargs: SimpleNamespace(
                args=arguments, returncode=1, stdout="", stderr="not available"
            ),
        )
        changes = []
        window.appearance_changed.connect(changes.append)
        self.assertEqual(window.appearance_combo.currentData(), "dark")
        window.appearance_combo.setCurrentIndex(window.appearance_combo.findData("light"))
        self.assertEqual(store.appearance(), "light")
        self.assertEqual(changes, ["light"])
        window.close()

    def test_notification_history_is_bounded_and_popup_dismisses(self) -> None:
        store = NotificationStore(self.settings())
        for index in range(105):
            store.add("Hinweis", str(index), "Test-App")
        history = store.history()
        self.assertEqual(len(history), store.MAX_ITEMS)
        self.assertEqual(history[0]["message"], "5")
        self.assertEqual(history[-1]["message"], "104")

        popup = NotificationPopup()
        popup.show_notification(history[-1], duration_ms=1)
        self.assertTrue(popup.isVisible())
        popup._dismiss()
        self.assertFalse(popup.isVisible())
        self.assertFalse(popup.dismiss_timer.isActive())
        store.clear()
        self.assertEqual(store.history(), [])
        popup.close()

    def test_start_menu_search_pin_recent_and_logout_work(self) -> None:
        taskbar = Taskbar(settings=self.settings())
        menu = taskbar.build_start_menu()
        programs = taskbar._program_menu
        rows = [action for action in programs.actions() if action.text() and action.text() != "Programme suchen"]
        self.assertEqual([action.text() for action in rows], sorted(action.text() for action in rows))
        search = next(
            action.defaultWidget()
            for action in programs.actions()
            if action.text() == "Programme suchen"
        )
        self.assertIsInstance(search, QLineEdit)
        search.setText("browser")
        self.app.processEvents()
        self.assertTrue(taskbar._program_actions["browser"].isVisible())
        self.assertFalse(taskbar._program_actions["editor"].isVisible())

        launched = []
        logged_out = []
        taskbar.application_requested.connect(launched.append)
        taskbar.logout_requested.connect(lambda: logged_out.append(True))
        taskbar._pin_actions["browser"].trigger()
        self.assertIn("browser", json.loads(taskbar.preferences.value("start/pinned")))
        taskbar._program_actions["browser"].trigger()
        self.assertEqual(json.loads(taskbar.preferences.value("start/recent")), ["browser"])
        self.assertEqual(launched, ["browser"])
        logout = next(
            action for action in menu.actions()[0].menu().actions()
            if action.text() == "Abmelden…"
        )
        logout.trigger()
        self.assertEqual(logged_out, [True])
        taskbar.close()

    def test_system_info_reports_real_metrics_and_cpu_delta(self) -> None:
        reads = 0

        def read_text(path, *args, **_kwargs):
            nonlocal reads
            value = str(path)
            if value == "/proc/device-tree/model":
                return "Raspberry Pi 4 Model B"
            if value == "/proc/cpuinfo":
                return "model name : Test CPU"
            if value == "/proc/meminfo":
                return "MemTotal: 1048576 kB\nMemAvailable: 524288 kB\n"
            if value == "/proc/stat":
                reads += 1
                return "cpu 100 0 50 850 0\n" if reads == 1 else "cpu 120 0 60 870 0\n"
            raise OSError("not available")

        provider = SystemInfoProvider()
        with patch.object(Path, "read_text", autospec=True, side_effect=read_text), patch(
            "system_info.shutil.disk_usage",
            return_value=SimpleNamespace(total=2 * 1024**3, used=1024**3, free=1024**3),
        ):
            first = provider.snapshot()
            second = provider.snapshot()
        self.assertEqual(first.model, "Raspberry Pi 4 Model B")
        self.assertEqual(first.cpu, "Test CPU")
        self.assertEqual(first.cpu_usage, "Wird gemessen…")
        self.assertEqual(second.cpu_usage, "60%")
        self.assertIn("0.5 GB verwendet", second.memory)
        self.assertIn("1.0 GB verwendet", second.storage)

    def test_missing_system_metrics_are_labeled_unavailable(self) -> None:
        provider = SystemInfoProvider()
        with patch.object(SystemInfoProvider, "_read_text", return_value=None), patch.object(
            Path, "read_text", side_effect=OSError("missing procfs")
        ), patch("system_info.shutil.disk_usage", side_effect=OSError("missing")):
            snapshot = provider.snapshot()
        self.assertEqual(snapshot.model, "Nicht verfügbar")
        self.assertEqual(snapshot.cpu_usage, "Nicht verfügbar")
        self.assertEqual(snapshot.memory, "Nicht verfügbar")
        self.assertEqual(snapshot.storage, "Nicht verfügbar")


if __name__ == "__main__":
    unittest.main()
