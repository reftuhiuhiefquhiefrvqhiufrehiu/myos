import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from desktop.version import APP_VERSION
from update_manager import CHANNELS, UpdateError, UpdateManager

WALLPAPER_PRESETS = {
    "lagoon": ("Lagune", ("#12354b", "#176c78", "#263d66")),
    "morning": ("Morgen", ("#23405d", "#568a92", "#d2a985")),
    "graphite": ("Schiefer", ("#26343d", "#465d68", "#263d66")),
}
WALLPAPER_EXTENSIONS = {".jpg", ".jpeg", ".png"}
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class SettingsStore:
    """Small per-user preference store backed by Qt's standard INI settings."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings("neonveil", "neonveil")

    def wallpaper(self) -> tuple[str, str]:
        style = self.settings.value("wallpaper/style", "lagoon", type=str)
        image_path = self.settings.value("wallpaper/image", "", type=str)
        if style not in WALLPAPER_PRESETS and style != "custom":
            return "lagoon", ""
        if style == "custom" and not is_supported_image(Path(image_path)):
            return "lagoon", ""
        return style, image_path

    def set_wallpaper(self, style: str, image_path: str = "") -> None:
        self.settings.setValue("wallpaper/style", style)
        self.settings.setValue("wallpaper/image", image_path)
        self.settings.sync()

    def reset_wallpaper(self) -> None:
        self.set_wallpaper("lagoon")

    def appearance(self) -> str:
        theme = self.settings.value("appearance/theme", "light", type=str)
        return theme if theme in {"light", "dark"} else "light"

    def set_appearance(self, theme: str) -> None:
        if theme not in {"light", "dark"}:
            raise ValueError("Unbekanntes Erscheinungsbild.")
        self.settings.setValue("appearance/theme", theme)
        self.settings.sync()


def is_supported_image(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in WALLPAPER_EXTENSIONS
        and QImageReader(str(path)).canRead()
    )


class AudioController:
    """Read and set the first usable ALSA mixer control via alsa-utils."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or subprocess.run
        self._control: str | None = None

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return self._runner(
            arguments,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def _find_control(self) -> tuple[str | None, str]:
        try:
            result = self._run(["amixer", "scontrols"])
        except (OSError, subprocess.SubprocessError) as error:
            return None, f"Lautstärke-Steuerung nicht verfügbar: {error}"
        if result.returncode != 0:
            detail = result.stderr.strip() or "kein Audiogerät gefunden"
            return None, f"Lautstärke-Steuerung nicht verfügbar: {detail}"

        controls = re.findall(r"Simple mixer control '([^']+)'", result.stdout)
        if not controls:
            return None, "Kein nutzbarer Lautstärke-Regler gefunden."
        preferred = ("Master", "PCM", "Headphone", "Speaker")
        self._control = next(
            (name for name in preferred if name in controls), controls[0]
        )
        return self._control, ""

    def read_volume(self) -> tuple[int | None, str]:
        control, message = self._find_control()
        if control is None:
            return None, message
        try:
            result = self._run(["amixer", "sget", control])
        except (OSError, subprocess.SubprocessError) as error:
            return None, f"Lautstärke konnte nicht gelesen werden: {error}"
        if result.returncode != 0:
            detail = result.stderr.strip() or "Mixer-Abfrage fehlgeschlagen"
            return None, f"Lautstärke konnte nicht gelesen werden: {detail}"
        match = re.search(r"\[(\d{1,3})%\]", result.stdout)
        if match is None or int(match.group(1)) > 100:
            return None, "Der Audioregler lieferte keinen gültigen Pegel."
        return int(match.group(1)), ""

    def set_volume(self, volume: int) -> tuple[bool, str]:
        if type(volume) is not int or not 0 <= volume <= 100:
            return False, "Die Lautstärke muss zwischen 0 und 100 liegen."
        control, message = self._find_control()
        if control is None:
            return False, message
        try:
            result = self._run(["amixer", "sset", control, f"{volume}%"])
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"Lautstärke konnte nicht geändert werden: {error}"
        if result.returncode != 0:
            detail = result.stderr.strip() or "Mixer-Befehl fehlgeschlagen"
            return False, f"Lautstärke konnte nicht geändert werden: {detail}"
        return True, f"Lautstärke auf {volume}% gesetzt."


@dataclass(frozen=True)
class DisplayState:
    output: str
    modes: tuple[str, ...]
    current_mode: str


class DisplayController:
    """Query and change only modes advertised by the active X11 display."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or subprocess.run

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return self._runner(
            arguments,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def query(self) -> tuple[DisplayState | None, str]:
        try:
            result = self._run(["xrandr", "--query"])
        except (OSError, subprocess.SubprocessError) as error:
            return None, f"Bildschirmmodi sind nicht verfügbar: {error}"
        if result.returncode != 0:
            detail = result.stderr.strip() or "X11-Anzeige nicht erreichbar"
            return None, f"Bildschirmmodi sind nicht verfügbar: {detail}"

        output: str | None = None
        modes: list[str] = []
        current_mode = ""
        for line in result.stdout.splitlines():
            header = re.match(r"^(\S+) connected\b", line)
            if header is not None and output is None:
                output = header.group(1)
                continue
            if output is not None and line and not line[0].isspace():
                break
            if output is not None:
                mode_match = re.match(r"^\s+(\d{3,5}x\d{3,5})\s+(.+)$", line)
                if mode_match is not None:
                    mode = mode_match.group(1)
                    if mode not in modes:
                        modes.append(mode)
                    if "*" in mode_match.group(2):
                        current_mode = mode

        if output is None:
            return None, "Kein verbundener Bildschirm wurde gefunden."
        if not modes:
            return None, f"Für {output} wurden keine Auflösungen gemeldet."
        return DisplayState(output, tuple(modes), current_mode), ""

    def apply_mode(self, mode: str) -> tuple[bool, str]:
        state, message = self.query()
        if state is None:
            return False, message
        if mode not in state.modes:
            return False, "Diese Auflösung wird vom Bildschirm nicht angeboten."
        try:
            result = self._run(
                ["xrandr", "--output", state.output, "--mode", mode]
            )
        except (OSError, subprocess.SubprocessError) as error:
            return False, f"Auflösung konnte nicht geändert werden: {error}"
        if result.returncode != 0:
            detail = result.stderr.strip() or "xrandr konnte den Modus nicht setzen"
            return False, f"Auflösung konnte nicht geändert werden: {detail}"
        return True, f"Auflösung auf {mode} gesetzt."


class SettingsWindow(QMainWindow):
    wallpaper_changed = Signal(str, str)
    appearance_changed = Signal(str)
    update_manager_requested = Signal()

    def __init__(
        self,
        store: SettingsStore | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Einstellungen")
        self.setMinimumSize(320, 240)
        self.resize(560, 460)
        self.store = store or SettingsStore()
        self.audio = AudioController(runner)
        self.display = DisplayController(runner)
        self.updates = UpdateManager(current_version=APP_VERSION)
        self._wallpaper_buttons: dict[str, QPushButton] = {}

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("settingsScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        heading = QLabel("Einstellungen")
        heading.setObjectName("heading")
        subtitle = QLabel("Passe deinen neonveil-Desktop an.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(heading)
        layout.addWidget(subtitle)
        layout.addWidget(self._appearance_group())
        layout.addWidget(self._wallpaper_group())

        layout.addWidget(self._audio_group())
        layout.addWidget(self._display_group())
        layout.addWidget(self._updates_group())
        layout.addWidget(self._about_group())
        layout.addStretch(1)
        self.scroll_area.setWidget(content)
        self.content_widget = content
        self.setCentralWidget(self.scroll_area)

        self._light_stylesheet = """
            QWidget { color: #20333b; font-size: 13px; }
            QLabel#heading { color: #14596a; font-size: 24px; font-weight: 700; }
            QLabel#subtitle { color: #536c74; }
            QGroupBox {
                background: #f8fafb;
                border: 1px solid #b5c7cc;
                border-radius: 4px;
                margin-top: 10px;
                padding: 12px 10px 10px;
                font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QLabel#status { color: #526b73; font-weight: 400; }
            QPushButton, QComboBox {
                background: #e8eff1;
                border: 1px solid #91a8af;
                border-radius: 3px;
                padding: 6px 9px;
            }
            QPushButton:hover, QComboBox:hover { background: #f7fbfc; border-color: #4d8795; }
            QPushButton:focus, QComboBox:focus {
                border: 2px solid #176c67;
                background: #f5fbfa;
            }
            QSlider:focus {
                background: #d4ebe8;
                border: 2px solid #176c67;
                border-radius: 4px;
            }
            QPushButton:checked {
                background: #d4ebe8;
                border: 2px solid #247b73;
                color: #164d49;
            }
            QPushButton:disabled, QComboBox:disabled { color: #73878d; }
            QSlider::groove:horizontal { height: 6px; background: #c3d2d6; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #247b73; border-radius: 3px; }
            QSlider::handle:horizontal {
                background: #ffffff; border: 2px solid #247b73;
                width: 15px; margin: -6px 0; border-radius: 8px;
            }
            """
        self.set_appearance(self.store.appearance(), emit=False)

    def _appearance_group(self) -> QGroupBox:
        group = QGroupBox("Erscheinungsbild")
        layout = QHBoxLayout(group)
        layout.addWidget(QLabel("Farbschema"))
        self.appearance_combo = QComboBox()
        self.appearance_combo.addItem("Hell", "light")
        self.appearance_combo.addItem("Dunkel", "dark")
        self.appearance_combo.currentIndexChanged.connect(
            lambda _index: self.set_appearance(self.appearance_combo.currentData())
        )
        layout.addWidget(self.appearance_combo, 1)
        return group

    def set_appearance(self, theme: str, emit: bool = True) -> None:
        if theme not in {"light", "dark"}:
            theme = "light"
        self.theme = theme
        if hasattr(self, "store"):
            self.store.set_appearance(theme)
        if hasattr(self, "appearance_combo"):
            self.appearance_combo.setCurrentIndex(
                max(0, self.appearance_combo.findData(theme))
            )
        stylesheet = self._light_stylesheet
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
                "#176c67": "#65b9ae",
                "#164d49": "#d2e8e5",
                "#526b73": "#b3c3c7",
                "#91a8af": "#5d747b",
                "#b5c7cc": "#4d6269",
                "#a9bcc1": "#536a71",
                "#c3d2d6": "#43545a",
                "#d4ebe8": "#315550",
                "#c8e1e7": "#354f55",
                "#173d48": "#dce9eb",
            }
            for light, dark in replacements.items():
                stylesheet = stylesheet.replace(light, dark)
        self.setStyleSheet(stylesheet)
        if emit:
            self.appearance_changed.emit(theme)

    def _wallpaper_group(self) -> QGroupBox:
        group = QGroupBox("Hintergrund")
        layout = QVBoxLayout(group)
        choices = QGridLayout()
        self._wallpaper_button_group = QButtonGroup(group)
        self._wallpaper_button_group.setExclusive(True)
        current_style, current_path = self.store.wallpaper()
        for index, (key, (title, colors)) in enumerate(WALLPAPER_PRESETS.items()):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setMinimumHeight(54)
            self._wallpaper_button_group.addButton(button)
            button.setStyleSheet(
                f"QPushButton {{ color: white; font-weight: 600; "
                f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, "
                f"stop:0 {colors[0]}, stop:0.55 {colors[1]}, stop:1 {colors[2]}); }}"
                "QPushButton:checked { border: 3px solid #ffffff; font-weight: 700; }"
                "QPushButton:focus { border: 3px solid #12354b; }"
            )
            button.setChecked(current_style == key)
            button.clicked.connect(
                lambda _checked=False, selected=key: self.apply_wallpaper(selected)
            )
            self._wallpaper_buttons[key] = button
            choices.addWidget(button, index // 2, index % 2)
        choices.setColumnStretch(0, 1)
        choices.setColumnStretch(1, 1)

        self.image_button = QPushButton("Eigenes Bild auswählen…")
        self.image_button.setCheckable(True)
        self.image_button.setChecked(current_style == "custom")
        self._wallpaper_button_group.addButton(self.image_button)
        self.image_button.setStyleSheet(
            """
            QPushButton:checked {
                color: #164d49;
                background: #d4ebe8;
                border: 3px solid #176c67;
                font-weight: 700;
            }
            QPushButton:focus { border: 3px solid #12354b; }
            """
        )
        self.image_button.clicked.connect(self._choose_wallpaper)
        self.reset_wallpaper_button = QPushButton("Zurücksetzen")
        self.reset_wallpaper_button.clicked.connect(self.reset_wallpaper)
        self.wallpaper_status = QLabel()
        self.wallpaper_status.setObjectName("status")
        self.wallpaper_status.setWordWrap(True)
        self.wallpaper_status.setText(
            f"✓ Eigenes Bild aktiv: {Path(current_path).name}"
            if current_style == "custom" else
            "Die Auswahl wird sofort gespeichert und angewendet."
        )

        actions = QVBoxLayout()
        actions.addWidget(self.image_button)
        actions.addWidget(self.reset_wallpaper_button)
        layout.addLayout(choices)
        layout.addLayout(actions)
        layout.addWidget(self.wallpaper_status)
        return group

    def _audio_group(self) -> QGroupBox:
        group = QGroupBox("Lautstärke")
        layout = QVBoxLayout(group)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_value = QLabel("—")
        self.volume_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        value_row = QHBoxLayout()
        value_row.addWidget(QLabel("Pegel"))
        value_row.addWidget(self.volume_slider, 1)
        value_row.addWidget(self.volume_value)
        self.volume_apply_button = QPushButton("Anwenden")
        self.volume_apply_button.clicked.connect(self._apply_volume)
        self.volume_status = QLabel()
        self.volume_status.setObjectName("status")
        self.volume_status.setWordWrap(True)
        layout.addLayout(value_row)
        layout.addWidget(self.volume_apply_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.volume_status)

        volume, message = self.audio.read_volume()
        if volume is None:
            self.volume_slider.setEnabled(False)
            self.volume_apply_button.setEnabled(False)
            self.volume_status.setText(message)
        else:
            self.volume_slider.setValue(volume)
            self.volume_value.setText(f"{volume}%")
            self.volume_status.setText("Systemregler erkannt.")
        self.volume_slider.valueChanged.connect(
            lambda value: self.volume_value.setText(f"{value}%")
        )
        return group

    def _display_group(self) -> QGroupBox:
        group = QGroupBox("Bildschirm")
        layout = QVBoxLayout(group)
        self.display_combo = QComboBox()
        self.display_apply_button = QPushButton("Auflösung anwenden")
        self.display_apply_button.clicked.connect(self._apply_display_mode)
        self.display_status = QLabel()
        self.display_status.setObjectName("status")
        self.display_status.setWordWrap(True)
        layout.addWidget(QLabel("Verfügbare Auflösungen"))
        layout.addWidget(self.display_combo)
        layout.addWidget(self.display_apply_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.display_status)

        state, message = self.display.query()
        if state is None:
            self.display_combo.setEnabled(False)
            self.display_apply_button.setEnabled(False)
            self.display_status.setText(message)
        else:
            for mode in state.modes:
                label = f"{mode}  · aktuell" if mode == state.current_mode else mode
                self.display_combo.addItem(label, mode)
            self.display_status.setText(f"Anzeige: {state.output}")
        return group

    def _updates_group(self) -> QGroupBox:
        group = QGroupBox("System · Updates")
        layout = QVBoxLayout(group)
        channel_row = QHBoxLayout()
        channel_row.addWidget(QLabel("Update-Kanal"))
        self.update_channel_combo = QComboBox()
        for channel, label in (
            ("stable", "Stable"),
            ("beta", "Beta"),
            ("developer", "Developer"),
        ):
            if channel in CHANNELS:
                self.update_channel_combo.addItem(label, channel)
        self.update_channel_combo.setCurrentIndex(
            max(0, self.update_channel_combo.findData(self.updates.channel))
        )
        self.update_channel_combo.currentIndexChanged.connect(
            self._change_update_channel
        )
        channel_row.addWidget(self.update_channel_combo, 1)
        layout.addLayout(channel_row)

        self.auto_update_check = QCheckBox("Automatisch suchen")
        self.auto_update_download = QCheckBox("Automatisch herunterladen")
        self.auto_update_install = QCheckBox("Automatisch installieren")
        self.auto_update_check.setChecked(self.updates.preferences["auto_check"])
        self.auto_update_download.setChecked(
            self.updates.preferences["auto_download"]
        )
        self.auto_update_install.setChecked(
            self.updates.preferences["auto_install"]
        )
        for checkbox in (
            self.auto_update_check,
            self.auto_update_download,
            self.auto_update_install,
        ):
            checkbox.toggled.connect(self._save_update_preferences)
            layout.addWidget(checkbox)

        warning = QLabel(
            "Beta-Versionen können Fehler enthalten. Developer-Versionen sind "
            "experimentell. Automatische Installation ist standardmäßig deaktiviert."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.open_update_manager_button = QPushButton("Update Manager öffnen…")
        self.open_update_manager_button.clicked.connect(
            self.update_manager_requested.emit
        )
        layout.addWidget(
            self.open_update_manager_button, 0, Qt.AlignmentFlag.AlignRight
        )
        return group

    def _change_update_channel(self, _index: int) -> None:
        selected = self.update_channel_combo.currentData()
        if selected == self.updates.channel:
            return
        if selected != "stable":
            answer = QMessageBox.warning(
                self,
                "Update-Kanal wechseln",
                f"{selected.title()}-Versionen können Fehler enthalten.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.update_channel_combo.blockSignals(True)
                self.update_channel_combo.setCurrentIndex(
                    max(
                        0,
                        self.update_channel_combo.findData(self.updates.channel),
                    )
                )
                self.update_channel_combo.blockSignals(False)
                return
        try:
            self.updates.save_preferences(channel=selected)
        except (OSError, UpdateError) as error:
            QMessageBox.warning(self, "Einstellungen", str(error))
            self.update_channel_combo.blockSignals(True)
            self.update_channel_combo.setCurrentIndex(
                max(0, self.update_channel_combo.findData(self.updates.channel))
            )
            self.update_channel_combo.blockSignals(False)

    def _save_update_preferences(self, _checked: bool = False) -> None:
        try:
            self.updates.save_preferences(
                auto_check=self.auto_update_check.isChecked(),
                auto_download=self.auto_update_download.isChecked(),
                auto_install=self.auto_update_install.isChecked(),
            )
        except (OSError, UpdateError) as error:
            QMessageBox.warning(self, "Update-Einstellungen", str(error))

    @staticmethod
    def _about_group() -> QGroupBox:
        group = QGroupBox("Über MyOS")
        layout = QVBoxLayout(group)
        layout.setSpacing(3)
        details = (
            ("MyOS-Version", APP_VERSION),
            ("Basis", "Raspberry Pi OS Lite · Debian Trixie · ARM64"),
            ("Sitzung", f"X11 · {platform.machine()}"),
        )
        for label_text, value_text in details:
            line = QHBoxLayout()
            label = QLabel(label_text)
            label.setStyleSheet("font-weight: 600; color: #14596a;")
            value = QLabel(value_text)
            value.setWordWrap(True)
            line.addWidget(label)
            line.addStretch(1)
            line.addWidget(value)
            layout.addLayout(line)
        return group

    def apply_wallpaper(self, style: str, image_path: str = "") -> bool:
        if style not in WALLPAPER_PRESETS and style != "custom":
            self.wallpaper_status.setText("Diese Hintergrund-Auswahl ist ungültig.")
            return False
        if style == "custom" and not is_supported_image(Path(image_path)):
            self.wallpaper_status.setText(
                "Bitte ein lesbares PNG- oder JPEG-Bild auswählen."
            )
            return False

        self.store.set_wallpaper(style, image_path)
        for key, button in self._wallpaper_buttons.items():
            button.setChecked(style == key)
        self.image_button.setChecked(style == "custom")
        if style == "custom":
            self.wallpaper_status.setText(
                f"✓ Eigenes Bild aktiv: {Path(image_path).name}"
            )
        else:
            self.wallpaper_status.setText(
                f"{WALLPAPER_PRESETS[style][0]} angewendet und gespeichert."
            )
        self.wallpaper_changed.emit(style, image_path)
        return True

    def reset_wallpaper(self) -> None:
        self.store.reset_wallpaper()
        self.apply_wallpaper("lagoon")

    def _choose_wallpaper(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Hintergrundbild auswählen",
            str(Path.home()),
            "Bilder (*.png *.jpg *.jpeg)",
        )
        if path:
            if not self.apply_wallpaper("custom", path):
                self._restore_wallpaper_selection()
        else:
            self._restore_wallpaper_selection()

    def _restore_wallpaper_selection(self) -> None:
        style, _image_path = self.store.wallpaper()
        for key, button in self._wallpaper_buttons.items():
            button.setChecked(style == key)
        self.image_button.setChecked(style == "custom")

    def _apply_volume(self) -> None:
        success, message = self.audio.set_volume(self.volume_slider.value())
        self.volume_status.setText(message)
        self.volume_status.setStyleSheet(
            f"color: {self._status_color(success)};"
        )

    def _apply_display_mode(self) -> None:
        mode = self.display_combo.currentData()
        if not isinstance(mode, str):
            self.display_status.setText("Wähle zuerst eine verfügbare Auflösung.")
            return
        success, message = self.display.apply_mode(mode)
        self.display_status.setText(message)
        self.display_status.setStyleSheet(f"color: {self._status_color(success)};")

    def _status_color(self, success: bool) -> str:
        if self.theme == "dark":
            return "#68c98e" if success else "#e8897d"
        return "#17634d" if success else "#9a3f35"
