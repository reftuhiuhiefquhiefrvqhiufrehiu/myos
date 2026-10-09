from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


PALETTE = [
    ("Mint", "#35c9bd"),
    ("Pink", "#fa68b8"),
    ("Violett", "#a98bff"),
    ("Blau", "#5caeff"),
    ("Sonne", "#ffd166"),
    ("Weiß", "#f4fbfc"),
]


class SketchCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(520, 330)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._image = QImage(900, 620, QImage.Format.Format_ARGB32_Premultiplied)
        self._image.fill(QColor("#0c171a"))
        self._last_point: QPoint | None = None
        self.color = QColor("#35c9bd")
        self.width = 7

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#0c171a"))
        painter.drawImage(self.rect(), self._image)
        painter.end()

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_point = event.position().toPoint()
            self._draw_point(self._last_point)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.buttons() & Qt.MouseButton.LeftButton and self._last_point is not None:
            current = event.position().toPoint()
            self._draw_line(self._last_point, current)
            self._last_point = current

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_point = None

    def _canvas_point(self, point: QPoint) -> QPoint:
        x = int(point.x() * self._image.width() / max(1, self.width()))
        y = int(point.y() * self._image.height() / max(1, self.height()))
        return QPoint(x, y)

    def _pen(self) -> QPen:
        pen = QPen(self.color, self.width * self._image.width() / max(1, self.width()))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def _draw_point(self, point: QPoint) -> None:
        painter = QPainter(self._image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen())
        mapped = self._canvas_point(point)
        painter.drawPoint(mapped)
        painter.end()
        self.update()

    def _draw_line(self, start: QPoint, end: QPoint) -> None:
        painter = QPainter(self._image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen())
        painter.drawLine(self._canvas_point(start), self._canvas_point(end))
        painter.end()
        self.update()

    def clear(self) -> None:
        self._image.fill(QColor("#0c171a"))
        self.update()

    def save_png(self, path: Path) -> bool:
        return self._image.save(str(path), "PNG")


class NeonSketchWindow(QMainWindow):
    """A minimal drawing studio designed for quick visual ideas."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Neon Sketch")
        self.setMinimumSize(620, 560)
        self.resize(760, 660)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Neon Sketch")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #eaf4f5;")
        subtitle = QLabel("Idee rein. Farbe drauf. Als PNG behalten.")
        subtitle.setStyleSheet("color: #91a9ae;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.status = QLabel("Bereit")
        self.status.setStyleSheet("color: #35c9bd;")
        header.addWidget(self.status)
        layout.addLayout(header)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(QLabel("Farbe:"))
        self._color_buttons: list[QPushButton] = []
        for name, color in PALETTE:
            button = QPushButton(name)
            button.setToolTip(f"{name} wählen")
            button.setStyleSheet(
                f"QPushButton {{ background: {color}; color: #071113; border: 0; border-radius: 7px; padding: 7px 10px; font-weight: bold; }}"
            )
            button.clicked.connect(lambda checked=False, c=color, n=name: self._select_color(c, n))
            tools.addWidget(button)
            self._color_buttons.append(button)
        tools.addSpacing(12)
        tools.addWidget(QLabel("Pinsel:"))
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(2, 24)
        self.width_slider.setValue(7)
        self.width_slider.setFixedWidth(120)
        self.width_slider.valueChanged.connect(self._change_width)
        tools.addWidget(self.width_slider)
        layout.addLayout(tools)

        self.canvas = SketchCanvas(self)
        self.canvas.setStyleSheet("border: 1px solid #36545b; border-radius: 12px; background: #0c171a;")
        layout.addWidget(self.canvas, 1)

        actions = QHBoxLayout()
        clear = QPushButton("Leinwand leeren")
        clear.setStyleSheet(self._secondary_style())
        clear.clicked.connect(self._clear)
        save = QPushButton("Als PNG speichern")
        save.setStyleSheet(self._primary_style())
        save.clicked.connect(self._save)
        actions.addWidget(clear)
        actions.addStretch(1)
        actions.addWidget(save)
        layout.addLayout(actions)

    def _select_color(self, color: str, name: str) -> None:
        self.canvas.color = QColor(color)
        self.status.setText(f"{name} ausgewählt")

    def _change_width(self, value: int) -> None:
        self.canvas.width = value
        self.status.setText(f"Pinselgröße: {value}")

    def _clear(self) -> None:
        self.canvas.clear()
        self.status.setText("Leinwand geleert")

    def _save(self) -> None:
        folder = Path.home() / "Pictures" / "NeonVeil"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            path = folder / f"neon-sketch-{stamp}.png"
            if self.canvas.save_png(path):
                self.status.setText(f"Gespeichert: {path.name}")
            else:
                self.status.setText("PNG konnte nicht gespeichert werden")
        except OSError:
            self.status.setText("Bilder-Ordner ist nicht beschreibbar")

    @staticmethod
    def _primary_style() -> str:
        return "QPushButton { background: #176c67; color: white; padding: 9px 14px; border-radius: 8px; font-weight: bold; } QPushButton:hover { background: #23877f; }"

    @staticmethod
    def _secondary_style() -> str:
        return "QPushButton { background: #203a40; color: #eaf4f5; padding: 9px 14px; border: 1px solid #36545b; border-radius: 8px; } QPushButton:hover { background: #294b52; }"
