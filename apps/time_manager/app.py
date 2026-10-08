from PySide6.QtCore import QElapsedTimer, QTime, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class TimeManagerWindow(QMainWindow):
    timer_finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Uhr")
        self.setMinimumSize(420, 310)
        self.resize(520, 390)
        self._timer_elapsed = QElapsedTimer()
        self._timer_duration_ms = 0
        self._timer_finished = False
        self._stopwatch_elapsed = QElapsedTimer()
        self._stopwatch_accumulated_ms = 0
        self._stopwatch_running = False

        content = QWidget()
        layout = QVBoxLayout(content)

        clock_group = QGroupBox("Aktuelle Uhrzeit")
        clock_layout = QVBoxLayout(clock_group)
        self.clock_label = QLabel()
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock_label.setStyleSheet("font-size: 38px; font-weight: 600;")
        clock_layout.addWidget(self.clock_label)

        timer_group = QGroupBox("Timer")
        timer_layout = QGridLayout(timer_group)
        self.timer_minutes = QSpinBox()
        self.timer_minutes.setRange(0, 1439)
        self.timer_minutes.setSuffix(" Min")
        self.timer_seconds = QSpinBox()
        self.timer_seconds.setRange(0, 59)
        self.timer_seconds.setSuffix(" Sek")
        self.timer_minutes.setValue(1)
        self.timer_display = QLabel("01:00")
        self.timer_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer_display.setStyleSheet("font-size: 24px; font-weight: 600;")
        self.timer_start_button = QPushButton("Start")
        self.timer_start_button.clicked.connect(self.start_timer)
        self.timer_reset_button = QPushButton("Zurücksetzen")
        self.timer_reset_button.clicked.connect(self.reset_timer)
        timer_layout.addWidget(self.timer_minutes, 0, 0)
        timer_layout.addWidget(self.timer_seconds, 0, 1)
        timer_layout.addWidget(self.timer_display, 1, 0, 1, 2)
        timer_layout.addWidget(self.timer_start_button, 2, 0)
        timer_layout.addWidget(self.timer_reset_button, 2, 1)

        stopwatch_group = QGroupBox("Stoppuhr")
        stopwatch_layout = QHBoxLayout(stopwatch_group)
        self.stopwatch_display = QLabel("00:00:00.00")
        self.stopwatch_display.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.stopwatch_start_button = QPushButton("Start")
        self.stopwatch_start_button.clicked.connect(self.toggle_stopwatch)
        self.stopwatch_reset_button = QPushButton("Zurücksetzen")
        self.stopwatch_reset_button.clicked.connect(self.reset_stopwatch)
        stopwatch_layout.addWidget(self.stopwatch_display, 1)
        stopwatch_layout.addWidget(self.stopwatch_start_button)
        stopwatch_layout.addWidget(self.stopwatch_reset_button)

        layout.addWidget(clock_group)
        layout.addWidget(timer_group)
        layout.addWidget(stopwatch_group)
        self.setCentralWidget(content)

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self._update_displays)
        self.tick_timer.start(50)
        self._update_displays()

    @staticmethod
    def format_clock_time(time: QTime) -> str:
        return time.toString("HH:mm:ss")

    @staticmethod
    def format_stopwatch(milliseconds: int) -> str:
        centiseconds = max(0, milliseconds) // 10
        hours, remainder = divmod(centiseconds, 360_000)
        minutes, remainder = divmod(remainder, 6_000)
        seconds, centiseconds = divmod(remainder, 100)
        return f"{hours:02}:{minutes:02}:{seconds:02}.{centiseconds:02}"

    def start_timer(self) -> None:
        if self._timer_elapsed.isValid():
            self._timer_duration_ms = max(
                0, self._timer_duration_ms - self._timer_elapsed.elapsed()
            )
            self._timer_elapsed.invalidate()
            self.timer_start_button.setText("Weiter")
            self._update_timer()
            return
        self._timer_finished = False
        if not self._timer_duration_ms:
            self._timer_duration_ms = (
                self.timer_minutes.value() * 60 + self.timer_seconds.value()
            ) * 1000
        if self._timer_duration_ms <= 0:
            self.timer_display.setText("Zeit abgelaufen")
            return
        self._timer_elapsed.start()
        self.timer_start_button.setText("Pause")
        self._update_timer()

    def reset_timer(self) -> None:
        self._timer_elapsed.invalidate()
        self._timer_duration_ms = 0
        self._timer_finished = False
        self.timer_start_button.setText("Start")
        self._update_timer()

    def toggle_stopwatch(self) -> None:
        if self._stopwatch_running:
            self._stopwatch_accumulated_ms += self._stopwatch_elapsed.elapsed()
            self._stopwatch_running = False
            self.stopwatch_start_button.setText("Weiter")
        else:
            self._stopwatch_elapsed.start()
            self._stopwatch_running = True
            self.stopwatch_start_button.setText("Pause")
        self._update_stopwatch()

    def reset_stopwatch(self) -> None:
        self._stopwatch_running = False
        self._stopwatch_accumulated_ms = 0
        self._stopwatch_elapsed.invalidate()
        self.stopwatch_start_button.setText("Start")
        self._update_stopwatch()

    def _update_displays(self) -> None:
        self.clock_label.setText(self.format_clock_time(QTime.currentTime()))
        self._update_timer()
        self._update_stopwatch()

    def _update_timer(self) -> None:
        if self._timer_finished:
            self.timer_display.setText("00:00")
            return
        if not self._timer_elapsed.isValid():
            remaining_ms = self._timer_duration_ms or (
                self.timer_minutes.value() * 60 + self.timer_seconds.value()
            ) * 1000
        else:
            remaining_ms = max(
                0, self._timer_duration_ms - self._timer_elapsed.elapsed()
            )
            if remaining_ms == 0:
                self._timer_elapsed.invalidate()
                self._timer_duration_ms = 0
                self._timer_finished = True
                self.timer_start_button.setText("Start")
                self.timer_finished.emit()

        if remaining_ms == 0 and self._timer_duration_ms == 0:
            self.timer_display.setText("00:00")
        else:
            total_seconds = (remaining_ms + 999) // 1000
            minutes, seconds = divmod(total_seconds, 60)
            self.timer_display.setText(f"{minutes:02}:{seconds:02}")

    def _update_stopwatch(self) -> None:
        elapsed = self._stopwatch_accumulated_ms
        if self._stopwatch_running:
            elapsed += self._stopwatch_elapsed.elapsed()
        self.stopwatch_display.setText(self.format_stopwatch(elapsed))
