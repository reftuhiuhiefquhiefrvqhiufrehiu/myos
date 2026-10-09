import unittest

from theme import (
    DARK,
    LIGHT,
    global_stylesheet,
    notification_stylesheet,
    normalize_theme,
    palette,
    taskbar_stylesheet,
)


class ThemeTokenTests(unittest.TestCase):
    def test_normalize_theme_maps_unknown_values_to_light(self) -> None:
        self.assertEqual(normalize_theme("dark"), "dark")
        self.assertEqual(normalize_theme("DARK"), "dark")
        self.assertEqual(normalize_theme("light"), "light")
        self.assertEqual(normalize_theme(None), "light")
        self.assertEqual(normalize_theme("nonsense"), "light")

    def test_palette_returns_independent_copies(self) -> None:
        light = palette("light")
        dark = palette("dark")
        self.assertEqual(light, LIGHT)
        self.assertEqual(dark, DARK)
        self.assertNotEqual(light["window"], dark["window"])
        light["window"] = "#000000"
        self.assertNotEqual(LIGHT["window"], "#000000")

    def test_light_palette_keeps_required_focus_colors(self) -> None:
        self.assertEqual(LIGHT["focus"], "#176c67")


class StylesheetTests(unittest.TestCase):
    def test_global_stylesheet_keeps_focus_rules_used_by_tests(self) -> None:
        stylesheet = global_stylesheet("light")
        self.assertIn(
            "QPushButton:focus, QToolButton:focus, QComboBox:focus", stylesheet
        )
        self.assertIn("border: 2px solid #176c67", stylesheet)

    def test_global_stylesheet_has_distinct_dark_theme(self) -> None:
        light = global_stylesheet("light")
        dark = global_stylesheet("dark")
        self.assertNotEqual(light, dark)
        self.assertIn(palette("dark")["window"], dark)

    def test_theme_stylesheets_cover_their_widgets(self) -> None:
        taskbar = taskbar_stylesheet("light")
        self.assertIn("QPushButton:focus, QToolButton:focus", taskbar)
        self.assertIn("border: 2px solid #176c67", taskbar)
        self.assertIn("QPushButton#startButton", taskbar)
        self.assertIn("QLabel#clock", taskbar)

        popup = notification_stylesheet("dark")
        self.assertIn("QWidget#notificationPopup", popup)
        self.assertIn("QLabel#appName", popup)


if __name__ == "__main__":
    unittest.main()
