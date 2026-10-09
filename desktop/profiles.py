import json
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


@dataclass(frozen=True)
class DesktopProfile:
    username: str
    avatar: str = ""


class ProfileStore:
    """Convenience profiles stored in the user's normal neonveil settings."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings("neonveil", "neonveil")

    def profiles(self) -> list[DesktopProfile]:
        raw = self.settings.value("profiles/list", "[]", type=str)
        try:
            entries = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(entries, list):
            return []
        profiles = []
        for entry in entries:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("username"), str)
                and entry["username"].strip()
            ):
                avatar = entry.get("avatar", "")
                profiles.append(
                    DesktopProfile(entry["username"], avatar if isinstance(avatar, str) else "")
                )
        return profiles

    def create(self, username: str, avatar: str = "") -> tuple[bool, str]:
        username = " ".join(username.split())
        if not 1 <= len(username) <= 32 or not re.fullmatch(
            r"[\w .'-]+", username, flags=re.UNICODE
        ):
            return False, "Bitte einen Namen mit 1 bis 32 Buchstaben eingeben."
        if any(profile.username.casefold() == username.casefold() for profile in self.profiles()):
            return False, "Dieser Profilname ist bereits vorhanden."
        profiles = self.profiles()
        profiles.append(DesktopProfile(username, avatar))
        self._save(profiles)
        return True, ""

    def _save(self, profiles: list[DesktopProfile]) -> None:
        self.settings.setValue(
            "profiles/list",
            json.dumps(
                [
                    {"username": profile.username, "avatar": profile.avatar}
                    for profile in profiles
                ],
                ensure_ascii=False,
            ),
        )
        self.settings.sync()


class ProfileChooser(QDialog):
    """Select or create a local desktop profile; this is not OS authentication."""

    def __init__(self, store: ProfileStore | None = None) -> None:
        super().__init__()
        self.store = store or ProfileStore()
        self.profile: DesktopProfile | None = None
        self._avatar_path = ""
        self.setWindowTitle("Bei NeonVeil anmelden")
        self.setMinimumSize(320, 240)
        self.resize(390, 330)

        layout = QVBoxLayout(self)
        heading = QLabel("Willkommen bei NeonVeil")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        layout.addWidget(QLabel("Wähle ein lokales Desktop-Profil oder erstelle ein neues."))
        self.profile_list = QListWidget()
        self.profile_list.itemDoubleClicked.connect(self._sign_in)
        layout.addWidget(self.profile_list, 1)

        self.username = QLineEdit()
        self.username.setPlaceholderText("Profilname")
        self.username.setMaxLength(32)
        self.username.returnPressed.connect(self.create_profile)
        layout.addWidget(self.username)
        avatar_row = QHBoxLayout()
        self.avatar_button = QPushButton("Profilbild auswählen…")
        self.avatar_button.clicked.connect(self._choose_avatar)
        self.avatar_status = QLabel("Kein Bild ausgewählt")
        self.avatar_status.setWordWrap(True)
        avatar_row.addWidget(self.avatar_button)
        avatar_row.addWidget(self.avatar_status, 1)
        layout.addLayout(avatar_row)

        buttons = QHBoxLayout()
        self.create_button = QPushButton("Profil erstellen")
        self.create_button.clicked.connect(self.create_profile)
        self.sign_in_button = QPushButton("Anmelden")
        self.sign_in_button.clicked.connect(self._sign_in_selected)
        self.sign_in_button.setEnabled(False)
        self.profile_list.currentItemChanged.connect(
            lambda current, _previous: self.sign_in_button.setEnabled(current is not None)
        )
        buttons.addWidget(self.create_button)
        buttons.addStretch(1)
        buttons.addWidget(self.sign_in_button)
        layout.addLayout(buttons)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setObjectName("notice")
        layout.addWidget(self.notice)

        stylesheet = """
            QDialog { background: #f0f4f5; color: #20333b; }
            QLabel#heading { color: #14596a; font-size: 21px; font-weight: 700; }
            QListWidget, QLineEdit { background: #ffffff; border: 1px solid #91a8af; padding: 6px; }
            QPushButton { background: #e8eff1; border: 1px solid #91a8af; padding: 6px 10px; }
            QPushButton:hover { background: #ffffff; border-color: #176c67; }
            QPushButton:focus, QLineEdit:focus, QListWidget:focus { border: 2px solid #176c67; }
            QLabel#notice { color: #9a3f35; }
            """
        if self.store.settings.value("appearance/theme", "light", type=str) == "dark":
            for light, dark in {
                "#f0f4f5": "#252f34",
                "#ffffff": "#202a2f",
                "#e8eff1": "#35464d",
                "#20333b": "#e4ecee",
                "#91a8af": "#5d747b",
            }.items():
                stylesheet = stylesheet.replace(light, dark)
        self.setStyleSheet(stylesheet)
        self._refresh_profiles()

    def _refresh_profiles(self) -> None:
        self.profile_list.clear()
        for profile in self.store.profiles():
            item = QListWidgetItem(self._avatar_icon(profile), profile.username)
            item.setData(Qt.ItemDataRole.UserRole, profile)
            self.profile_list.addItem(item)
        if self.profile_list.count():
            self.profile_list.setCurrentRow(0)

    @staticmethod
    def _avatar_icon(profile: DesktopProfile):
        if profile.avatar:
            pixmap = QPixmap(profile.avatar)
            if not pixmap.isNull():
                return pixmap.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        pixmap = QPixmap(40, 40)
        pixmap.fill(Qt.GlobalColor.transparent)
        from PySide6.QtGui import QPainter, QColor, QFont

        painter = QPainter(pixmap)
        painter.setBrush(QColor("#176c67"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, 38, 38)
        painter.setPen(QColor("white"))
        font = QFont()
        font.setPointSize(16)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, profile.username[0].upper())
        painter.end()
        return pixmap

    def _choose_avatar(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Profilbild auswählen", str(Path.home()), "Bilder (*.png *.jpg *.jpeg)"
        )
        if path:
            self._avatar_path = path
            self.avatar_status.setText(Path(path).name)

    def create_profile(self) -> None:
        success, message = self.store.create(self.username.text(), self._avatar_path)
        if not success:
            self.notice.setText(message)
            return
        self._refresh_profiles()
        item = self.profile_list.item(self.profile_list.count() - 1)
        self.profile_list.setCurrentItem(item)
        self._sign_in_selected()

    def _sign_in_selected(self) -> None:
        item = self.profile_list.currentItem()
        if item is not None:
            self._sign_in(item)

    def _sign_in(self, item: QListWidgetItem) -> None:
        profile = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(profile, DesktopProfile):
            self.profile = profile
            self.accept()
