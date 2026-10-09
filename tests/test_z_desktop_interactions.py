import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from apps.file_manager.app import FileManagerWindow
from apps.file_manager.shortcuts import create_desktop_shortcut, read_desktop_shortcut
from apps.file_manager.transfer import (
    RENAME,
    REPLACE,
    SKIP,
    ConflictDecision,
)
from apps.file_manager.trash import TrashStore, TrashWindow
from desktop.shell import DesktopShell
from main import file_application


class DesktopInteractionTests(unittest.TestCase):
    """Keep Qt widget lifetime checks last in the unittest discovery order."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.addCleanup(QApplication.clipboard().clear)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings_path = self.root / "desktop.ini"
        self.settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)

    def close_widget(self, widget) -> None:
        widget.close()
        widget.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_common_file_extensions_map_to_matching_myos_applications(self) -> None:
        for filename, application in (
            ("picture.PNG", "pictures"),
            ("track.ogg", "music"),
            ("page.HTML", "code"),
            ("component.tsx", "code"),
            ("document.txt", "editor"),
            ("data.json", "code"),
            ("unknown.xyz", None),
        ):
            with self.subTest(filename=filename):
                self.assertEqual(file_application(filename), application)

    def test_desktop_shortcut_round_trips_file_paths_and_app_ids(self) -> None:
        desktop = self.root / "Desktop"
        target = self.root / "Text with spaces.txt"
        target.touch()
        file_link = create_desktop_shortcut(desktop, "Notiz", target=target)
        app_link = create_desktop_shortcut(desktop, "Dateien", app_id="files")

        self.assertEqual(read_desktop_shortcut(file_link), (target, None))
        self.assertEqual(read_desktop_shortcut(app_link), (None, "files"))
        self.assertEqual(file_link.name, "Notiz.desktop")

    def test_shortcut_reader_rejects_shell_exec_launchers(self) -> None:
        link = self.root / "unsafe.desktop"
        link.write_text(
            "[Desktop Entry]\nType=Application\nExec=sh -c 'touch /tmp/pwned'\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Nur sichere NeonVeil"):
            read_desktop_shortcut(link)
        link.write_text(
            "[Desktop Entry]\nType=Link\nURL=file:///first\nURL=file:///second\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Verknüpfung ist ungültig"):
            read_desktop_shortcut(link)

    def test_desktop_opens_links_and_trashes_shortcuts_without_deleting_targets(self) -> None:
        desktop_path = self.root / "Desktop"
        target = self.root / "notes.txt"
        target.write_text("keep", encoding="utf-8")
        link = create_desktop_shortcut(desktop_path, "Notizen", target=target)
        app_link = create_desktop_shortcut(
            desktop_path, "Einstellungen", app_id="settings"
        )
        store = TrashStore(self.root / "trash")
        desktop = DesktopShell(desktop_path, store, self.settings)
        opened = []
        opened_apps = []
        desktop.file_requested.connect(opened.append)
        desktop.application_requested.connect(opened_apps.append)
        shortcut_item = next(
            desktop.shortcuts.item(index)
            for index in range(desktop.shortcuts.count())
            if desktop.shortcuts.item(index).data(Qt.ItemDataRole.UserRole)
            == f"file:{link}"
        )

        desktop._open_shortcut(shortcut_item)
        self.assertEqual(opened, [str(target)])
        app_item = next(
            desktop.shortcuts.item(index)
            for index in range(desktop.shortcuts.count())
            if desktop.shortcuts.item(index).data(Qt.ItemDataRole.UserRole)
            == f"file:{app_link}"
        )
        desktop._open_shortcut(app_item)
        self.assertEqual(opened_apps, ["settings"])
        desktop._trash_path(link)
        self.assertFalse(link.exists())
        self.assertTrue(target.exists())
        self.assertEqual(len(store.entries()), 1)
        self.close_widget(desktop)

    def test_desktop_keeps_multiple_icon_columns_visible(self) -> None:
        desktop_path = self.root / "Desktop"
        desktop_path.mkdir()
        for index in range(10):
            (desktop_path / f"item-{index}.txt").touch()
        desktop = DesktopShell(desktop_path, settings=self.settings)
        desktop.resize(1024, 250)
        self.assertGreater(desktop.shortcuts.width(), 132)
        self.close_widget(desktop)

    def test_file_manager_clipboard_copy_and_cut_paste_use_selection(self) -> None:
        source_dir = self.root / "source"
        copy_dir = self.root / "copy"
        move_dir = self.root / "move"
        for path in (source_dir, copy_dir, move_dir):
            path.mkdir()
        source = source_dir / "note.txt"
        source.write_text("hello", encoding="utf-8")
        manager = FileManagerWindow(lambda _path: None, source_dir)
        self.addCleanup(self.close_widget, manager)
        manager.items.item(0).setSelected(True)

        manager.copy_to_clipboard()
        manager.current_path = copy_dir
        manager.paste_clipboard()
        self.assertEqual((copy_dir / source.name).read_text(encoding="utf-8"), "hello")
        self.assertTrue(source.exists())

        manager.current_path = source_dir
        manager.refresh()
        manager.items.item(0).setSelected(True)
        manager.cut_to_clipboard()
        manager.current_path = move_dir
        manager.paste_clipboard()
        self.assertFalse(source.exists())
        self.assertEqual((move_dir / source.name).read_text(encoding="utf-8"), "hello")

    def test_file_manager_drop_moves_files_and_resolves_conflicts(self) -> None:
        source_dir = self.root / "source"
        target_dir = self.root / "target"
        source_dir.mkdir()
        target_dir.mkdir()
        source = source_dir / "note.txt"
        source.write_text("hello", encoding="utf-8")
        manager = FileManagerWindow(lambda _path: None, source_dir)
        self.addCleanup(self.close_widget, manager)

        manager._drop_paths([source], target_dir, copy=False)
        self.assertFalse(source.exists())
        self.assertTrue((target_dir / source.name).exists())

        copied = source_dir / "other.txt"
        copied.write_text("copy", encoding="utf-8")
        existing = target_dir / copied.name
        existing.write_text("existing", encoding="utf-8")

        result = manager._drop_paths(
            [copied],
            target_dir,
            copy=True,
            conflict_handler=lambda _source, _target: ConflictDecision(SKIP),
        )
        self.assertTrue(copied.exists())
        self.assertEqual(existing.read_text(encoding="utf-8"), "existing")
        self.assertEqual(len(result.skipped), 1)

        result = manager._drop_paths(
            [copied],
            target_dir,
            copy=True,
            conflict_handler=lambda _source, _target: ConflictDecision(REPLACE),
        )
        self.assertEqual(existing.read_text(encoding="utf-8"), "copy")
        self.assertEqual(len(result.replaced), 1)

        result = manager._drop_paths(
            [copied],
            target_dir,
            copy=True,
            conflict_handler=lambda _source, _target: ConflictDecision(
                RENAME, new_name="renamed.txt"
            ),
        )
        self.assertTrue((target_dir / "renamed.txt").exists())
        self.assertEqual(len(result.renamed), 1)

    def test_file_manager_clipboard_copies_whole_folders(self) -> None:
        source_dir = self.root / "source"
        destination = self.root / "destination"
        tree = source_dir / "tree"
        (tree / "inner").mkdir(parents=True)
        destination.mkdir()
        (tree / "inner" / "note.txt").write_text("data", encoding="utf-8")
        manager = FileManagerWindow(lambda _path: None, source_dir)
        self.addCleanup(self.close_widget, manager)
        manager.items.item(0).setSelected(True)

        manager.copy_to_clipboard()
        manager.current_path = destination
        manager.paste_clipboard()

        self.assertTrue((destination / "tree" / "inner" / "note.txt").is_file())
        self.assertTrue(tree.is_dir())

    def test_desktop_drop_onto_folder_shortcut_moves_files(self) -> None:
        desktop_path = self.root / "Desktop"
        folder = self.root / "Dokumente"
        folder.mkdir()
        link = create_desktop_shortcut(desktop_path, "Dokumente", target=folder)
        desktop = DesktopShell(
            desktop_path, TrashStore(self.root / "trash"), self.settings
        )
        self.addCleanup(self.close_widget, desktop)
        item = next(
            desktop.shortcuts.item(index)
            for index in range(desktop.shortcuts.count())
            if desktop.shortcuts.item(index).data(Qt.ItemDataRole.UserRole)
            == f"file:{link}"
        )
        self.assertEqual(desktop.shortcuts._folder_target_for_item(item), folder)

        source = self.root / "note.txt"
        source.write_text("data", encoding="utf-8")
        desktop._drop_into_folder([source], folder, copy=False)
        self.assertFalse(source.exists())
        self.assertTrue((folder / "note.txt").is_file())

    def test_delete_key_moves_selected_file_to_recoverable_trash(self) -> None:
        folder = self.root / "folder"
        folder.mkdir()
        source = folder / "delete.txt"
        source.write_text("recover me", encoding="utf-8")
        store = TrashStore(self.root / "trash")
        manager = FileManagerWindow(
            lambda _path: None, folder, trash_store=store
        )
        self.addCleanup(self.close_widget, manager)
        manager.show()
        manager.items.setFocus()
        manager.items.item(0).setSelected(True)
        manager.items.setCurrentRow(0)
        self.app.processEvents()

        QTest.keyClick(manager.items, Qt.Key.Key_Delete)
        self.app.processEvents()

        self.assertFalse(source.exists())
        self.assertEqual(len(store.entries()), 1)

    def test_enter_in_search_field_does_not_open_selected_file(self) -> None:
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "note.txt").touch()
        opened = []
        manager = FileManagerWindow(opened.append, folder)
        self.addCleanup(self.close_widget, manager)
        manager.show()
        manager.items.setCurrentRow(0)
        manager.search_field.setFocus()
        self.app.processEvents()

        QTest.keyClick(manager.search_field, Qt.Key.Key_Return)
        self.app.processEvents()

        self.assertEqual(opened, [])

    def test_file_manager_context_menu_actions_follow_current_selection(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            (root / "one.txt").touch()
            manager = FileManagerWindow(lambda _path: None, root)
            self.addCleanup(self.close_widget, manager)

            menu = manager._build_context_menu(manager.items.item(0))
            labels = [action.text() for action in menu.actions()]
            self.assertIn("Desktop-Verknüpfung erstellen", labels)
            self.assertIn("Löschen", labels)
            menu.close()
            menu.deleteLater()

            menu = manager._build_context_menu(None)
            labels = [action.text() for action in menu.actions()]
            self.assertIn("Neuer Ordner…", labels)
            menu.close()
            menu.deleteLater()
            self.app.processEvents()

    def test_trash_individual_permanent_delete_is_confirmed_and_multi_restore_works(self) -> None:
        store = TrashStore(self.root / "trash")
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_text("1", encoding="utf-8")
        second.write_text("2", encoding="utf-8")
        store.move_to_trash(first)
        store.move_to_trash(second)
        window = TrashWindow(store)
        self.addCleanup(self.close_widget, window)
        window.items.item(0).setSelected(True)
        window.items.item(1).setSelected(True)
        window.restore_selected()
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

        entry_path = self.root / "permanent.txt"
        entry_path.write_text("delete", encoding="utf-8")
        store.move_to_trash(entry_path)
        window.refresh()
        window.items.setCurrentRow(window.items.count() - 1)
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.No
        ):
            window.delete_selected()
        self.assertEqual(len(store.entries()), 1)
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            window.delete_selected()
        self.assertEqual(store.entries(), [])

    def test_delete_key_in_trash_uses_permanent_delete_confirmation(self) -> None:
        source = self.root / "file.txt"
        source.write_text("trash", encoding="utf-8")
        store = TrashStore(self.root / "trash")
        store.move_to_trash(source)
        window = TrashWindow(store)
        self.addCleanup(self.close_widget, window)
        window.show()
        window.items.setFocus()
        window.items.setCurrentRow(0)
        self.app.processEvents()
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            QTest.keyClick(window.items, Qt.Key.Key_Delete)
        self.app.processEvents()
        self.assertEqual(store.entries(), [])


if __name__ == "__main__":
    unittest.main()
