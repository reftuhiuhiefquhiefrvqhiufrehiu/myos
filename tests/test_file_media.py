import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from apps.file_manager.app import FileManagerWindow
from apps.file_manager.trash import TrashStore, TrashWindow
from apps.music_player.app import MusicPlayerWindow
from apps.screenshots.app import ScreenshotController, ScreenshotHotkey


class FakeScreen:
    def grabWindow(self, _window_id: int) -> QPixmap:
        pixmap = QPixmap(12, 8)
        pixmap.fill(Qt.GlobalColor.blue)
        return pixmap


class FileMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_file_manager_searches_recursively_and_results_are_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "subfolder"
            nested.mkdir()
            target = nested / "My-Note.txt"
            target.write_text("hello", encoding="utf-8")
            opened = []
            manager = FileManagerWindow(
                opened.append, root, trash_store=TrashStore(root / "trash")
            )
            manager.search_field.setText("note")
            manager.search()

            self.assertEqual(manager.items.count(), 1)
            result = manager.items.item(0)
            self.assertEqual(result.data(Qt.ItemDataRole.UserRole), str(target))
            manager.items.itemDoubleClicked.emit(result)
            self.assertEqual(opened, [str(target)])
            manager.clear_search()
            self.assertIsNone(manager._search_root)
            manager.close()

    def test_file_properties_show_required_metadata_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.txt"
            path.write_text("hello", encoding="utf-8")
            manager = FileManagerWindow(
                lambda _path: None,
                Path(directory),
                trash_store=TrashStore(Path(directory) / "trash"),
            )
            with patch.object(QMessageBox, "information") as show_properties:
                manager.show_properties(path)
            details = show_properties.call_args.args[2]
            for label in (
                "Größe:",
                "Dateityp: text/plain",
                "Speicherort:",
                "Erstellungsdatum:",
                "Änderungsdatum:",
            ):
                self.assertIn(label, details)
            manager.close()

    def test_delete_moves_to_trash_and_restore_preserves_existing_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trash = TrashStore(root / "trash")
            source = root / "document.txt"
            source.write_text("old", encoding="utf-8")
            entry = trash.move_to_trash(source)
            self.assertFalse(source.exists())
            self.assertEqual(trash.size(), entry.stored_path.stat().st_size)

            source.write_text("new", encoding="utf-8")
            restored = trash.restore(entry)
            self.assertEqual(source.read_text(encoding="utf-8"), "new")
            self.assertEqual(restored.read_text(encoding="utf-8"), "old")
            self.assertEqual(restored.name, "document (wiederhergestellt 1).txt")
            self.assertEqual(trash.entries(), [])

    def test_file_manager_delete_uses_recoverable_trash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "delete-me.txt"
            source.write_text("recoverable", encoding="utf-8")
            store = TrashStore(root / "trash")
            manager = FileManagerWindow(
                lambda _path: None, root, trash_store=store
            )
            manager.items.setCurrentRow(0)

            manager.delete_selected()

            self.assertFalse(source.exists())
            self.assertEqual(len(store.entries()), 1)
            self.assertEqual(store.entries()[0].original_path, source)
            manager.close()

    def test_trash_window_confirms_permanent_deletion_and_reports_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TrashStore(root / "trash")
            source = root / "note.txt"
            source.write_text("trash me", encoding="utf-8")
            entry = store.move_to_trash(source)
            window = TrashWindow(store)
            self.assertEqual(window.items.count(), 1)
            self.assertIn("8 B", window.status.text())

            with patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.No,
            ):
                window.empty_trash()
            self.assertTrue(entry.stored_path.exists())

            with patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                window.empty_trash()
            self.assertFalse(entry.stored_path.exists())
            self.assertEqual(window.items.count(), 0)
            window.close()

    def test_music_playlist_filters_supported_files_and_formats_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mp3 = root / "track.mp3"
            ogg = root / "podcast.ogg"
            wav = root / "ignore.wav"
            for path in (mp3, ogg, wav):
                path.touch()
            player = MusicPlayerWindow()
            player.add_to_playlist([mp3, ogg, wav])
            self.assertEqual(player.playlist, [mp3, ogg])
            self.assertEqual(player.items.count(), 2)
            self.assertEqual(player._format_time(65_000), "01:05")
            player.close()

    def test_screenshot_saves_numbered_capture_under_pictures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ScreenshotController(Path(directory), lambda: FakeScreen())
            first = controller.capture()
            second = controller.capture()
            self.assertEqual(first.name, "MyOS-Screenshot-001.png")
            self.assertEqual(second.name, "MyOS-Screenshot-002.png")
            self.assertEqual(first.parent.name, "Screenshots")
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())

    def test_print_key_hotkey_triggers_capture(self) -> None:
        captures = []

        class Controller:
            def capture(self):
                captures.append(True)

        hotkey = ScreenshotHotkey(Controller())
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Print,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(hotkey.eventFilter(self.app, event))
        self.assertEqual(captures, [True])

    def test_start_menu_registers_music_screenshot_and_trash_apps(self) -> None:
        from taskbar import Taskbar

        taskbar = Taskbar()
        taskbar.build_start_menu()
        self.assertTrue({"music", "screenshot", "trash"}.issubset(taskbar._program_actions))
        taskbar.close()


if __name__ == "__main__":
    unittest.main()
