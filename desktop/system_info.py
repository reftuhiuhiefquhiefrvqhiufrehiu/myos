import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QLabel, QMainWindow, QWidget

from version import APP_VERSION



@dataclass(frozen=True)
class SystemSnapshot:
    version: str
    model: str
    cpu: str
    cpu_usage: str
    memory: str
    storage: str


class SystemInfoProvider:
    def __init__(self) -> None:
        self._cpu_sample: tuple[int, int] | None = None

    @staticmethod
    def _read_text(path: str) -> str | None:
        try:
            return Path(path).read_text(errors="replace").replace("\x00", "").strip()
        except OSError:
            return None

    def _cpu_usage(self) -> str:
        try:
            fields = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        except (OSError, ValueError, IndexError):
            return "Nicht verfügbar"
        if len(fields) < 4:
            return "Nicht verfügbar"
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        total = sum(fields)
        previous = self._cpu_sample
        self._cpu_sample = total, idle
        if previous is None or total <= previous[0]:
            return "Wird gemessen…"
        delta_total = total - previous[0]
        delta_idle = idle - previous[1]
        percent = max(0, min(100, round(100 * (delta_total - delta_idle) / delta_total)))
        return f"{percent}%"

    @staticmethod
    def _cpu_model() -> str:
        cpu_info = SystemInfoProvider._read_text("/proc/cpuinfo")
        if cpu_info:
            for line in cpu_info.splitlines():
                label, separator, value = line.partition(":")
                if separator and label.lower().strip() in {"model name", "hardware", "processor"}:
                    value = value.strip()
                    if value:
                        return value
        name = platform.processor()
        return name or "Nicht verfügbar"

    @staticmethod
    def _memory() -> str:
        try:
            values = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, value = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(value.strip().split()[0]) * 1024
            total = values["MemTotal"]
            available = values["MemAvailable"]
        except (OSError, KeyError, ValueError, IndexError):
            return "Nicht verfügbar"
        used = max(0, total - available)
        return f"{used / 1024**3:.1f} GB verwendet von {total / 1024**3:.1f} GB"

    @staticmethod
    def _storage() -> str:
        try:
            usage = shutil.disk_usage("/")
        except OSError:
            return "Nicht verfügbar"
        return (
            f"{usage.used / 1024**3:.1f} GB verwendet von "
            f"{usage.total / 1024**3:.1f} GB"
        )

    def snapshot(self) -> SystemSnapshot:
        model = self._read_text("/proc/device-tree/model")
        if not model:
            model = "Nicht verfügbar"
        return SystemSnapshot(
            APP_VERSION,
            model,
            self._cpu_model(),
            self._cpu_usage(),
            self._memory(),
            self._storage(),
        )


class AboutMyOSWindow(QMainWindow):
    def __init__(self, provider: SystemInfoProvider | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Über MyOS")
        self.setMinimumSize(340, 240)
        self.resize(520, 320)
        self.provider = provider or SystemInfoProvider()
        content = QWidget()
        layout = QGridLayout(content)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setHorizontalSpacing(22)
        layout.setVerticalSpacing(12)
        heading = QLabel("Über MyOS")
        heading.setObjectName("heading")
        layout.addWidget(heading, 0, 0, 1, 2)
        self.values: dict[str, QLabel] = {}
        for row, (key, label) in enumerate(
            (
                ("version", "MyOS-Version"),
                ("model", "Raspberry-Pi-Modell"),
                ("cpu", "Prozessor"),
                ("cpu_usage", "Prozessorauslastung"),
                ("memory", "Arbeitsspeicher"),
                ("storage", "Speicherplatz (/ )"),
            ),
            start=1,
        ):
            name = QLabel(label)
            name.setStyleSheet("font-weight: 600; color: #14596a;")
            value = QLabel("Wird gelesen…")
            value.setWordWrap(True)
            self.values[key] = value
            layout.addWidget(name, row, 0)
            layout.addWidget(value, row, 1)
        self.setCentralWidget(content)
        self.refresh()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(2000)

    def refresh(self) -> None:
        snapshot = self.provider.snapshot()
        for key in self.values:
            self.values[key].setText(getattr(snapshot, key))
