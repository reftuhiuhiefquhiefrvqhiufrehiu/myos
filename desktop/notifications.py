import json
from datetime import datetime
from typing import Callable

from PySide6.QtCore import (
    QSettings,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from theme import notification_stylesheet, normalize_theme


class NotificationStore:
    MAX_ITEMS = 100

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings("neonveil", "neonveil")
        self.error = ""

    def history(self) -> list[dict[str, str]]:
        raw = self.settings.value("notifications/history", "[]", type=str)
        try:
            items = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            self.error = "Der gespeicherte Benachrichtigungsverlauf ist ungültig."
            return []
        if not isinstance(items, list):
            self.error = "Der gespeicherte Benachrichtigungsverlauf ist ungültig."
            return []
        return [
            {key: value for key, value in item.items() if key in {"title", "message", "app", "time"} and isinstance(value, str)}
            for item in items[-self.MAX_ITEMS:]
            if isinstance(item, dict)
        ]

    def add(self, title: str, message: str, app: str = "NeonVeil") -> dict[str, str]:
        item = {
            "title": title[:120],
            "message": message[:500],
            "app": app[:80],
            "time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        }
        items = self.history()
        items.append(item)
        self._save(items[-self.MAX_ITEMS:])
        return item

    def clear(self) -> None:
        self._save([])

    def _save(self, items: list[dict[str, str]]) -> None:
        try:
            self.settings.setValue(
                "notifications/history", json.dumps(items, ensure_ascii=False)
            )
            self.settings.sync()
            if self.settings.status() != QSettings.Status.NoError:
                self.error = "Benachrichtigungsverlauf kann nicht gespeichert werden."
            else:
                self.error = ""
        except (OSError, RuntimeError) as error:
            self.error = f"Benachrichtigungsverlauf kann nicht gespeichert werden: {error}"


class NotificationPopup(QWidget):
    dismissed = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("notificationPopup")
        self.setFixedWidth(320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        header = QHBoxLayout()
        self.app_label = QLabel("NeonVeil")
        self.app_label.setObjectName("appName")
        self.close_button = QPushButton("×")
        self.close_button.setFixedSize(25, 24)
        self.close_button.setAccessibleName("Benachrichtigung schließen")
        self.close_button.clicked.connect(self._dismiss)
        header.addWidget(self.app_label)
        header.addStretch(1)
        header.addWidget(self.close_button)
        self.title_label = QLabel()
        self.title_label.setObjectName("title")
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        layout.addLayout(header)
        layout.addWidget(self.title_label)
        layout.addWidget(self.message_label)
        self.actions_widget = QWidget(self)
        self.actions_layout = QHBoxLayout(self.actions_widget)
        self.actions_layout.setContentsMargins(0, 4, 0, 0)
        self.actions_layout.setSpacing(4)
        self.actions_widget.hide()
        layout.addWidget(self.actions_widget)
        self._action_buttons: list[QPushButton] = []
        self.set_theme("light")
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self._dismiss)

    def show_notification(
        self,
        item: dict[str, str],
        duration_ms: int = 5000,
        actions: tuple[tuple[str, Callable[[], None]], ...] = (),
    ) -> None:
        for button in self._action_buttons:
            self.actions_layout.removeWidget(button)
            button.deleteLater()
        self._action_buttons.clear()
        for label, callback in actions:
            button = QPushButton(label, self.actions_widget)
            button.clicked.connect(
                lambda _checked=False, action=callback: self._run_action(action)
            )
            self.actions_layout.addWidget(button)
            self._action_buttons.append(button)
        self.actions_widget.setVisible(bool(actions))
        self.app_label.setText(item["app"])
        self.title_label.setText(item["title"])
        self.message_label.setText(item["message"])
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 12, area.bottom() - self.height() - 58)
        self.show()
        self.raise_()
        self.dismiss_timer.start(max(1000, duration_ms))

    def _run_action(self, callback: Callable[[], None]) -> None:
        self.dismiss_timer.stop()
        self.hide()
        callback()
        self.dismissed.emit()

    def _dismiss(self) -> None:
        self.dismiss_timer.stop()
        self.hide()
        self.dismissed.emit()

    def set_theme(self, theme: str) -> None:
        self.theme = normalize_theme(theme)
        self.setStyleSheet(notification_stylesheet(self.theme))


class NotificationHistoryWindow(QMainWindow):
    def __init__(self, store: NotificationStore | None = None) -> None:
        super().__init__()
        self.store = store or NotificationStore()
        self.setWindowTitle("Benachrichtigungen")
        self.setMinimumSize(320, 260)
        self.resize(460, 380)
        content = QWidget()
        layout = QVBoxLayout(content)
        heading = QLabel("Benachrichtigungen")
        heading.setObjectName("heading")
        self.items = QListWidget()
        self.empty_label = QLabel("Noch keine Benachrichtigungen.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clear_button = QPushButton("Verlauf leeren")
        self.clear_button.clicked.connect(self.clear_history)
        self.storage_status = QLabel()
        self.storage_status.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(self.items, 1)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.storage_status)
        layout.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignRight)
        self.setCentralWidget(content)
        self.refresh()

    def refresh(self) -> None:
        self.items.clear()
        for entry in reversed(self.store.history()):
            self.items.addItem(
                QListWidgetItem(
                    f"{entry.get('time', '')}  ·  {entry.get('app', 'NeonVeil')}\n"
                    f"{entry.get('title', '')}\n{entry.get('message', '')}"
                )
            )
        has_items = self.items.count() > 0
        self.items.setVisible(has_items)
        self.empty_label.setVisible(not has_items)
        self.clear_button.setEnabled(has_items)
        self.storage_status.setText(self.store.error)
        self.storage_status.setStyleSheet("color: #9a3f35;" if self.store.error else "")

    def clear_history(self) -> None:
        self.store.clear()
        self.refresh()


class NotificationCenter:
    def __init__(self, store: NotificationStore | None = None) -> None:
        self.store = store or NotificationStore()
        self.popup = NotificationPopup()

    def notify(
        self,
        title: str,
        message: str,
        app: str = "NeonVeil",
        *,
        actions: tuple[tuple[str, Callable[[], None]], ...] = (),
    ) -> None:
        item = self.store.add(title, message, app)
        self.popup.show_notification(item, actions=actions)

    def notify_update(
        self,
        version: str,
        *,
        details: Callable[[], None],
        install: Callable[[], None],
        later: Callable[[], None] | None = None,
        message: str | None = None,
    ) -> None:
        item = self.store.add(
            "Ein neues NeonVeil-Update ist verfügbar",
            message or f"NeonVeil {version} kann heruntergeladen und installiert werden.",
            "NeonVeil Update",
        )
        self.popup.show_notification(
            item,
            duration_ms=12_000,
            actions=(
                ("Details", details),
                ("Später", later or (lambda: None)),
                ("Update installieren", install),
            ),
        )

    def set_theme(self, theme: str) -> None:
        self.popup.set_theme(theme)

    def open_history(self) -> NotificationHistoryWindow:
        window = NotificationHistoryWindow(self.store)
        window.show()
        return window
