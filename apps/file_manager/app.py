import mimetypes
import os
import shutil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFileInfo, QMimeData, QUrl, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMenu,
    QMainWindow,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apps.file_manager.shortcuts import create_desktop_shortcut
from apps.file_manager.transfer import (
    TransferResult,
    transfer_items,
)
from apps.file_manager.transfer_ui import run_transfer
from apps.file_manager.trash import TrashStore


class FileListWidget(QListWidget):
    paths_dropped = Signal(object, object, bool)
    key_command = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers()
        control = bool(
            modifiers
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        command = None
        if control and key == Qt.Key.Key_C:
            command = "copy"
        elif control and key == Qt.Key.Key_X:
            command = "cut"
        elif control and key == Qt.Key.Key_V:
            command = "paste"
        elif control and key == Qt.Key.Key_A:
            command = "select-all"
        elif control and shift and key == Qt.Key.Key_N:
            command = "new-folder"
        elif key == Qt.Key.Key_Delete:
            command = "delete"
        elif key == Qt.Key.Key_F2:
            command = "rename"
        elif key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            command = "open"
        elif key in {Qt.Key.Key_Backspace, Qt.Key.Key_Left} and (
            key == Qt.Key.Key_Backspace
            or modifiers & Qt.KeyboardModifier.AltModifier
        ):
            command = "back"
        if command is None:
            super().keyPressEvent(event)
        else:
            self.key_command.emit(command)
            event.accept()

    def mimeData(self, items: list[QListWidgetItem]) -> QMimeData:
        mime_data = QMimeData()
        mime_data.setUrls(
            [
                QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole))
                for item in items
            ]
        )
        return mime_data

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        item = self.itemAt(event.position().toPoint())
        destination = (
            Path(item.data(Qt.ItemDataRole.UserRole))
            if item is not None
            and Path(item.data(Qt.ItemDataRole.UserRole)).is_dir()
            else None
        )
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            copy = bool(event.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
            self.paths_dropped.emit(paths, destination, copy)
            event.acceptProposedAction()
            return
        event.ignore()


class FileManagerWindow(QMainWindow):
    def __init__(
        self,
        open_file: Callable[[str], None],
        start_path: Path | None = None,
        open_trash: Callable[[], None] | None = None,
        trash_store: TrashStore | None = None,
        desktop_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Dateien")
        self.setMinimumSize(520, 360)
        self.resize(700, 480)
        self._open_file = open_file
        self._open_trash = open_trash
        self.trash_store = trash_store or TrashStore()
        self.desktop_path = desktop_path or (Path.home() / "Desktop")
        self._search_root: Path | None = None
        self.current_path = (start_path or Path.home()).expanduser().absolute()

        content = QWidget()
        layout = QVBoxLayout(content)
        navigation = QHBoxLayout()

        for text, callback in (
            ("Zurück", self.go_back),
            ("Nach oben", self.go_up),
            ("Startordner", self.go_home),
            ("Aktualisieren", self.refresh),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            navigation.addWidget(button)

        self.path_label = QLabel()
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        search_row = QHBoxLayout()
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Dateien und Ordner suchen…")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.returnPressed.connect(self.search)
        search_button = QPushButton("Suchen")
        search_button.clicked.connect(self.search)
        self.clear_search_button = QPushButton("Suche beenden")
        self.clear_search_button.clicked.connect(self.clear_search)
        search_row.addWidget(self.search_field, 1)
        search_row.addWidget(search_button)
        search_row.addWidget(self.clear_search_button)

        self.items = FileListWidget()
        self.items.itemDoubleClicked.connect(self._activate_item)
        self.items.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.items.customContextMenuRequested.connect(self._show_context_menu)
        self.items.paths_dropped.connect(self._drop_paths)
        self.items.key_command.connect(self._handle_list_key)
        self.items.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        actions = QHBoxLayout()
        for text, callback in (
            ("Kopieren", self.copy_selected),
            ("Verschieben", self.move_selected),
            ("Löschen", self.delete_selected),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        self.trash_button = QPushButton("Papierkorb")
        self.trash_button.clicked.connect(self._show_trash)
        self.trash_button.setEnabled(open_trash is not None)
        actions.addWidget(self.trash_button)
        actions.addStretch(1)

        layout.addLayout(navigation)
        layout.addWidget(self.path_label)
        layout.addLayout(search_row)
        layout.addWidget(self.items, 1)
        layout.addLayout(actions)
        self.setCentralWidget(content)
        self.refresh()

    def refresh(self) -> None:
        if self._search_root is not None:
            self.search()
            return
        self.items.clear()
        self.path_label.setText(str(self.current_path))
        self.setWindowTitle(f"Dateien — {self.current_path.name or '/'}")
        try:
            entries = sorted(
                self.current_path.iterdir(),
                key=lambda entry: (not entry.is_dir(), entry.name.casefold()),
            )
        except OSError as error:
            self._show_error("Ordner kann nicht gelesen werden", error)
            return

        style = QApplication.style()
        for path in entries:
            icon = style.standardIcon(
                style.StandardPixmap.SP_DirIcon
                if path.is_dir()
                else style.StandardPixmap.SP_FileIcon
            )
            item = QListWidgetItem(icon, path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.items.addItem(item)

    def go_back(self) -> None:
        self._change_directory(self.current_path.parent)

    def go_up(self) -> None:
        self.go_back()

    def go_home(self) -> None:
        self._change_directory(Path.home())

    def copy_selected(self) -> None:
        self._transfer_selected(move=False)

    def move_selected(self) -> None:
        self._transfer_selected(move=True)

    def delete_selected(self) -> None:
        sources = self._selected_paths()
        if not sources:
            return
        errors = []
        for source in sources:
            try:
                self.trash_store.move_to_trash(source)
            except (OSError, ValueError) as error:
                errors.append(f"{source.name}: {error}")
        if errors:
            QMessageBox.warning(
                self,
                "Elemente konnten nicht gelöscht werden",
                "\n".join(errors[:8]),
            )
        self.refresh()

    def search(self) -> None:
        query = self.search_field.text().strip().casefold()
        if not query:
            self.clear_search()
            return

        root = self._search_root or self.current_path
        self._search_root = root
        self.items.clear()
        self.path_label.setText(f"Suchergebnisse in {root}")
        self.setWindowTitle(f"Suche — {root.name or '/'}")
        errors: list[OSError] = []
        try:
            for current, directories, files in os.walk(
                root, onerror=errors.append, followlinks=False
            ):
                directories.sort(key=str.casefold)
                files.sort(key=str.casefold)
                parent = Path(current)
                for name in (*directories, *files):
                    if query not in name.casefold():
                        continue
                    path = parent / name
                    relative = path.relative_to(root)
                    icon_kind = (
                        QApplication.style().StandardPixmap.SP_DirIcon
                        if path.is_dir()
                        else QApplication.style().StandardPixmap.SP_FileIcon
                    )
                    item = QListWidgetItem(
                        QApplication.style().standardIcon(icon_kind),
                        str(relative),
                    )
                    item.setData(Qt.ItemDataRole.UserRole, str(path))
                    item.setToolTip(str(path))
                    self.items.addItem(item)
        except OSError as error:
            errors.append(error)
        if errors:
            self._show_error(
                "Einige Ordner konnten nicht durchsucht werden",
                OSError("\n".join(str(error) for error in errors[:5])),
            )

    def clear_search(self) -> None:
        self._search_root = None
        self.search_field.clear()
        self.refresh()

    def show_properties(self, path: Path) -> None:
        try:
            info = path.stat()
        except OSError as error:
            self._show_error("Dateieigenschaften nicht verfügbar", error)
            return

        file_type = (
            "Ordner"
            if path.is_dir()
            else mimetypes.guess_type(path.name)[0] or "Unbekannter Dateityp"
        )
        try:
            size = self._path_size(path)
        except OSError as error:
            self._show_error("Dateieigenschaften nicht verfügbar", error)
            return
        birth_time = QFileInfo(str(path)).birthTime()
        if birth_time.isValid():
            creation_date = birth_time.toLocalTime().toString("dd.MM.yyyy HH:mm")
        else:
            fallback_birth_time = getattr(info, "st_birthtime", None)
            creation_date = (
                self._format_timestamp(fallback_birth_time)
                if fallback_birth_time is not None
                else "Nicht vom Dateisystem bereitgestellt"
            )
        details = (
            f"Größe: {self._format_size(size)}\n"
            f"Dateityp: {file_type}\n"
            f"Speicherort: {path.parent}\n"
            f"Erstellungsdatum: {creation_date}\n"
            f"Änderungsdatum: {self._format_timestamp(info.st_mtime)}"
        )
        QMessageBox.information(self, f"Eigenschaften – {path.name}", details)

    def _show_context_menu(self, position) -> None:
        item = self.items.itemAt(position)
        menu = self._build_context_menu(item)
        menu.exec(self.items.mapToGlobal(position))

    def _build_context_menu(self, item: QListWidgetItem | None) -> QMenu:
        if item is None:
            self.items.clearSelection()
            menu = QMenu(self)
            menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            menu.addAction("Neuer Ordner…", self.create_folder)
            menu.addAction("Einfügen", self.paste_clipboard).setEnabled(
                self._clipboard_has_urls()
            )
            menu.addAction("Aktualisieren", self.refresh)
            return menu
        if not item.isSelected():
            self.items.clearSelection()
            item.setSelected(True)
            self.items.setCurrentItem(item)
        paths = self._selected_paths()
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if len(paths) == 1:
            menu.addAction("Öffnen", lambda: self._open_path(paths[0]))
            menu.addAction("Eigenschaften", lambda: self.show_properties(paths[0]))
            menu.addAction("Umbenennen…", self.rename_selected)
            menu.addAction(
                "Desktop-Verknüpfung erstellen",
                lambda: self.create_desktop_shortcut(paths[0]),
            )
        menu.addSeparator()
        menu.addAction("Kopieren", self.copy_to_clipboard)
        menu.addAction("Ausschneiden", self.cut_to_clipboard)
        menu.addAction("Einfügen", self.paste_clipboard).setEnabled(
            self._clipboard_has_urls()
        )
        menu.addAction("Löschen", self.delete_selected)
        return menu

    def _show_trash(self) -> None:
        if self._open_trash is not None:
            self._open_trash()

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{size} B"

    @staticmethod
    def _format_timestamp(timestamp: float) -> str:
        from datetime import datetime

        return datetime.fromtimestamp(timestamp).astimezone().strftime("%d.%m.%Y %H:%M")

    @classmethod
    def _path_size(cls, path: Path) -> int:
        if path.is_symlink() or not path.is_dir():
            return path.lstat().st_size
        return path.lstat().st_size + sum(
            cls._path_size(child) for child in path.iterdir()
        )

    def _change_directory(self, path: Path) -> None:
        if not path.is_dir():
            QMessageBox.warning(
                self, "Ordner nicht gefunden", f"„{path}“ ist kein Ordner."
            )
            return
        self.current_path = path
        self._search_root = None
        self.search_field.clear()
        self.refresh()

    def _activate_item(self, item: QListWidgetItem) -> None:
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        self._open_path(path)

    def _open_path(self, path: Path) -> None:
        if path.is_dir():
            self._change_directory(path)
        else:
            self._open_file(str(path))

    def _selected_paths(self) -> list[Path]:
        return [
            Path(item.data(Qt.ItemDataRole.UserRole))
            for item in self.items.selectedItems()
        ]

    def _selected_path(self) -> Path | None:
        item = self.items.currentItem()
        if item is None:
            QMessageBox.information(
                self, "Nichts ausgewählt", "Wähle zuerst eine Datei oder einen Ordner."
            )
            return None
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def create_folder(self) -> None:
        name, accepted = QInputDialog.getText(
            self, "Neuen Ordner erstellen", "Ordnername:"
        )
        if not accepted or not name:
            return
        if Path(name).name != name or name in {".", ".."}:
            QMessageBox.warning(
                self, "Ungültiger Ordnername", "Gib einen einzelnen Ordnernamen ein."
            )
            return
        destination = self.current_path / name
        try:
            destination.mkdir()
        except OSError as error:
            self._show_error("Ordner kann nicht erstellt werden", error)
            return
        self.refresh()

    def rename_selected(self) -> None:
        source = self._selected_path()
        if source is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Element umbenennen", "Neuer Name:", text=source.name
        )
        if not accepted or not name:
            return
        if Path(name).name != name or name in {".", ".."}:
            QMessageBox.warning(
                self, "Ungültiger Name", "Gib einen einzelnen Datei- oder Ordnernamen ein."
            )
            return
        destination = source.with_name(name)
        if destination == source:
            return
        if destination.exists() or destination.is_symlink():
            QMessageBox.warning(
                self, "Name bereits vorhanden", f"„{destination.name}“ existiert bereits."
            )
            return
        try:
            source.rename(destination)
        except OSError as error:
            self._show_error("Element kann nicht umbenannt werden", error)
            return
        self.refresh()

    def create_desktop_shortcut(self, source: Path) -> Path | None:
        if not source.exists() and not source.is_symlink():
            QMessageBox.warning(self, "Datei nicht gefunden", str(source))
            return None
        try:
            result = create_desktop_shortcut(
                self.desktop_path, source.stem, target=source
            )
        except (OSError, ValueError) as error:
            self._show_error("Desktop-Verknüpfung kann nicht erstellt werden", error)
            return None
        return result

    def copy_to_clipboard(self) -> None:
        self._set_clipboard(cut=False)

    def cut_to_clipboard(self) -> None:
        self._set_clipboard(cut=True)

    def _set_clipboard(self, *, cut: bool) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        if cut:
            mime_data.setData("application/x-myos-cut", b"1")
        QApplication.clipboard().setMimeData(mime_data)

    def paste_clipboard(self) -> None:
        mime_data = QApplication.clipboard().mimeData()
        if mime_data is None:
            return
        paths = [Path(url.toLocalFile()) for url in mime_data.urls() if url.isLocalFile()]
        if not paths:
            return
        cut = mime_data.hasFormat("application/x-myos-cut")
        result = self._drop_paths(paths, self.current_path, not cut)
        if (
            cut
            and result is not None
            and result.moved
            and not result.cancelled
            and not result.errors
        ):
            QApplication.clipboard().clear()

    @staticmethod
    def _clipboard_has_urls() -> bool:
        mime_data = QApplication.clipboard().mimeData()
        return mime_data is not None and mime_data.hasUrls()

    def _drop_paths(
        self,
        paths: list[Path],
        destination: Path | None,
        copy: bool,
        *,
        conflict_handler: Callable[[Path, Path], object] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> TransferResult:
        target_directory = destination or self.current_path
        if conflict_handler is None:
            result = run_transfer(
                self, paths, target_directory, move=not copy
            )
        else:
            result = transfer_items(
                paths,
                target_directory,
                move=not copy,
                decide_conflict=conflict_handler,
                progress=progress,
                should_cancel=should_cancel,
            )
            self._show_transfer_errors(result.errors)
        self._after_transfer(result)
        self.refresh()
        return result

    def _after_transfer(self, result: TransferResult) -> None:
        if result.cancelled:
            self.statusBar().showMessage(
                f"Übertragung abgebrochen — {result.summary()}", 6000
            )
        elif result.changed:
            self.statusBar().showMessage(result.summary(), 6000)

    def _show_transfer_errors(self, errors: list[str]) -> None:
        if errors:
            QMessageBox.warning(
                self, "Einige Elemente konnten nicht übertragen werden", "\n".join(errors[:8])
            )

    def _transfer_selected(self, move: bool) -> None:
        sources = self._selected_paths()
        if not sources:
            self._selected_path()
            return
        destination = QFileDialog.getExistingDirectory(
            self,
            "Zielordner auswählen",
            str(self.current_path),
        )
        if not destination:
            return
        self._drop_paths(sources, Path(destination), copy=not move)

    @staticmethod
    def transfer_path(source: Path, target: Path, move: bool) -> None:
        if move:
            shutil.move(str(source), str(target))
        elif source.is_dir() and not source.is_symlink():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    def _show_error(self, title: str, error: OSError) -> None:
        QMessageBox.critical(self, title, str(error))

    def _activate_current(self) -> None:
        item = self.items.currentItem()
        if item is not None:
            self._activate_item(item)

    def _handle_list_key(self, command: str) -> None:
        actions = {
            "copy": self.copy_to_clipboard,
            "cut": self.cut_to_clipboard,
            "paste": self.paste_clipboard,
            "select-all": self.items.selectAll,
            "new-folder": self.create_folder,
            "delete": self.delete_selected,
            "rename": self.rename_selected,
            "open": self._activate_current,
            "back": self.go_back,
        }
        actions[command]()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers()
        control = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if control and key == Qt.Key.Key_C:
            self.copy_to_clipboard()
        elif control and key == Qt.Key.Key_X:
            self.cut_to_clipboard()
        elif control and key == Qt.Key.Key_V:
            self.paste_clipboard()
        elif control and key == Qt.Key.Key_A:
            self.items.selectAll()
        elif control and shift and key == Qt.Key.Key_N:
            self.create_folder()
        elif key == Qt.Key.Key_Delete:
            self.delete_selected()
        elif key == Qt.Key.Key_Backspace:
            self.go_back()
        elif key == Qt.Key.Key_F2:
            self.rename_selected()
        elif key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            item = self.items.currentItem()
            if item is not None:
                self._activate_item(item)
        else:
            super().keyPressEvent(event)
