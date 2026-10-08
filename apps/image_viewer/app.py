from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


class ImageViewerWindow(QMainWindow):
    def __init__(self, file_path: Path | None = None) -> None:
        super().__init__()
        self.setMinimumSize(420, 300)
        self.resize(760, 540)
        self.image_paths: list[Path] = []
        self.current_index = -1
        self._fullscreen = False

        content = QWidget()
        layout = QVBoxLayout(content)
        self.image_label = QLabel("Öffne ein PNG- oder JPG-Bild.")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(200, 160)
        self.image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.image_label.setStyleSheet("background: #26343a; color: #f2f7f7;")

        controls = QHBoxLayout()
        self.open_button = QPushButton("Öffnen…")
        self.open_button.clicked.connect(self.open_dialog)
        self.previous_button = QPushButton("Vorheriges")
        self.previous_button.clicked.connect(self.show_previous)
        self.next_button = QPushButton("Nächstes")
        self.next_button.clicked.connect(self.show_next)
        self.fullscreen_button = QPushButton("Vollbild")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        controls.addWidget(self.open_button)
        controls.addWidget(self.previous_button)
        controls.addWidget(self.next_button)
        controls.addStretch(1)
        controls.addWidget(self.fullscreen_button)

        layout.addWidget(self.image_label, 1)
        layout.addLayout(controls)
        self.setCentralWidget(content)

        if file_path is not None:
            self.open_image(file_path)

    def open_dialog(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Bild öffnen",
            str(Path.home()),
            "Bilder (*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG)",
        )
        if path:
            self.open_image(Path(path))

    def open_image(self, file_path: Path) -> None:
        path = file_path.expanduser().absolute()
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            QMessageBox.warning(
                self,
                "Nicht unterstütztes Bildformat",
                "Es können PNG- und JPG-Bilder angezeigt werden.",
            )
            return
        if not path.is_file():
            QMessageBox.warning(
                self, "Bild nicht gefunden", f"„{path}“ wurde nicht gefunden."
            )
            return

        try:
            siblings = sorted(
                (
                    item
                    for item in path.parent.iterdir()
                    if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
                ),
                key=lambda item: item.name.casefold(),
            )
        except OSError as error:
            QMessageBox.critical(self, "Ordner kann nicht gelesen werden", str(error))
            return

        self.image_paths = siblings or [path]
        self.current_index = self.image_paths.index(path)
        self._show_current_image()

    def show_previous(self) -> None:
        self._step(-1)

    def show_next(self) -> None:
        self._step(1)

    def toggle_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        self.showFullScreen() if self._fullscreen else self.showNormal()
        self.fullscreen_button.setText(
            "Fenster" if self._fullscreen else "Vollbild"
        )

    def resizeEvent(self, event) -> None:
        self._render_image()
        super().resizeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._fullscreen:
            self.toggle_fullscreen()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Left:
            self.show_previous()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Right:
            self.show_next()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._fullscreen = False
        super().closeEvent(event)

    def _step(self, direction: int) -> None:
        if not self.image_paths:
            return
        self.current_index = (self.current_index + direction) % len(self.image_paths)
        self._show_current_image()

    def _show_current_image(self) -> None:
        if not self.image_paths or self.current_index < 0:
            return
        path = self.image_paths[self.current_index]
        self.setWindowTitle(f"{path.name} — Bilder")
        self._render_image()
        self.previous_button.setEnabled(len(self.image_paths) > 1)
        self.next_button.setEnabled(len(self.image_paths) > 1)

    def _render_image(self) -> None:
        if self.current_index < 0 or self.current_index >= len(self.image_paths):
            return
        path = self.image_paths[self.current_index]
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.image_label.setText(f"Bild kann nicht geladen werden:\n{path}")
            return
        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
