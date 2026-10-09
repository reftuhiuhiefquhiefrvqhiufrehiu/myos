import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from apps.file_manager.app import FileManagerWindow
from apps.file_manager.trash import TrashStore
from apps.image_viewer.app import ImageViewerWindow
from apps.text_editor.app import TextEditorWindow


class PhaseFourApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_file_manager_lists_directories_before_files_and_opens_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "folder"
            folder.mkdir()
            (root / "z.txt").write_text("hello", encoding="utf-8")
            (root / "a.txt").write_text("hello", encoding="utf-8")
            opened = []
            manager = FileManagerWindow(opened.append, root)
            manager.refresh()

            self.assertEqual(
                [manager.items.item(index).text() for index in range(manager.items.count())],
                ["folder", "a.txt", "z.txt"],
            )
            manager._activate_item(manager.items.item(0))
            self.assertEqual(manager.current_path, folder)
            manager.go_up()
            manager._activate_item(manager.items.item(1))
            self.assertEqual(opened, [str(root / "a.txt")])
            manager.close()

    def test_file_manager_hides_dotfiles_until_toggled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "visible.txt").write_text("hello", encoding="utf-8")
            (root / ".hidden.txt").write_text("secret", encoding="utf-8")
            manager = FileManagerWindow(lambda _path: None, root)
            manager.refresh()

            def listed() -> list[str]:
                return [
                    manager.items.item(index).text()
                    for index in range(manager.items.count())
                ]

            self.assertEqual(listed(), ["visible.txt"])
            manager.toggle_hidden_files()
            self.assertEqual(listed(), [".hidden.txt", "visible.txt"])
            manager.toggle_hidden_files()
            self.assertEqual(listed(), ["visible.txt"])
            manager.close()

    def test_file_manager_copies_moves_and_deletes_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("contents", encoding="utf-8")
            copy = root / "copy.txt"
            moved = root / "moved.txt"

            FileManagerWindow.transfer_path(source, copy, move=False)
            self.assertEqual(copy.read_text(encoding="utf-8"), "contents")
            FileManagerWindow.transfer_path(source, moved, move=True)
            self.assertFalse(source.exists())
            self.assertEqual(moved.read_text(encoding="utf-8"), "contents")

            manager = FileManagerWindow(
                lambda _path: None, root, trash_store=TrashStore(root / "trash")
            )
            manager.refresh()
            item = next(
                manager.items.item(index)
                for index in range(manager.items.count())
                if manager.items.item(index).text() == "copy.txt"
            )
            manager.items.setCurrentItem(item)
            with patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ):
                manager.delete_selected()
            self.assertFalse(copy.exists())
            manager.close()

    def test_text_editor_creates_opens_and_saves_utf8_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notiz.txt"
            path.write_text("Grüße", encoding="utf-8")
            editor = TextEditorWindow(path)
            self.assertEqual(editor.editor.toPlainText(), "Grüße")
            self.assertFalse(editor.editor.document().isModified())

            editor.editor.appendPlainText("neue Zeile")
            self.assertTrue(editor.windowTitle().startswith("notiz.txt *"))
            editor.save()
            self.assertEqual(
                path.read_text(encoding="utf-8"), "Grüße\nneue Zeile"
            )
            self.assertFalse(editor.editor.document().isModified())

            editor.new_file()
            self.assertIsNone(editor.file_path)
            self.assertEqual(editor.editor.toPlainText(), "")
            editor.close()

    def test_text_editor_save_as_sets_current_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "neu.txt"
            editor = TextEditorWindow()
            editor.editor.setPlainText("Text")
            with patch.object(
                QFileDialog, "getSaveFileName", return_value=(str(path), "")
            ):
                editor.save_as()
            self.assertEqual(path.read_text(encoding="utf-8"), "Text")
            self.assertEqual(editor.file_path, path)
            editor.close()

    def test_image_viewer_shows_and_cycles_neighboring_png_and_jpg_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "01.png"
            second = root / "02.jpg"
            third = root / "03.PNG"
            for path, color in (
                (first, Qt.GlobalColor.red),
                (second, Qt.GlobalColor.green),
                (third, Qt.GlobalColor.blue),
            ):
                image = QImage(40, 30, QImage.Format.Format_RGB32)
                image.fill(color)
                self.assertTrue(image.save(str(path)))

            viewer = ImageViewerWindow(second)
            viewer.show()
            self.app.processEvents()
            self.assertEqual(viewer.current_index, 1)
            self.assertEqual(viewer.windowTitle(), "02.jpg — Bilder")
            self.assertFalse(viewer.image_label.pixmap().isNull())

            viewer.show_next()
            self.assertEqual(viewer.image_paths[viewer.current_index], third)
            viewer.show_next()
            self.assertEqual(viewer.image_paths[viewer.current_index], first)
            viewer.show_previous()
            self.assertEqual(viewer.image_paths[viewer.current_index], third)
            viewer.close()

    def test_image_viewer_toggles_fullscreen(self) -> None:
        viewer = ImageViewerWindow()
        viewer.show()
        viewer.toggle_fullscreen()
        self.app.processEvents()
        self.assertTrue(viewer.isFullScreen())
        self.assertEqual(viewer.fullscreen_button.text(), "Fenster")
        viewer.toggle_fullscreen()
        self.app.processEvents()
        self.assertFalse(viewer.isFullScreen())
        self.assertEqual(viewer.fullscreen_button.text(), "Vollbild")
        viewer.close()

    def test_image_viewer_open_dialog_loads_selected_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bild.png"
            image = QImage(32, 24, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.cyan)
            self.assertTrue(image.save(str(path)))

            viewer = ImageViewerWindow()
            with patch.object(
                QFileDialog, "getOpenFileName", return_value=(str(path), "")
            ):
                viewer.open_dialog()
            self.assertEqual(viewer.image_paths, [path])
            self.assertEqual(viewer.windowTitle(), "bild.png — Bilder")
            viewer.close()


if __name__ == "__main__":
    unittest.main()
