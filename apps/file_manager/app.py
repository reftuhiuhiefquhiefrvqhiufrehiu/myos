import mimetypes
import os
import shutil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFileInfo, Qt
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
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apps.file_manager.trash import TrashStore


class FileManagerWindow(QMainWindow):
    def __init__(
        self,
        open_file: Callable[[str], None],
        start_path: Path | None = None,
        open_trash: Callable[[], None] | None = None,
        trash_store: TrashStore | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Dateien")
        self.setMinimumSize(520, 360)
        self.resize(700, 480)
        self._open_file = open_file
        self._open_trash = open_trash
        self.trash_store = trash_store or TrashStore()
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

        self.items = QListWidget()
        self.items.itemDoubleClicked.connect(self._activate_item)
        self.items.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.items.customContextMenuRequested.connect(self._show_context_menu)

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
        source = self._selected_path()
        if source is None:
            return
        try:
            self.trash_store.move_to_trash(source)
        except (OSError, ValueError) as error:
            self._show_error("Element kann nicht in den Papierkorb verschoben werden", error)
            return
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
        if item is None:
            return
        self.items.setCurrentItem(item)
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        if path.is_dir():
            open_action = menu.addAction("Öffnen")
            open_action.triggered.connect(lambda: self._activate_item(item))
        else:
            open_action = menu.addAction("Öffnen")
            open_action.triggered.connect(lambda: self._open_file(str(path)))
        properties_action = menu.addAction("Eigenschaften")
        properties_action.triggered.connect(lambda: self.show_properties(path))
        menu.exec(self.items.mapToGlobal(position))

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
        if path.is_dir():
            self._change_directory(path)
        else:
            self._open_file(str(path))

    def _selected_path(self) -> Path | None:
        item = self.items.currentItem()
        if item is None:
            QMessageBox.information(
                self, "Nichts ausgewählt", "Wähle zuerst eine Datei oder einen Ordner."
            )
            return None
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def _transfer_selected(self, move: bool) -> None:
        source = self._selected_path()
        if source is None:
            return
        destination = QFileDialog.getExistingDirectory(
            self,
            "Zielordner auswählen",
            str(self.current_path),
        )
        if not destination:
            return
        target = Path(destination) / source.name
        if source == target or source in target.parents:
            QMessageBox.warning(
                self, "Ungültiger Zielordner", "Der Zielordner liegt innerhalb der Quelle."
            )
            return
        try:
            self.transfer_path(source, target, move)
        except OSError as error:
            self._show_error(
                "Element kann nicht verschoben werden"
                if move
                else "Element kann nicht kopiert werden",
                error,
            )
            return
        self.refresh()

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
