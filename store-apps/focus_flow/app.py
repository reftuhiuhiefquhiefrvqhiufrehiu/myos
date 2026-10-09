from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)


MODES = {
    "Fokus · 25 Minuten": (25 * 60, "Fokus"),
    "Kurze Pause · 5 Minuten": (5 * 60, "Pause"),
    "Lange Pause · 15 Minuten": (15 * 60, "Pause"),
}


class FocusFlowWindow(QMainWindow):
    """A calm, offline Pomodoro timer with a daily session counter."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Focus Flow")
        self.setMinimumSize(430, 420)
        self.resize(480, 470)
        self._state_path = Path.home() / ".local" / "share" / "NeonVeil" / "focus-flow.json"
        self._completed = self._load_completed()
        self._running = False
        self._remaining, _ = self._mode_details()
        self._total = self._remaining

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(14)

        eyebrow = QLabel("DEIN FOKUSRAUM")
        eyebrow.setStyleSheet("color: #35c9bd; font-size: 11px; font-weight: 700; letter-spacing: 2px;")
        layout.addWidget(eyebrow)

        title = QLabel("Eine Sache nach der anderen.")
        title.setStyleSheet("font-size: 24px; font-weight: 700; color: #eaf4f5;")
        layout.addWidget(title)

        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("Woran arbeitest du gerade?")
        self.task_input.setClearButtonEnabled(True)
        self.task_input.setStyleSheet(
            "QLineEdit { padding: 11px 12px; border: 1px solid #36545b; border-radius: 9px; "
            "background: #14262b; color: #eaf4f5; font-size: 14px; }"
        )
        layout.addWidget(self.task_input)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(MODES)
        self.mode_combo.currentTextChanged.connect(self._change_mode)
        self.mode_combo.setStyleSheet("QComboBox { padding: 9px; border: 1px solid #36545b; border-radius: 8px; }")
        layout.addWidget(self.mode_combo)

        self.time_label = QLabel()
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        timer_font = QFont("Sans Serif", 44)
        timer_font.setBold(True)
        self.time_label.setFont(timer_font)
        self.time_label.setStyleSheet("color: #35c9bd; padding: 6px;")
        layout.addWidget(self.time_label)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12)
        self.progress.setStyleSheet(
            "QProgressBar { border: 0; border-radius: 6px; background: #1d3338; } "
            "QProgressBar::chunk { border-radius: 6px; background: #35c9bd; }"
        )
        layout.addWidget(self.progress)

        self.status = QLabel()
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet("color: #9db4b9; font-size: 13px;")
        layout.addWidget(self.status)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.start_button = QPushButton("Starten")
        self.start_button.setStyleSheet(self._primary_button_style())
        self.start_button.clicked.connect(self._toggle_timer)
        self.reset_button = QPushButton("Zurücksetzen")
        self.reset_button.setStyleSheet(self._secondary_button_style())
        self.reset_button.clicked.connect(self._reset_timer)
        controls.addWidget(self.start_button, 2)
        controls.addWidget(self.reset_button, 1)
        layout.addLayout(controls)

        self.sessions = QLabel()
        self.sessions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sessions.setStyleSheet("color: #eaf4f5; background: #14262b; border-radius: 9px; padding: 10px;")
        layout.addWidget(self.sessions)
        layout.addStretch(1)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self._render()

    def _mode_details(self) -> tuple[int, str]:
        return MODES[self.mode_combo.currentText()] if hasattr(self, "mode_combo") else next(iter(MODES.values()))

    def _change_mode(self) -> None:
        if not self._running:
            self._reset_timer()

    def _toggle_timer(self) -> None:
        self._running = not self._running
        if self._running:
            self.timer.start()
        else:
            self.timer.stop()
        self._render()

    def _reset_timer(self) -> None:
        self.timer.stop()
        self._running = False
        self._total, _ = self._mode_details()
        self._remaining = self._total
        self._render()

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self.timer.stop()
            self._running = False
            _, kind = self._mode_details()
            if kind == "Fokus":
                self._completed += 1
                self._save_completed()
            self.status.setText("Zeit ist um — gut gemacht.")
            self._remaining = 0
        self._render()

    def _render(self) -> None:
        minutes, seconds = divmod(max(0, self._remaining), 60)
        self.time_label.setText(f"{minutes:02d}:{seconds:02d}")
        self.progress.setRange(0, max(1, self._total))
        self.progress.setValue(max(0, self._total - self._remaining))
        _, kind = self._mode_details()
        if self._running:
            self.status.setText(f"{kind}-Session läuft" + (f" · {self.task_input.text()}" if self.task_input.text() else ""))
        elif self._remaining:
            self.status.setText("Bereit, wenn du es bist.")
        self.start_button.setText("Pausieren" if self._running else "Starten")
        self.sessions.setText(f"Heute abgeschlossene Fokus-Sessions:  {self._completed}")

    def _load_completed(self) -> int:
        try:
            return max(0, int(json.loads(self._state_path.read_text(encoding="utf-8")).get("completed", 0)))
        except (OSError, ValueError, json.JSONDecodeError):
            return 0

    def _save_completed(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"completed": self._completed}), encoding="utf-8")
            temporary.replace(self._state_path)
        except OSError:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.timer.stop()
        super().closeEvent(event)

    @staticmethod
    def _primary_button_style() -> str:
        return "QPushButton { background: #176c67; color: white; padding: 11px; border-radius: 8px; font-weight: bold; } QPushButton:hover { background: #23877f; }"

    @staticmethod
    def _secondary_button_style() -> str:
        return "QPushButton { background: #203a40; color: #eaf4f5; padding: 11px; border: 1px solid #36545b; border-radius: 8px; } QPushButton:hover { background: #294b52; }"
