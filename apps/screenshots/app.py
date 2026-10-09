from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QStandardPaths, Qt, Signal
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ScreenshotController(QObject):
    screenshot_saved = Signal(str)

    def __init__(
        self,
        pictures_dir: Path | None = None,
        screen_provider=None,
    ) -> None:
        super().__init__()
        pictures_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation
        )
        self.pictures_dir = pictures_dir or Path(pictures_location or Path.home() / "Pictures")
        self.screen_provider = screen_provider or QApplication.primaryScreen

    def capture(self) -> Path:
        screen = self.screen_provider()
        if screen is None:
            raise RuntimeError("Es ist kein Bildschirm verfügbar.")
        screenshot_dir = self.pictures_dir / "Screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        index = 1
        while (screenshot_dir / f"NeonVeil-Screenshot-{index:03}.png").exists():
            index += 1
        path = screenshot_dir / f"NeonVeil-Screenshot-{index:03}.png"
        pixmap: QPixmap = screen.grabWindow(0)
        if pixmap.isNull():
            raise OSError("Der Bildschirm konnte nicht aufgenommen werden.")
        if not pixmap.save(str(path), "PNG"):
            raise OSError(f"Screenshot konnte nicht gespeichert werden: {path}")
        self.screenshot_saved.emit(str(path))
        return path


class ScreenshotHotkey(QObject):
    def __init__(self, controller: ScreenshotController) -> None:
        super().__init__()
        self.controller = controller

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Print
            and not event.isAutoRepeat()
        ):
            try:
                self.controller.capture()
            except (OSError, RuntimeError) as error:
                QMessageBox.warning(None, "Screenshot fehlgeschlagen", str(error))
            return True
        return super().eventFilter(watched, event)


class ScreenshotWindow(QMainWindow):
    screenshot_saved = Signal(str)

    def __init__(self, controller: ScreenshotController | None = None) -> None:
        super().__init__()
        self.controller = controller or ScreenshotController()
        self.controller.screenshot_saved.connect(self.screenshot_saved)
        self.setWindowTitle("Screenshot")
        self.setMinimumSize(340, 190)
        self.resize(440, 230)
        content = QWidget()
        layout = QVBoxLayout(content)
        heading = QLabel("Screenshot aufnehmen")
        heading.setObjectName("heading")
        self.status = QLabel(
            "Der ganze Bildschirm wird in Bilder/Screenshots gespeichert.\n"
            "Druck-Taste nimmt jederzeit einen Screenshot auf."
        )
        self.status.setWordWrap(True)
        self.capture_button = QPushButton("Screenshot aufnehmen")
        self.capture_button.clicked.connect(self.capture)
        layout.addWidget(heading)
        layout.addWidget(self.status, 1)
        layout.addWidget(self.capture_button)
        self.setCentralWidget(content)

    def capture(self) -> None:
        try:
            path = self.controller.capture()
        except (OSError, RuntimeError) as error:
            QMessageBox.warning(self, "Screenshot fehlgeschlagen", str(error))
            return
        self.status.setText(f"Gespeichert: {path}")
