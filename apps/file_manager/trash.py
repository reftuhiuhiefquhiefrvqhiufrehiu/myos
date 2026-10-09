import shutil
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class TrashEntry:
    stored_path: Path
    info_path: Path
    original_path: Path | None
    deleted_at: str


class TrashStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            root or Path.home() / ".local" / "share" / "MyOS" / "Trash"
        ).expanduser().absolute()
        self.files_path = self.root / "files"
        self.info_path = self.root / "info"
        self.error = ""

    def move_to_trash(self, source: Path) -> TrashEntry:
        source = source.expanduser().absolute()
        if not source.exists() and not source.is_symlink():
            raise FileNotFoundError(source)
        if source == self.root or self.root in source.parents:
            raise ValueError("Der Papierkorb kann nicht in sich selbst verschoben werden.")

        self.files_path.mkdir(parents=True, exist_ok=True)
        self.info_path.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        stored_path = self.files_path / identifier
        info_path = self.info_path / f"{identifier}.trashinfo"
        deleted_at = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        shutil.move(str(source), str(stored_path))
        encoded_path = urllib.parse.quote(source.as_posix(), safe="/")
        try:
            info_path.write_text(
                "[Trash Info]\n"
                f"Path={encoded_path}\n"
                f"DeletionDate={deleted_at}\n",
                encoding="utf-8",
            )
        except OSError:
            shutil.move(str(stored_path), str(source))
            raise
        return TrashEntry(stored_path, info_path, source, deleted_at)

    def entries(self) -> list[TrashEntry]:
        self.error = ""
        if not self.files_path.exists():
            return []
        entries: list[TrashEntry] = []
        for stored_path in sorted(self.files_path.iterdir(), key=lambda path: path.name):
            info_path = self.info_path / f"{stored_path.name}.trashinfo"
            original_path: Path | None = None
            deleted_at = ""
            try:
                values = {}
                for line in info_path.read_text(encoding="utf-8").splitlines():
                    key, separator, value = line.partition("=")
                    if separator:
                        values[key] = value
                encoded_path = values.get("Path")
                if encoded_path:
                    original_path = Path(
                        urllib.parse.unquote(encoded_path)
                    )
                deleted_at = values.get("DeletionDate", "")
            except (OSError, UnicodeError, ValueError) as error:
                self.error = f"Papierkorb-Metadaten konnten nicht gelesen werden: {error}"
            entries.append(
                TrashEntry(stored_path, info_path, original_path, deleted_at)
            )
        return entries

    def restore(self, entry: TrashEntry) -> Path:
        self._validate_entry(entry)
        if entry.original_path is None:
            raise ValueError("Der ursprüngliche Speicherort ist nicht verfügbar.")
        destination = entry.original_path
        if not destination.is_absolute():
            raise ValueError("Der ursprüngliche Speicherort ist ungültig.")
        if destination.exists() or destination.is_symlink():
            suffix = 1
            while True:
                candidate = destination.with_name(
                    f"{destination.stem} (wiederhergestellt {suffix}){destination.suffix}"
                )
                if not candidate.exists() and not candidate.is_symlink():
                    destination = candidate
                    break
                suffix += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry.stored_path), str(destination))
        entry.info_path.unlink(missing_ok=True)
        return destination

    def size(self) -> int:
        total = 0
        for entry in self.entries():
            total += self._path_size(entry.stored_path)
        return total

    def delete_permanently(self, entry: TrashEntry) -> None:
        self._validate_entry(entry)
        self._remove_path(entry.stored_path)
        entry.info_path.unlink(missing_ok=True)

    def _validate_entry(self, entry: TrashEntry) -> None:
        if (
            entry.stored_path.parent.absolute() != self.files_path.absolute()
            or entry.info_path.parent.absolute() != self.info_path.absolute()
            or entry.info_path.name != f"{entry.stored_path.name}.trashinfo"
        ):
            raise ValueError("Das Element gehört nicht zu diesem Papierkorb.")

    def empty(self) -> list[str]:
        errors: list[str] = []
        if not self.files_path.exists():
            return errors
        for entry in self.entries():
            try:
                self.delete_permanently(entry)
            except OSError as error:
                errors.append(f"{entry.stored_path.name}: {error}")
        return errors

    @classmethod
    def _path_size(cls, path: Path) -> int:
        if path.is_symlink() or not path.is_dir():
            return path.lstat().st_size
        return path.lstat().st_size + sum(
            cls._path_size(child) for child in path.iterdir()
        )

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


class TrashListWidget(QListWidget):
    key_command = Signal(str)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.key_command.emit("delete")
        elif event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.key_command.emit("restore")
        elif (
            event.key() == Qt.Key.Key_A
            and event.modifiers()
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        ):
            self.key_command.emit("select-all")
        else:
            super().keyPressEvent(event)


class TrashWindow(QMainWindow):
    def __init__(self, store: TrashStore | None = None) -> None:
        super().__init__()
        self.store = store or TrashStore()
        self.setWindowTitle("Papierkorb")
        self.setMinimumSize(400, 300)
        self.resize(560, 400)
        content = QWidget()
        layout = QVBoxLayout(content)
        heading = QLabel("Papierkorb")
        heading.setObjectName("heading")
        self.items = TrashListWidget()
        self.items.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.items.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.items.customContextMenuRequested.connect(self._show_context_menu)
        self.items.itemDoubleClicked.connect(lambda _item: self.restore_selected())
        self.items.key_command.connect(self._handle_key_command)
        self.status = QLabel()
        self.status.setWordWrap(True)
        buttons = QHBoxLayout()
        self.restore_button = QPushButton("Wiederherstellen")
        self.restore_button.clicked.connect(self.restore_selected)
        self.delete_button = QPushButton("Endgültig löschen…")
        self.delete_button.clicked.connect(self.delete_selected)
        self.empty_button = QPushButton("Papierkorb leeren…")
        self.empty_button.clicked.connect(self.empty_trash)
        self.refresh_button = QPushButton("Aktualisieren")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.empty_button)
        buttons.addWidget(self.refresh_button)
        layout.addWidget(heading)
        layout.addWidget(self.items, 1)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        self.setCentralWidget(content)
        self.refresh()

    def refresh(self) -> None:
        self.items.clear()
        try:
            entries = self.store.entries()
            total = FileManagerSize.format(self.store.size())
        except OSError as error:
            self.status.setText(f"Papierkorb kann nicht gelesen werden: {error}")
            self.restore_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.empty_button.setEnabled(False)
            return
        errors: list[str] = []
        for entry in entries:
            try:
                size = FileManagerSize.format(self.store._path_size(entry.stored_path))
            except OSError as error:
                size = "Größe nicht verfügbar"
                errors.append(f"{entry.stored_path.name}: {error}")
            original = str(entry.original_path) if entry.original_path else "Ursprung unbekannt"
            item = QListWidgetItem(
                f"{entry.original_path.name if entry.original_path else entry.stored_path.name}  ·  {size}\n"
                f"{entry.deleted_at or 'Datum unbekannt'}  ·  {original}"
            )
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(f"{original}\n{size}")
            self.items.addItem(item)
        self.status.setText(
            f"{len(entries)} Elemente · {total}"
            + (f"\n{self.store.error}" if self.store.error else "")
            + (f"\n{'; '.join(errors[:3])}" if errors else "")
        )
        self.restore_button.setEnabled(bool(entries))
        self.delete_button.setEnabled(bool(entries))
        self.empty_button.setEnabled(bool(entries))

    def restore_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            QMessageBox.information(self, "Nichts ausgewählt", "Wähle ein Element aus.")
            return
        restored: list[str] = []
        errors: list[str] = []
        for entry in entries:
            try:
                destination = self.store.restore(entry)
                restored.append(str(destination))
            except (OSError, ValueError) as error:
                errors.append(f"{entry.stored_path.name}: {error}")
        self.status.setText(
            f"Wiederhergestellt: {', '.join(restored[:3])}"
            + (f"\nFehler: {'; '.join(errors[:3])}" if errors else "")
        )
        self.refresh()

    def _selected_entries(self) -> list[TrashEntry]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.items.selectedItems()
        ]

    def _handle_key_command(self, command: str) -> None:
        if command == "delete":
            self.delete_selected()
        elif command == "restore":
            self.restore_selected()
        elif command == "select-all":
            self.items.selectAll()

    def delete_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            QMessageBox.information(self, "Nichts ausgewählt", "Wähle ein Element aus.")
            return
        answer = QMessageBox.question(
            self,
            "Elemente endgültig löschen",
            f"{len(entries)} Element(e) endgültig löschen? Dieser Vorgang kann nicht rückgängig gemacht werden.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        errors = []
        for entry in entries:
            try:
                self.store.delete_permanently(entry)
            except (OSError, ValueError) as error:
                errors.append(f"{entry.stored_path.name}: {error}")
        if errors:
            QMessageBox.warning(
                self, "Einige Elemente konnten nicht gelöscht werden", "\n".join(errors[:8])
            )
        self.refresh()

    def _show_context_menu(self, position) -> None:
        item = self.items.itemAt(position)
        if item is None:
            self.items.clearSelection()
            return
        if not item.isSelected():
            self.items.clearSelection()
            item.setSelected(True)
            self.items.setCurrentItem(item)
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.addAction("Wiederherstellen", self.restore_selected)
        menu.addAction("Endgültig löschen…", self.delete_selected)
        menu.addAction("Papierkorb leeren…", self.empty_trash)
        menu.exec(self.items.mapToGlobal(position))

    def empty_trash(self) -> None:
        answer = QMessageBox.question(
            self,
            "Papierkorb endgültig leeren",
            "Alle Elemente im Papierkorb endgültig löschen? Dieser Vorgang kann nicht rückgängig gemacht werden.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        errors = self.store.empty()
        if errors:
            QMessageBox.warning(
                self,
                "Papierkorb teilweise geleert",
                "Einige Elemente konnten nicht endgültig gelöscht werden:\n"
                + "\n".join(errors[:8]),
            )
        self.refresh()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected()
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.restore_selected()
            return
        if (
            event.key() == Qt.Key.Key_A
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.items.selectAll()
            return
        super().keyPressEvent(event)


class FileManagerSize:
    @staticmethod
    def format(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{size} B"
