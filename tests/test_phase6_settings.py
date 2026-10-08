import os
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from apps.settings.app import (
    AudioController,
    DisplayController,
    DisplayState,
    SettingsStore,
    SettingsWindow,
)


ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / ".phase6-settings-test.ini"


def result(
    arguments: list[str],
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)


class PhaseSixSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        SETTINGS_FILE.unlink(missing_ok=True)
        self.addCleanup(SETTINGS_FILE.unlink, missing_ok=True)

    def test_wallpaper_choice_persists_and_reset_restores_default(self) -> None:
        settings = QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)
        store = SettingsStore(settings)
        store.set_wallpaper("morning")
        reloaded = SettingsStore(
            QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)
        )
        self.assertEqual(reloaded.wallpaper(), ("morning", ""))
        reloaded.reset_wallpaper()
        self.assertEqual(reloaded.wallpaper(), ("lagoon", ""))

    def test_wallpaper_control_applies_preset_and_rejects_unknown_choice(self) -> None:
        window = SettingsWindow(
            SettingsStore(QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)),
            runner=lambda arguments, **_kwargs: result(arguments, stderr="no device", returncode=1),
        )
        emitted = []
        window.wallpaper_changed.connect(lambda style, path: emitted.append((style, path)))

        self.assertTrue(window.apply_wallpaper("graphite"))
        self.assertFalse(window.apply_wallpaper("unknown"))
        self.assertEqual(emitted, [("graphite", "")])
        self.assertEqual(window.store.wallpaper(), ("graphite", ""))
        self.assertTrue(window._wallpaper_buttons["graphite"].isChecked())
        window.close()

    def test_wallpaper_accepts_a_local_image_and_persists_its_path(self) -> None:
        image_path = ROOT / ".phase6-wallpaper-test.png"
        self.addCleanup(image_path.unlink, missing_ok=True)
        image = QImage(4, 4, QImage.Format.Format_RGB32)
        image.fill(0x247B73)
        self.assertTrue(image.save(str(image_path)))

        store = SettingsStore(
            QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)
        )
        window = SettingsWindow(
            store,
            runner=lambda arguments, **_kwargs: result(arguments, stderr="unavailable", returncode=1),
        )
        self.assertTrue(window.apply_wallpaper("custom", str(image_path)))
        self.assertEqual(store.wallpaper(), ("custom", str(image_path)))
        self.assertTrue(window.image_button.isChecked())
        self.assertIn("✓ Eigenes Bild aktiv", window.wallpaper_status.text())
        window.close()

        reopened = SettingsWindow(
            SettingsStore(
                QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)
            ),
            runner=lambda arguments, **_kwargs: result(
                arguments, stderr="unavailable", returncode=1
            ),
        )
        self.assertTrue(reopened.image_button.isChecked())
        self.assertIn("✓ Eigenes Bild aktiv", reopened.wallpaper_status.text())
        reopened.close()

    def test_cancelled_wallpaper_picker_restores_existing_selection(self) -> None:
        store = SettingsStore(
            QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)
        )
        store.set_wallpaper("morning")
        window = SettingsWindow(
            store,
            runner=lambda arguments, **_kwargs: result(
                arguments, stderr="unavailable", returncode=1
            ),
        )

        with patch(
            "apps.settings.app.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ):
            window.image_button.click()

        self.assertTrue(window._wallpaper_buttons["morning"].isChecked())
        self.assertFalse(window.image_button.isChecked())
        self.assertEqual(store.wallpaper(), ("morning", ""))
        window.close()

    def test_settings_window_scrolls_on_small_displays_and_keeps_a_small_minimum(self) -> None:
        window = SettingsWindow(
            SettingsStore(QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)),
            runner=lambda arguments, **_kwargs: result(arguments, stderr="unavailable", returncode=1),
        )
        self.assertLessEqual(window.minimumWidth(), 320)
        self.assertLessEqual(window.minimumHeight(), 240)
        window.resize(320, 240)
        window.show()
        self.app.processEvents()
        self.assertGreater(
            window.scroll_area.verticalScrollBar().maximum(),
            0,
        )
        self.assertEqual(
            window.scroll_area.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertLessEqual(
            window.content_widget.width(),
            window.scroll_area.viewport().width(),
        )
        window.close()

    def test_keyboard_focus_styles_use_visible_borders(self) -> None:
        window = SettingsWindow(
            SettingsStore(QSettings(str(SETTINGS_FILE), QSettings.Format.IniFormat)),
            runner=lambda arguments, **_kwargs: result(arguments, stderr="unavailable", returncode=1),
        )
        window.show()
        window.image_button.setFocus()
        self.app.processEvents()
        self.assertTrue(window.image_button.hasFocus())
        self.assertIn("QPushButton:focus", window.styleSheet())
        self.assertIn("border: 3px solid #12354b", window.image_button.styleSheet())
        self.assertIsInstance(window.display_combo, QComboBox)
        self.assertIn("QComboBox:focus", window.styleSheet())
        window.close()

    def test_audio_controller_uses_detected_control_and_validates_response(self) -> None:
        commands = []

        def runner(arguments, **_kwargs):
            commands.append(arguments)
            if arguments == ["amixer", "scontrols"]:
                return result(arguments, "Simple mixer control 'Master',0\n")
            if arguments == ["amixer", "sget", "Master"]:
                return result(arguments, "Front Left: Playback 50 [63%] [on]\n")
            return result(arguments)

        audio = AudioController(runner)
        self.assertEqual(audio.read_volume(), (63, ""))
        self.assertEqual(audio.set_volume(72), (True, "Lautstärke auf 72% gesetzt."))
        self.assertEqual(commands[-1], ["amixer", "sset", "Master", "72%"])
        command_count = len(commands)
        self.assertFalse(audio.set_volume(101)[0])
        self.assertEqual(len(commands), command_count)

    def test_audio_controller_reports_missing_hardware_or_malformed_level(self) -> None:
        unavailable = AudioController(
            lambda arguments, **_kwargs: result(
                arguments, stderr="cannot find card", returncode=1
            )
        )
        self.assertIn("cannot find card", unavailable.read_volume()[1])

        malformed = AudioController(
            lambda arguments, **_kwargs: (
                result(arguments, "Simple mixer control 'Master',0\n")
                if arguments == ["amixer", "scontrols"]
                else result(arguments, "Playback [very loud]\n")
            )
        )
        self.assertIn("keinen gültigen Pegel", malformed.read_volume()[1])

    def test_display_controller_lists_only_reported_modes_and_applies_selection(self) -> None:
        query = (
            "Screen 0: minimum 320 x 200, current 1280 x 720\n"
            "HDMI-1 connected primary 1280x720+0+0\n"
            "   1920x1080 60.00\n"
            "   1280x720 60.00*+\n"
            "DP-1 disconnected\n"
        )
        commands = []

        def runner(arguments, **_kwargs):
            commands.append(arguments)
            if arguments == ["xrandr", "--query"]:
                return result(arguments, query)
            return result(arguments)

        display = DisplayController(runner)
        state, message = display.query()
        self.assertEqual(message, "")
        self.assertEqual(
            state,
            DisplayState("HDMI-1", ("1920x1080", "1280x720"), "1280x720"),
        )
        self.assertEqual(display.apply_mode("1920x1080"), (True, "Auflösung auf 1920x1080 gesetzt."))
        self.assertEqual(
            commands[-1],
            ["xrandr", "--output", "HDMI-1", "--mode", "1920x1080"],
        )
        command_count = len(commands)
        self.assertFalse(display.apply_mode("9999x9999")[0])
        self.assertEqual(len(commands), command_count + 1)

    def test_display_controller_surfaces_command_failures(self) -> None:
        unavailable = DisplayController(
            lambda arguments, **_kwargs: result(
                arguments, stderr="Can't open display", returncode=1
            )
        )
        state, message = unavailable.query()
        self.assertIsNone(state)
        self.assertIn("Can't open display", message)

        failing_change = DisplayController(
            lambda arguments, **_kwargs: (
                result(
                    arguments,
                    "HDMI-1 connected\n   1280x720 60.00*\n",
                )
                if arguments == ["xrandr", "--query"]
                else result(arguments, stderr="mode rejected", returncode=1)
            )
        )
        self.assertIn("mode rejected", failing_change.apply_mode("1280x720")[1])


if __name__ == "__main__":
    unittest.main()
