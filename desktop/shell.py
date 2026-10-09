import mimetypes
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFileInfo, QPoint, QSettings, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from apps.file_manager.shortcuts import create_desktop_shortcut, read_desktop_shortcut
from apps.file_manager.transfer_ui import run_transfer
from apps.file_manager.trash import TrashStore
from theme import NEON, global_stylesheet, normalize_theme


class DesktopShortcutList(QListWidget):
    files_dropped = Signal(object)
    paths_dropped_into = Signal(object, object, bool)
    key_command = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(False)

    def mimeData(self, items: list[QListWidgetItem]):
        from PySide6.QtCore import QMimeData, QUrl

        mime_data = QMimeData()
        mime_data.setUrls(
            [
                QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole + 1))
                for item in items
                if item.data(Qt.ItemDataRole.UserRole + 1)
            ]
        )
        return mime_data

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if not paths:
            event.ignore()
            return
        copy = bool(event.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        target_directory = self._folder_target_at(event.position().toPoint())
        if target_directory is not None:
            self.paths_dropped_into.emit(paths, target_directory, copy)
            event.acceptProposedAction()
            return
        self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def _folder_target_at(self, position) -> Path | None:
        return self._folder_target_for_item(self.itemAt(position))

    def _folder_target_for_item(self, item: QListWidgetItem | None) -> Path | None:
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        if not value or not value.startswith("file:"):
            return None
        path = Path(value.removeprefix("file:"))
        if path.suffix.casefold() == ".desktop":
            try:
                target, app_id = read_desktop_shortcut(path)
            except (OSError, ValueError):
                return None
            if app_id is None and target is not None and target.is_dir():
                return target
            return None
        if path.is_dir():
            return path
        return None

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.key_command.emit("delete")
        elif event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.key_command.emit("open")
        else:
            super().keyPressEvent(event)


class DesktopShell(QWidget):
    application_requested = Signal(str)
    file_requested = Signal(str)

    APPLICATIONS = (
        ("files", "Dateien"),
        ("editor", "Texteditor"),
        ("pictures", "Bilder"),
        ("clock", "Uhr"),
        ("terminal", "Terminal"),
        ("code", "Code Studio"),
        ("browser", "Browser"),
        ("downloads", "Downloads"),
    )
    APPLICATION_TITLES = {
        "welcome": "Willkommen bei NeonVeil",
        "files": "Dateien",
        "editor": "Texteditor",
        "pictures": "Bilder",
        "clock": "Uhr",
        "terminal": "Terminal",
        "code": "Code Studio",
        "browser": "Browser",
        "downloads": "Downloads",
        "settings": "Einstellungen",
        "about": "Über NeonVeil",
        "notifications": "Benachrichtigungen",
        "music": "Musik",
        "screenshot": "Screenshot",
        "trash": "Papierkorb",
        "pi-tools": "Raspberry-Pi-Werkzeuge",
        "update-manager": "NeonVeil Update Manager",
    }

    def __init__(
        self,
        desktop_path: Path | None = None,
        trash_store: TrashStore | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnBottomHint,
        )
        self.setWindowTitle("NeonVeil")
        preferences = settings or QSettings("neonveil", "neonveil")
        self.preferences = preferences
        self.desktop_path = (desktop_path or Path.home() / "Desktop").expanduser().absolute()
        self.trash_store = trash_store or TrashStore()
        self.wallpaper_style = preferences.value("wallpaper/style", "lagoon", type=str)
        self.wallpaper_image_path = preferences.value("wallpaper/image", "", type=str)
        self.wallpaper_pixmap = (
            QPixmap(self.wallpaper_image_path)
            if self.wallpaper_style == "custom" and self.wallpaper_image_path
            else QPixmap()
        )
        self.theme = preferences.value("appearance/theme", "light", type=str)
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        else:
            self.resize(1024, 768)

        self.shortcuts = DesktopShortcutList(self)
        self.shortcuts.setViewMode(QListWidget.ViewMode.IconMode)
        self.shortcuts.setFlow(QListWidget.Flow.TopToBottom)
        self.shortcuts.setMovement(QListWidget.Movement.Static)
        self.shortcuts.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.shortcuts.setIconSize(QSize(40, 40))
        self.shortcuts.setGridSize(QSize(110, 78))
        self.shortcuts.setStyleSheet(
            """
            QListWidget {
                background: transparent;
                border: none;
                color: #f2f7f7;
                outline: none;
            }
            QListWidget::item {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 5px;
            }
            QListWidget::item:selected {
                background: rgba(28, 114, 138, 170);
                border-color: rgba(218, 246, 246, 180);
            }
            QListWidget::item:hover {
                background: rgba(218, 246, 246, 40);
                border-color: rgba(218, 246, 246, 120);
            }
            """
        )
        self.shortcuts.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.shortcuts.customContextMenuRequested.connect(self._show_shortcut_menu)
        self.shortcuts.files_dropped.connect(self.create_file_shortcuts)
        self.shortcuts.paths_dropped_into.connect(self._drop_into_folder)
        self.shortcuts.key_command.connect(self._handle_shortcut_key)
        self.refresh_shortcuts()
        self.shortcuts.itemDoubleClicked.connect(self._open_shortcut)

    def refresh_shortcuts(self) -> None:
        self.shortcuts.clear()
        hidden = set(self.preferences.value("desktop/hidden_app_shortcuts", [], type=list))
        standard_icons = {
            "files": self.style().StandardPixmap.SP_DirIcon,
            "editor": self.style().StandardPixmap.SP_FileIcon,
            "pictures": self.style().StandardPixmap.SP_DesktopIcon,
            "clock": self.style().StandardPixmap.SP_ComputerIcon,
            "terminal": self.style().StandardPixmap.SP_ComputerIcon,
            "code": self.style().StandardPixmap.SP_FileIcon,
            "browser": self.style().StandardPixmap.SP_DesktopIcon,
            "downloads": getattr(
                self.style().StandardPixmap,
                "SP_DownloadIcon",
                self.style().StandardPixmap.SP_DirIcon,
            ),
        }
        for app_id, label in self.APPLICATIONS:
            if app_id not in hidden:
                item = QListWidgetItem(
                    self.style().standardIcon(standard_icons[app_id]), label
                )
                item.setData(Qt.ItemDataRole.UserRole, f"app:{app_id}")
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
                item.setToolTip(f"{label} öffnen")
                self.shortcuts.addItem(item)

        if not self.desktop_path.is_dir():
            self._resize_shortcut_grid()
            return
        try:
            paths = sorted(
                self.desktop_path.iterdir(), key=lambda value: value.name.casefold()
            )
        except OSError as error:
            QMessageBox.warning(self, "Desktop-Ordner kann nicht gelesen werden", str(error))
            self._resize_shortcut_grid()
            return
        for path in paths:
            if path.name.startswith("."):
                continue
            label = path.stem if path.suffix.casefold() == ".desktop" else path.name
            icon_kind = (
                self.style().StandardPixmap.SP_DirIcon
                if path.is_dir()
                else self.style().StandardPixmap.SP_FileIcon
            )
            item = QListWidgetItem(self.style().standardIcon(icon_kind), label)
            item.setData(Qt.ItemDataRole.UserRole, f"file:{path}")
            item.setData(Qt.ItemDataRole.UserRole + 1, str(path))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setToolTip(str(path))
            self.shortcuts.addItem(item)
        self._resize_shortcut_grid()

    def _resize_shortcut_grid(self) -> None:
        self.shortcuts.setFixedWidth(max(0, self.width() - 36))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        if self.wallpaper_style == "custom" and self.wallpaper_image_path:
            if not self.wallpaper_pixmap.isNull():
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(self.rect(), self.wallpaper_pixmap)
                painter.fillRect(self.rect(), QColor(10, 30, 40, 55))
            else:
                self._paint_gradient(painter, ("#12354b", "#176c78", "#263d66"))
        else:
            gradients = {
                "lagoon": ("#12354b", "#176c78", "#263d66"),
                "morning": ("#23405d", "#568a92", "#d2a985"),
                "graphite": ("#26343d", "#465d68", "#263d66"),
            }
            self._paint_gradient(
                painter,
                gradients.get(self.wallpaper_style, gradients["lagoon"]),
            )
        if self.theme == "dark":
            painter.fillRect(self.rect(), QColor(5, 13, 18, 78))

        # A soft neon bloom in the upper corner gives the desktop depth.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow_center = QPoint(int(self.width() * 0.2), int(self.height() * 0.24))
        glow = QRadialGradient(
            glow_center, max(self.width(), self.height()) * 0.7
        )
        glow.setColorAt(0.0, QColor(53, 201, 189, 64))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(self.rect(), glow)

        painter.setPen(QPen(QColor(142, 226, 218, 48), 1))
        step = 52
        for x in range(0, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.drawLine(0, y, self.width(), y)

        vignette = QRadialGradient(
            self.rect().center(), max(self.width(), self.height()) * 0.72
        )
        vignette.setColorAt(0.55, QColor(0, 0, 0, 0))
        vignette.setColorAt(1.0, QColor(2, 10, 14, 105))
        painter.fillRect(self.rect(), vignette)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        title_font = painter.font()
        title_font.setBold(True)
        title_font.setPointSizeF(max(13.0, title_font.pointSizeF() + 6))
        painter.setFont(title_font)
        painter.setPen(QColor(NEON))
        painter.drawText(37, self.height() - 36, "NeonVeil")
        painter.setPen(QColor("#f2f7f7"))
        painter.drawText(36, self.height() - 38, "NeonVeil")
        painter.setFont(QFont(title_font.family(), max(8.0, title_font.pointSizeF() - 7)))
        painter.setPen(QColor(230, 246, 245, 200))
        painter.drawText(
            36,
            self.height() - 18,
            "Ein eigener Desktop für Raspberry Pi",
        )
        painter.fillRect(36, self.height() - 31, 62, 3, QColor(NEON))

    def set_wallpaper(self, style: str, image_path: str = "") -> None:
        self.wallpaper_style = style
        self.wallpaper_image_path = image_path
        self.wallpaper_pixmap = (
            QPixmap(image_path) if style == "custom" and image_path else QPixmap()
        )
        self.update()

    def set_theme(self, theme: str) -> None:
        self.theme = normalize_theme(theme)
        self.update()

    def _paint_gradient(self, painter: QPainter, colors: tuple[str, str, str]) -> None:
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        for position, color in zip((0.0, 0.55, 1.0), colors):
            gradient.setColorAt(position, QColor(color))
        painter.fillRect(self.rect(), gradient)

    def resizeEvent(self, event) -> None:
        self._resize_shortcut_grid()
        self.shortcuts.setGeometry(
            18, 18, max(0, self.width() - 36), max(0, self.height() - 90)
        )
        super().resizeEvent(event)

    def _open_shortcut(self, item: QListWidgetItem) -> None:
        value = item.data(Qt.ItemDataRole.UserRole)
        if value.startswith("app:"):
            self.application_requested.emit(value.removeprefix("app:"))
            return
        path = Path(value.removeprefix("file:"))
        if path.suffix.casefold() == ".desktop":
            try:
                target, app_id = read_desktop_shortcut(path)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Verknüpfung kann nicht geöffnet werden", str(error))
                return
            if app_id is not None:
                self.application_requested.emit(app_id)
            elif target is not None:
                self.file_requested.emit(str(target))
            return
        self.file_requested.emit(str(path))

    def create_file_shortcuts(self, paths: list[Path]) -> None:
        errors = []
        for path in paths:
            if not path.exists() and not path.is_symlink():
                errors.append(f"{path}: Datei nicht gefunden.")
                continue
            try:
                create_desktop_shortcut(self.desktop_path, path.stem, target=path)
            except (OSError, ValueError) as error:
                errors.append(f"{path.name}: {error}")
        self.refresh_shortcuts()
        if errors:
            QMessageBox.warning(
                self,
                "Desktop-Verknüpfung teilweise erstellt",
                "\n".join(errors[:8]),
            )

    def _drop_into_folder(
        self, paths: list[Path], target_directory: Path, copy: bool
    ) -> None:
        run_transfer(self, paths, Path(target_directory), move=not copy)
        self.refresh_shortcuts()

    def create_application_shortcut(self, app_id: str) -> None:
        if app_id not in self.APPLICATION_TITLES:
            QMessageBox.warning(self, "Unbekannte Anwendung", app_id)
            return
        try:
            create_desktop_shortcut(
                self.desktop_path,
                self.application_title(app_id),
                app_id=app_id,
            )
        except (OSError, ValueError) as error:
            QMessageBox.critical(
                self, "Verknüpfung kann nicht erstellt werden", str(error)
            )
            return
        self.refresh_shortcuts()

    def _show_shortcut_menu(self, position) -> None:
        item = self.shortcuts.itemAt(position)
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if item is not None:
            if not item.isSelected():
                self.shortcuts.setCurrentItem(item)
            value = item.data(Qt.ItemDataRole.UserRole)
            menu.addAction("Öffnen", lambda: self._open_shortcut(item))
            if value.startswith("app:"):
                menu.addAction("Verknüpfung entfernen", self._remove_app_shortcut)
            else:
                path = Path(value.removeprefix("file:"))
                if path.suffix.casefold() == ".desktop":
                    menu.addAction("Verknüpfung entfernen", lambda: self._trash_path(path))
                else:
                    menu.addAction("In Papierkorb verschieben", lambda: self._trash_path(path))
                    menu.addAction("Eigenschaften", lambda: self._show_file_properties(path))
        else:
            create_menu = menu.addMenu("Verknüpfung erstellen")
            for app_id, title in sorted(
                self.APPLICATION_TITLES.items(), key=lambda pair: pair[1].casefold()
            ):
                if app_id == "welcome":
                    continue
                create_menu.addAction(
                    title, lambda selected_app=app_id: self.create_application_shortcut(selected_app)
                )
        if not menu.isEmpty():
            menu.exec(self.shortcuts.mapToGlobal(position))

    def _remove_app_shortcut(self) -> None:
        item = self.shortcuts.currentItem()
        if item is None:
            return
        app_id = item.data(Qt.ItemDataRole.UserRole).removeprefix("app:")
        hidden = set(self.preferences.value("desktop/hidden_app_shortcuts", [], type=list))
        hidden.add(app_id)
        self.preferences.setValue("desktop/hidden_app_shortcuts", sorted(hidden))
        self.refresh_shortcuts()

    def _trash_path(self, path: Path) -> None:
        try:
            self.trash_store.move_to_trash(path)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Element kann nicht entfernt werden", str(error))
            return
        self.refresh_shortcuts()

    def _show_file_properties(self, path: Path) -> None:
        try:
            info = path.stat()
            size = self.trash_store._path_size(path)
        except OSError as error:
            QMessageBox.critical(self, "Eigenschaften nicht verfügbar", str(error))
            return
        file_type = (
            "Ordner"
            if path.is_dir()
            else mimetypes.guess_type(path.name)[0] or "Unbekannter Dateityp"
        )
        birth_time = QFileInfo(str(path)).birthTime()
        if birth_time.isValid():
            created = birth_time.toLocalTime().toString("dd.MM.yyyy HH:mm")
        else:
            created = (
                datetime.fromtimestamp(info.st_birthtime).astimezone().strftime("%d.%m.%Y %H:%M")
                if hasattr(info, "st_birthtime")
                else "Nicht vom Dateisystem bereitgestellt"
            )
        QMessageBox.information(
            self,
            f"Eigenschaften – {path.name}",
            f"Größe: {size} Byte\nDateityp: {file_type}\n"
            f"Speicherort: {path.parent}\nErstellungsdatum: {created}\n"
            f"Änderungsdatum: {datetime.fromtimestamp(info.st_mtime).astimezone().strftime('%d.%m.%Y %H:%M')}",
        )

    def _activate_current(self) -> None:
        item = self.shortcuts.currentItem()
        if item is not None:
            self._open_shortcut(item)

    def _handle_shortcut_key(self, command: str) -> None:
        if command == "delete":
            self._delete_current()
        elif command == "open":
            self._activate_current()

    def _delete_current(self) -> None:
        item = self.shortcuts.currentItem()
        if item is None:
            return
        value = item.data(Qt.ItemDataRole.UserRole)
        if value.startswith("app:"):
            self._remove_app_shortcut()
        else:
            self._trash_path(Path(value.removeprefix("file:")))

    @classmethod
    def application_title(cls, app_id: str) -> str:
        return cls.APPLICATION_TITLES.get(app_id, app_id.title())

    @staticmethod
    def create_application_page(app_id: str) -> QWidget:
        descriptions = {
            "welcome": (
                "Dein Desktop ist gestartet. Programme, Dateien, "
                "Einstellungen und das Systemmenü findest du im Startmenü."
            ),
            "files": "Öffne den Dateimanager über das Startmenü.",
            "editor": "Öffne den Texteditor über das Startmenü.",
            "pictures": "Öffne die Bilderanzeige über das Startmenü.",
            "clock": "Uhrzeit, Countdown und Stoppuhr.",
            "terminal": "Öffne das Terminal über das Startmenü.",
            "code": "Bearbeite lokale HTML/CSS/JS/TS-Dateien mit Vorschau und Ausführung.",
            "browser": "Öffne den Browser über das Startmenü.",
            "downloads": "Öffne deinen lokalen Downloads-Ordner.",
            "settings": "Hintergrund, Lautstärke, Bildschirm und Systeminfos.",
            "about": "Version, Modell, Prozessor, Arbeitsspeicher und Speicherplatz.",
            "music": "MP3- und OGG-Dateien mit Playlist abspielen.",
            "screenshot": "Den Bildschirm aufnehmen und in Bilder speichern.",
            "trash": "Gelöschte Elemente wiederherstellen oder endgültig entfernen.",
            "pi-tools": "WLAN, Bluetooth, GPIO und lokale Pi-Systemdaten verwalten.",
        }
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        heading = QLabel(DesktopShell.application_title(app_id))
        heading.setObjectName("heading")
        description = QLabel(descriptions.get(app_id, "Die Anwendung folgt später."))
        description.setWordWrap(True)
        status = QLabel("NeonVeil · Phase 6")

        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addStretch()
        layout.addWidget(status)
        return content

    @staticmethod
    def window_stylesheet(theme: str = "light") -> str:
        return global_stylesheet(theme)
