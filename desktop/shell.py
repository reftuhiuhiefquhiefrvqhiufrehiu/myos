from PySide6.QtCore import QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


class DesktopShell(QWidget):
    application_requested = Signal(str)

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
        "welcome": "Willkommen bei neonveil",
        "files": "Dateien",
        "editor": "Texteditor",
        "pictures": "Bilder",
        "clock": "Uhr",
        "terminal": "Terminal",
        "code": "Code Studio",
        "browser": "Browser",
        "downloads": "Downloads",
        "settings": "Einstellungen",
        "about": "Über MyOS",
        "notifications": "Benachrichtigungen",
        "music": "Musik",
        "screenshot": "Screenshot",
        "trash": "Papierkorb",
        "pi-tools": "Raspberry-Pi-Werkzeuge",
        "update-manager": "MyOS Update Manager",
    }

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnBottomHint,
        )
        self.setWindowTitle("neonveil")
        preferences = QSettings("neonveil", "neonveil")
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

        self.shortcuts = QListWidget(self)
        self.shortcuts.setViewMode(QListWidget.ViewMode.IconMode)
        self.shortcuts.setFlow(QListWidget.Flow.TopToBottom)
        self.shortcuts.setMovement(QListWidget.Movement.Static)
        self.shortcuts.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.shortcuts.setIconSize(QSize(40, 40))
        self.shortcuts.setGridSize(QSize(110, 78))
        self.shortcuts.setFixedWidth(132)
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
        for app_id, label in self.APPLICATIONS:
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
            standard_icon = standard_icons[app_id]
            item = QListWidgetItem(self.style().standardIcon(standard_icon), label)
            item.setData(Qt.ItemDataRole.UserRole, app_id)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setToolTip(f"{label} öffnen")
            self.shortcuts.addItem(item)
        self.shortcuts.itemDoubleClicked.connect(self._open_shortcut)

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

        painter.setPen(QPen(QColor(142, 226, 218, 42), 1))
        step = 52
        for x in range(0, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.drawLine(0, y, self.width(), y)

        painter.setPen(QColor("#f2f7f7"))
        painter.drawText(
            36,
            self.height() - 38,
            "neonveil",
        )
        painter.setPen(QColor(230, 246, 245, 185))
        painter.drawText(
            36,
            self.height() - 18,
            "Ein eigener Desktop für Raspberry Pi",
        )

    def set_wallpaper(self, style: str, image_path: str = "") -> None:
        self.wallpaper_style = style
        self.wallpaper_image_path = image_path
        self.wallpaper_pixmap = (
            QPixmap(image_path) if style == "custom" and image_path else QPixmap()
        )
        self.update()

    def set_theme(self, theme: str) -> None:
        self.theme = "dark" if theme == "dark" else "light"
        self.update()

    def _paint_gradient(self, painter: QPainter, colors: tuple[str, str, str]) -> None:
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        for position, color in zip((0.0, 0.55, 1.0), colors):
            gradient.setColorAt(position, QColor(color))
        painter.fillRect(self.rect(), gradient)

    def resizeEvent(self, event) -> None:
        self.shortcuts.setGeometry(18, 18, 132, max(0, self.height() - 90))
        super().resizeEvent(event)

    def _open_shortcut(self, item: QListWidgetItem) -> None:
        self.application_requested.emit(item.data(Qt.ItemDataRole.UserRole))

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
        status = QLabel("neonveil · Phase 6")

        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addStretch()
        layout.addWidget(status)
        return content

    @staticmethod
    def window_stylesheet(theme: str = "light") -> str:
        stylesheet = """
            QMainWindow, QWidget { background: #f0f4f5; color: #20333b; }
            QLabel#heading { color: #14596a; font-size: 22px; font-weight: 700; }
            QLabel#subtitle { color: #536c74; }
            QPushButton, QToolButton, QComboBox {
                color: #20333b;
                background: #e8eff1;
                border: 1px solid #91a8af;
                border-radius: 3px;
                padding: 6px 10px;
            }
            QPushButton:hover, QToolButton:hover, QComboBox:hover {
                background: #f7fbfc;
                border-color: #4d8795;
            }
            QPushButton:focus, QToolButton:focus, QComboBox:focus {
                border: 2px solid #176c67;
                background: #f5fbfa;
            }
            QPushButton:disabled, QToolButton:disabled { color: #73878d; }
            QLineEdit, QTextEdit, QListWidget, QSpinBox, QTimeEdit {
                background: #ffffff;
                border: 1px solid #a9bcc1;
                border-radius: 3px;
                selection-background-color: #247b73;
            }
            QLineEdit:focus, QTextEdit:focus, QListWidget:focus {
                border-color: #247b73;
            }
            QMenu {
                background: #f8fafb;
                border: 1px solid #668995;
                padding: 4px;
            }
            QMenu::item { padding: 7px 28px 7px 12px; }
            QMenu::item:selected { background: #c8e1e7; color: #173d48; }
        """
        if theme == "dark":
            replacements = {
                "#f0f4f5": "#252f34",
                "#f8fafb": "#2b383e",
                "#ffffff": "#202a2f",
                "#e8eff1": "#35464d",
                "#f7fbfc": "#3b4e55",
                "#f5fbfa": "#30464a",
                "#20333b": "#e4ecee",
                "#536c74": "#b3c3c7",
                "#73878d": "#87999e",
                "#14596a": "#69bdb1",
                "#91a8af": "#5d747b",
                "#a9bcc1": "#536a71",
                "#c8e1e7": "#354f55",
                "#173d48": "#dce9eb",
            }
            for light, dark in replacements.items():
                stylesheet = stylesheet.replace(light, dark)
        return stylesheet
