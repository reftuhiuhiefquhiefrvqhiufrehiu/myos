from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from appstore.core import (
    AppStore,
    AppStoreError,
    CatalogApp,
    compare_versions,
    parse_version,
)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "store-apps"))

from apps.app_store.app import AppCard, AppStoreWindow
from calculator.app import CalculatorWindow, SafeCalculator
from unit_converter.app import UnitConverterWindow
from neon_sketch.app import NeonSketchWindow, SketchCanvas
from focus_flow.app import FocusFlowWindow, MODES

ROOT_DIR = Path(__file__).resolve().parent.parent
STORE_APPS_DIR = ROOT_DIR / "store-apps"


class TestAppStoreCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QApplication.instance() is None:
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_appstore_"))
        self.catalog_path = self.temp_dir / "catalog.json"
        self.store_dir = self.temp_dir / "store-apps"
        self.store_dir.mkdir(parents=True, exist_ok=True)

        self.sample_catalog = {
            "version": 1,
            "apps": [
                {
                    "id": "calculator",
                    "name": "Neon Calculator",
                    "version": "1.0.0",
                    "entry": "calculator.app:CalculatorWindow",
                    "summary": "Taschenrechner",
                    "description": "Ein moderner Rechner.",
                    "category": "Werkzeuge",
                    "files": {
                        "calculator/__init__.py": "1a6d9b6019dcff19f28a61bb13591744f48b82f034c2e1bd7d1284ed285aeb7b",
                        "calculator/app.py": "1c14ae14a7ae28dd1c7947f735d354626d725270ce15bc5df9b0217140dfc8dd",
                    },
                },
                {
                    "id": "unit_converter",
                    "name": "Einheitenrechner",
                    "version": "1.1.0",
                    "entry": "unit_converter.app:UnitConverterWindow",
                    "summary": "Umrechner",
                    "category": "Werkzeuge",
                    "files": {
                        "unit_converter/__init__.py": "d8283253559b394f30668877345e53d8bf45bbfc8c89afd36b3b4bc6adf4b331",
                    },
                },
            ],
        }
        self.catalog_path.write_text(json.dumps(self.sample_catalog), encoding="utf-8")

        self.store = AppStore(
            repository="https://github.com/coolionk/neonveil",
            ref="main",
            store_dir=self.store_dir,
            catalog_path=self.catalog_path,
            root_path=self.temp_dir,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_version_parsing_and_comparison(self) -> None:
        self.assertEqual(compare_versions("1.0.0", "1.0.0"), 0)
        self.assertEqual(compare_versions("1.1.0", "1.0.0"), 1)
        self.assertEqual(compare_versions("1.0.0", "1.2.0"), -1)
        self.assertEqual(compare_versions("2.0.0", "1.9.9"), 1)
        self.assertEqual(compare_versions("1.0.1", "1.0.0"), 1)
        self.assertEqual(compare_versions("1.0.0-beta", "1.0.0"), -1)
        self.assertEqual(compare_versions("1.0.0", "1.0.0-beta"), 1)

    def test_repository_validation(self) -> None:
        owner, repo = AppStore.validate_repository("https://github.com/owner/myrepo")
        self.assertEqual(owner, "owner")
        self.assertEqual(repo, "myrepo")

        owner, repo = AppStore.validate_repository("owner/myrepo.git")
        self.assertEqual(owner, "owner")
        self.assertEqual(repo, "myrepo")

        with self.assertRaises(AppStoreError):
            AppStore.validate_repository("http://insecure.com/foo/bar")

        with self.assertRaises(AppStoreError):
            AppStore.validate_repository("invalid_format")

    def test_fetch_catalog_from_local_file(self) -> None:
        apps = self.store.fetch_catalog(force_remote=False)
        self.assertEqual(len(apps), 2)
        self.assertEqual(apps[0].id, "calculator")
        self.assertEqual(apps[0].name, "Neon Calculator")
        self.assertEqual(apps[1].id, "unit_converter")

    def test_catalog_malicious_path_rejection(self) -> None:
        malicious_catalog = {
            "version": 1,
            "apps": [
                {
                    "id": "bad_app",
                    "name": "Bad App",
                    "version": "1.0.0",
                    "entry": "bad.app:Window",
                    "files": {
                        "../etc/passwd": "1a6d9b6019dcff19f28a61bb13591744f48b82f034c2e1bd7d1284ed285aeb7b",
                        "bad/script.sh": "1a6d9b6019dcff19f28a61bb13591744f48b82f034c2e1bd7d1284ed285aeb7b",
                    },
                }
            ],
        }
        self.catalog_path.write_text(json.dumps(malicious_catalog), encoding="utf-8")
        apps = self.store.fetch_catalog(force_remote=False)
        self.assertEqual(len(apps), 0)

    def test_install_uninstall_cycle(self) -> None:
        # Create mock file in repo to be installed via local fallback
        calc_src = self.temp_dir / "store-apps" / "calculator"
        calc_src.mkdir(parents=True, exist_ok=True)
        init_file = calc_src / "__init__.py"
        init_file.write_bytes(b"# init")
        init_sha = "1a6d9b6019dcff19f28a61bb13591744f48b82f034c2e1bd7d1284ed285aeb7b"

        app_py = calc_src / "app.py"
        app_py.write_bytes(b"# app")
        app_sha = "1c14ae14a7ae28dd1c7947f735d354626d725270ce15bc5df9b0217140dfc8dd"

        # Update catalog with matching shas
        import hashlib

        init_real_sha = hashlib.sha256(b"# init").hexdigest()
        app_real_sha = hashlib.sha256(b"# app").hexdigest()

        custom_catalog = {
            "version": 1,
            "apps": [
                {
                    "id": "test_app",
                    "name": "Test Application",
                    "version": "1.0.0",
                    "entry": "test_app.app:TestWindow",
                    "summary": "Test App",
                    "category": "Werkzeuge",
                    "files": {
                        "test_app/__init__.py": init_real_sha,
                        "test_app/app.py": app_real_sha,
                    },
                }
            ],
        }
        self.catalog_path.write_text(json.dumps(custom_catalog), encoding="utf-8")
        self.store.fetch_catalog(force_remote=False)

        # Place the test_app source in root_path for fallback
        src_dir = self.temp_dir / "store-apps" / "test_app"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "__init__.py").write_bytes(b"# init")
        (src_dir / "app.py").write_bytes(b"# app")

        # Install
        installed = self.store.install_app("test_app")
        self.assertEqual(installed.id, "test_app")
        self.assertTrue(self.store.is_installed("test_app"))

        # Verify manifest was written
        inst_manifest = self.store_dir / "test_app" / "app.json"
        self.assertTrue(inst_manifest.is_file())

        # Uninstall
        self.assertTrue(self.store.uninstall_app("test_app"))
        self.assertFalse(self.store.is_installed("test_app"))

    def test_checksum_mismatch_fails(self) -> None:
        custom_catalog = {
            "version": 1,
            "apps": [
                {
                    "id": "tampered_app",
                    "name": "Tampered",
                    "version": "1.0.0",
                    "entry": "tampered.app:TamperedWindow",
                    "files": {
                        "tampered_app/app.py": "0" * 64,  # Incorrect hash
                    },
                }
            ],
        }
        self.catalog_path.write_text(json.dumps(custom_catalog), encoding="utf-8")
        self.store.fetch_catalog(force_remote=False)

        src_dir = self.temp_dir / "store-apps" / "tampered_app"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "app.py").write_bytes(b"content")

        with self.assertRaises(AppStoreError):
            self.store.install_app("tampered_app")


class TestStoreAppsUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QApplication.instance() is None:
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()

    def test_safe_calculator_evaluator(self) -> None:
        self.assertEqual(SafeCalculator.evaluate("2 + 3"), 5)
        self.assertEqual(SafeCalculator.evaluate("10 - 4 * 2"), 2)
        self.assertEqual(SafeCalculator.evaluate("(10 - 4) * 2"), 12)
        self.assertEqual(SafeCalculator.evaluate("15 ÷ 3"), 5)
        self.assertEqual(SafeCalculator.evaluate("2.5 × 4"), 10)
        self.assertEqual(SafeCalculator.evaluate("2 ^ 3"), 8)
        self.assertEqual(SafeCalculator.evaluate("-5 + 12"), 7)

        with self.assertRaises(ZeroDivisionError):
            SafeCalculator.evaluate("10 / 0")

        with self.assertRaises(ValueError):
            SafeCalculator.evaluate("( 2 + 3")

    def test_calculator_window(self) -> None:
        calc = CalculatorWindow()
        self.assertEqual(calc.result_label.text(), "0")
        calc._on_button_click("7")
        calc._on_button_click("+")
        calc._on_button_click("5")
        calc.calculate()
        self.assertEqual(calc.result_label.text(), "12")

        calc._on_button_click("C")
        self.assertEqual(calc.result_label.text(), "0")

    def test_unit_converter_window(self) -> None:
        conv = UnitConverterWindow()
        conv.in_input.setText("1000")
        conv.cat_combo.setCurrentText("Länge")
        conv.in_unit_combo.setCurrentText("Meter (m)")
        conv.out_unit_combo.setCurrentText("Kilometer (km)")
        conv._convert()
        self.assertEqual(conv.out_output.text(), "1")

        # Temperature
        conv.cat_combo.setCurrentText("Temperatur")
        conv.in_unit_combo.setCurrentText("Grad Celsius (°C)")
        conv.out_unit_combo.setCurrentText("Grad Fahrenheit (°F)")
        conv.in_input.setText("0")
        conv._convert()
        self.assertEqual(conv.out_output.text(), "32")

    def test_neon_sketch_window(self) -> None:
        sketch = NeonSketchWindow()
        self.assertEqual(sketch.canvas.width, 7)
        self.assertEqual(sketch.canvas.color.name(), "#35c9bd")
        self.assertEqual(sketch.status.text(), "Bereit")

        sketch._select_color("#fa68b8", "Pink")
        self.assertEqual(sketch.canvas.color.name(), "#fa68b8")
        self.assertEqual(sketch.status.text(), "Pink ausgewählt")

        sketch._change_width(14)
        self.assertEqual(sketch.canvas.width, 14)
        self.assertEqual(sketch.status.text(), "Pinselgröße: 14")

        sketch._clear()
        self.assertEqual(sketch.status.text(), "Leinwand geleert")
        sketch.close()

    def test_sketch_canvas_export(self) -> None:
        canvas = SketchCanvas()
        export_dir = Path(tempfile.mkdtemp(prefix="test_sketch_"))
        try:
            target = export_dir / "sketch.png"
            self.assertTrue(canvas.save_png(target))
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)

    def test_focus_flow_window(self) -> None:
        flow = FocusFlowWindow()
        try:
            self.assertEqual(flow.time_label.text(), "25:00")
            self.assertEqual(flow.progress.value(), 0)
            self.assertFalse(flow._running)
            self.assertEqual(flow.start_button.text(), "Starten")
            self.assertEqual(flow.sessions.text().split(":")[-1].strip(), str(flow._completed))

            # Switching a mode while idle resets the countdown to that mode.
            flow.mode_combo.setCurrentText("Kurze Pause · 5 Minuten")
            self.assertEqual(flow.time_label.text(), "05:00")
            flow.mode_combo.setCurrentText("Lange Pause · 15 Minuten")
            self.assertEqual(flow.time_label.text(), "15:00")

            # A completed focus session increments the daily counter.
            flow.mode_combo.setCurrentText("Fokus · 25 Minuten")
            self.assertEqual(flow.time_label.text(), "25:00")
            state = Path(tempfile.mkdtemp(prefix="test_focus_")) / "focus-flow.json"
            flow._state_path = state
            before = flow._completed
            flow._remaining = 1
            flow._tick()
            self.assertEqual(flow._completed, before + 1)
            self.assertTrue(state.is_file())

            # Pause leaves the remaining time untouched.
            flow._reset_timer()
            self.assertEqual(flow.time_label.text(), "25:00")
            flow._toggle_timer()
            self.assertTrue(flow._running)
            self.assertEqual(flow.start_button.text(), "Pausieren")
            self.assertEqual(flow.status.text(), "Fokus-Session läuft")
            flow._remaining = 1490
            flow._toggle_timer()
            self.assertFalse(flow._running)
            self.assertEqual(flow._remaining, 1490)
            self.assertEqual(flow.start_button.text(), "Starten")
        finally:
            flow.close()

    def test_focus_flow_state_roundtrip(self) -> None:
        flow = FocusFlowWindow()
        try:
            state = Path(tempfile.mkdtemp(prefix="test_focus_state_")) / "focus-flow.json"
            flow._state_path = state
            self.assertEqual(flow._load_completed(), 0)
            flow._completed = 7
            flow._save_completed()
            self.assertEqual(flow._load_completed(), 7)
            state.write_text("not json", encoding="utf-8")
            self.assertEqual(flow._load_completed(), 0)
        finally:
            flow.close()

    def test_catalog_covers_every_store_app(self) -> None:
        """Every folder in store-apps must ship a catalog entry with valid files."""
        dirs = {child.name for child in STORE_APPS_DIR.iterdir() if child.is_dir() and not child.name.startswith(".")}
        self.assertEqual(dirs, {"calculator", "unit_converter", "neon_sketch", "focus_flow"})

        store = AppStore()
        apps = store.fetch_catalog(force_remote=False)
        self.assertEqual([app.id for app in apps], ["calculator", "unit_converter", "neon_sketch", "focus_flow"])

        for app in apps:
            for rel_path, expected_sha in app.files.items():
                source = ROOT_DIR / "store-apps" / rel_path
                self.assertTrue(source.is_file(), f"{rel_path} fehlt im Repository")
                actual = hashlib.sha256(source.read_bytes()).hexdigest()
                self.assertEqual(actual, expected_sha, f"Prüfsumme für {rel_path} stimmt nicht")
            self.assertGreater(app.size, 0)
            for field in ("entry", "summary", "description", "category", "author"):
                self.assertTrue(getattr(app, field), f"{app.id} hat kein {field}")

    def test_store_app_entry_points_load(self) -> None:
        """Each catalog entry must resolve to a constructible window class."""
        store = AppStore()
        for app in store.fetch_catalog(force_remote=False):
            window = store.load_app_window(app.id)
            try:
                self.assertIsNotNone(window)
            finally:
                if hasattr(window, "close"):
                    window.close()
                    window.deleteLater()

    def test_app_store_window(self) -> None:
        store = AppStore()
        win = AppStoreWindow(store=store)
        self.assertIsNotNone(win)
        if win.worker:
            win.worker.wait(2000)
            QApplication.processEvents()
        self.assertTrue(win.btn_refresh.isEnabled())
        # Filter search
        win.search_input.setText("Calculator")
        self.assertEqual(win._search_text, "calculator")
        win._set_filter("installed")
        self.assertEqual(win._active_filter, "installed")
        win.close()


if __name__ == "__main__":
    unittest.main()
