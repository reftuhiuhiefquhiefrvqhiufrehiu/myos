import subprocess
import json
from typing import Callable

from PySide6.QtCore import QEvent, QSettings, QTime, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QLineEdit,
    QWidgetAction,
    QWidget,
)


class Taskbar(QWidget):
    application_requested = Signal(str)
    logout_requested = Signal()

    PROGRAMS = (
        ("browser", "Browser"),
        ("code", "Code Studio"),
        ("files", "Dateien"),
        ("downloads", "Downloads"),
        ("settings", "Einstellungen"),
        ("pictures", "Bilder"),
        ("editor", "Texteditor"),
        ("terminal", "Terminal"),
        ("clock", "Uhr"),
        ("about", "Über NeonVeil"),
        ("music", "Musik"),
        ("screenshot", "Screenshot"),
        ("trash", "Papierkorb"),
        ("pi-tools", "Raspberry-Pi-Werkzeuge"),
        ("update-manager", "NeonVeil Update Manager"),
    )

    def __init__(
        self,
        power_command: Callable[[str], tuple[bool, str]] | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._power_command = power_command or self._run_power_command
        self.preferences = settings or QSettings("neonveil", "neonveil")
        self._profile_name = ""
        self.setObjectName("taskbar")
        self.setWindowTitle("NeonVeil Taskleiste")
        self.setFixedHeight(46)
        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.geometry()
            self.setGeometry(
                geometry.x(),
                geometry.y() + geometry.height() - self.height(),
                geometry.width(),
                self.height(),
            )
        else:
            self.setGeometry(0, 722, 1024, self.height())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 8, 5)
        layout.setSpacing(6)

        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("startButton")
        self.start_button.setMinimumWidth(76)
        self.start_button.setAccessibleName("Startmenü öffnen")
        self.start_button.clicked.connect(self._show_start_menu)
        layout.addWidget(self.start_button)

        self.tasks_layout = QHBoxLayout()
        self.tasks_layout.setContentsMargins(0, 0, 0, 0)
        self.tasks_layout.setSpacing(4)
        self._task_windows: dict[QWidget, QToolButton] = {}
        layout.addLayout(self.tasks_layout)
        layout.addStretch(1)

        self.clock = QLabel()
        self.clock.setObjectName("clock")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock.setMinimumWidth(68)
        self.clock.setAccessibleName("Uhr")
        layout.addWidget(self.clock)

        self._light_stylesheet = """
            QWidget#taskbar {
                background: #dbe5e8;
                border-top: 1px solid #f8fcfd;
            }
            QPushButton, QToolButton {
                color: #20333b;
                background: #f0f5f6;
                border: 1px solid #a2b6bc;
                border-radius: 3px;
                padding: 5px 10px;
            }
            QPushButton:hover, QToolButton:hover {
                background: #ffffff;
                border-color: #39817f;
            }
            QPushButton:focus, QToolButton:focus {
                border: 2px solid #176c67;
                background: #f5fbfa;
            }
            QPushButton#startButton {
                color: #ffffff;
                font-weight: 600;
                background: #176c67;
                border-color: #104e4b;
            }
            QPushButton#startButton:hover { background: #247f79; }
            QToolButton:checked {
                background: #c6dddc;
                border-color: #4c8a87;
            }
            QLabel#clock {
                background: #edf3f4;
                border: 1px solid #a8bbc0;
                border-radius: 3px;
                padding: 4px;
                color: #173d48;
                font-weight: 600;
            }
            QMenu {
                background: #f8fafb;
                border: 1px solid #668995;
                padding: 4px;
            }
            QMenu::item { padding: 8px 30px 8px 12px; }
            QMenu::item:selected { background: #c8e1e7; color: #173d48; }
            """
        self.set_theme(self.preferences.value("appearance/theme", "light", type=str))

        self._update_clock()
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._start_menu: QMenu | None = None
        self._program_menu: QMenu | None = None

    def _update_clock(self) -> None:
        now = QTime.currentTime()
        self.clock.setText(now.toString("HH:mm"))
        self.clock.setToolTip(now.toString("HH:mm:ss"))

    def _show_start_menu(self) -> None:
        menu = self.build_start_menu()
        menu.exec(
            self.start_button.mapToGlobal(
                self.start_button.rect().bottomLeft()
            )
        )

    def build_start_menu(self) -> QMenu:
        menu = QMenu(self)
        self._start_menu = menu
        profile_menu = menu.addMenu(f"Profil · {self._profile_name or 'Gast'}")
        if profile_menu is not None:
            logout = profile_menu.addAction("Abmelden…")
            logout.triggered.connect(self.logout_requested.emit)

        self._recent_menu = menu.addMenu("Zuletzt verwendet")
        self._fill_saved_menu(self._recent_menu, "start/recent", "Noch keine Programme gestartet.")
        self._pinned_menu = menu.addMenu("Angeheftete Programme")
        self._fill_saved_menu(self._pinned_menu, "start/pinned", "Noch keine Programme angeheftet.")
        pin_menu = menu.addMenu("Programme anheften")
        self._pin_actions = {}
        pinned_ids = self._saved_ids("start/pinned")
        for app_id, title in sorted(self.PROGRAMS, key=lambda entry: entry[1].casefold()):
            action = pin_menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(app_id in pinned_ids)
            action.toggled.connect(
                lambda checked, selected=app_id: self._set_pinned(selected, checked)
            )
            self._pin_actions[app_id] = action

        programs = menu.addMenu("Programme")
        self._program_menu = programs
        self._program_actions = {}
        search = QLineEdit()
        search.setPlaceholderText("Programme suchen…")
        search.setClearButtonEnabled(True)
        search.setAccessibleName("Programme suchen")
        search_action = QWidgetAction(programs)
        search_action.setText("Programme suchen")
        search_action.setDefaultWidget(search)
        programs.addAction(search_action)
        programs.addSeparator()

        for app_id, title in sorted(self.PROGRAMS, key=lambda entry: entry[1].casefold()):
            action = programs.addAction(title)
            self._program_actions[app_id] = action
            action.triggered.connect(
                lambda _checked=False, selected=app_id: self._launch(selected)
            )
        search.textChanged.connect(self._filter_programs)

        files_action = menu.addAction("Dateien")
        files_action.triggered.connect(lambda: self._launch("files"))
        downloads_action = menu.addAction("Downloads")
        downloads_action.triggered.connect(lambda: self._launch("downloads"))
        settings_action = menu.addAction("Einstellungen")
        settings_action.triggered.connect(lambda: self._launch("settings"))
        notification_action = menu.addAction("Benachrichtigungen")
        notification_action.triggered.connect(lambda: self._launch("notifications"))
        about_action = menu.addAction("Über NeonVeil")
        about_action.triggered.connect(lambda: self._launch("about"))
        menu.addSeparator()

        power_menu = menu.addMenu("Ein/Aus")
        shutdown_action = power_menu.addAction("Ausschalten…")
        shutdown_action.triggered.connect(lambda: self._confirm_power_action("poweroff"))
        restart_action = power_menu.addAction("Neustarten…")
        restart_action.triggered.connect(lambda: self._confirm_power_action("reboot"))
        return menu

    def _saved_ids(self, key: str) -> list[str]:
        value = self.preferences.value(key, "[]", type=str)
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
        return [item for item in decoded if isinstance(item, str)] if isinstance(decoded, list) else []

    def _fill_saved_menu(self, menu: QMenu, key: str, empty_message: str) -> None:
        ids = self._saved_ids(key)
        titles = dict(self.PROGRAMS)
        if not ids:
            empty_action = menu.addAction(empty_message)
            empty_action.setEnabled(False)
            return
        for app_id in ids:
            if app_id not in titles:
                continue
            action = menu.addAction(titles[app_id])
            action.triggered.connect(lambda _checked=False, selected=app_id: self._launch(selected))

    def _launch(self, app_id: str) -> None:
        self.record_recent(app_id)
        if self._start_menu is not None:
            self._start_menu.close()
        self.application_requested.emit(app_id)

    def record_recent(self, app_id: str) -> None:
        if app_id not in dict(self.PROGRAMS):
            return
        recent = [item for item in self._saved_ids("start/recent") if item != app_id]
        recent.insert(0, app_id)
        self.preferences.setValue("start/recent", json.dumps(recent[:8]))
        self.preferences.sync()

    def _set_pinned(self, app_id: str, pinned_state: bool) -> None:
        pinned = self._saved_ids("start/pinned")
        if pinned_state and app_id not in pinned:
            pinned.append(app_id)
        elif not pinned_state and app_id in pinned:
            pinned.remove(app_id)
        self.preferences.setValue("start/pinned", json.dumps(pinned))
        self.preferences.sync()
        self._pinned_menu.clear()
        self._fill_saved_menu(self._pinned_menu, "start/pinned", "Noch keine Programme angeheftet.")

    def _filter_programs(self, text: str) -> None:
        query = text.casefold().strip()
        for app_id, action in self._program_actions.items():
            action.setVisible(query in dict(self.PROGRAMS)[app_id].casefold())

    def set_profile(self, username: str) -> None:
        self._profile_name = username
        self.start_button.setText(f"Start · {username}")
        self.start_button.setToolTip(f"Startmenü · angemeldet als {username}")

    def set_theme(self, theme: str) -> None:
        self.theme = "dark" if theme == "dark" else "light"
        stylesheet = self._light_stylesheet
        if self.theme == "dark":
            replacements = {
                "#dbe5e8": "#252f34", "#f8fcfd": "#43545a",
                "#f8fafb": "#2b383e",
                "#20333b": "#e4ecee", "#f0f5f6": "#35464d",
                "#ffffff": "#202a2f", "#a2b6bc": "#5d747b",
                "#edf3f4": "#303e44", "#173d48": "#dce9eb",
                "#f5fbfa": "#30464a", "#c6dddc": "#315550",
                "#c8e1e7": "#354f55",
            }
            for light, dark in replacements.items():
                stylesheet = stylesheet.replace(light, dark)
        self.setStyleSheet(stylesheet)

    def add_window(self, window: QWidget) -> None:
        title = window.windowTitle()
        button = QToolButton(self)
        button.setText(title)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setCheckable(True)
        button.setChecked(True)
        button.setMaximumWidth(190)
        button.setToolTip(title)
        button.clicked.connect(lambda: self._activate_window(window))
        self.tasks_layout.addWidget(button)

        self._task_windows[window] = button
        window.installEventFilter(self)
        window.destroyed.connect(button.deleteLater)
        window.destroyed.connect(lambda: self._remove_task_button(button))

        def sync_title(new_title: str) -> None:
            button.setText(new_title)
            button.setToolTip(new_title)

        window.windowTitleChanged.connect(sync_title)
        self.raise_()

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        if event.type() in (
            QEvent.Type.WindowStateChange,
            QEvent.Type.WindowActivate,
        ):
            button = self._task_windows.get(watched)
            if button is not None:
                button.setChecked(watched.isVisible() and not watched.isMinimized())
            if event.type() == QEvent.Type.WindowActivate:
                self.raise_()
        return super().eventFilter(watched, event)

    def _remove_task_button(self, button: QToolButton) -> None:
        self.tasks_layout.removeWidget(button)
        for window, task_button in list(self._task_windows.items()):
            if task_button is button:
                del self._task_windows[window]

    @staticmethod
    def _activate_window(window: QWidget) -> None:
        if not window.isVisible() or window.isMinimized():
            window.showNormal()
            window.raise_()
            window.activateWindow()
        elif window.isActiveWindow():
            window.showMinimized()
        else:
            window.raise_()
            window.activateWindow()

    def _confirm_power_action(self, command: str) -> None:
        if command not in {"poweroff", "reboot"}:
            self._show_power_error(command, "Ungültige Systemaktion.")
            return
        verb = "herunterfahren" if command == "poweroff" else "neu starten"
        answer = QMessageBox.question(
            self,
            "Systemaktion bestätigen",
            f"Möchtest du NeonVeil wirklich {verb}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        success, detail = self._power_command(command)
        if not success:
            self._show_power_error(command, detail)

    @staticmethod
    def _run_power_command(command: str) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["systemctl", command],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return False, str(error)
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip() or f"systemctl beendete sich mit {result.returncode}."

    def _show_power_error(self, command: str, detail: str = "") -> None:
        QMessageBox.critical(
            self,
            "Systemaktion fehlgeschlagen",
            f"systemctl {command} konnte nicht ausgeführt werden."
            + (f"\n\n{detail}" if detail else ""),
        )
