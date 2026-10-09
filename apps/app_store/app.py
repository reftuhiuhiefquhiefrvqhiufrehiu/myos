from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from appstore.core import AppStore, AppStoreError, CatalogApp, InstalledApp

LOGGER = logging.getLogger("neonveil.appstore.ui")


class AppStoreWorker(QThread):
    """Background worker for network and file operations."""

    progress = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[..., object]) -> None:
        super().__init__()
        self.operation = operation

    def run(self) -> None:
        try:
            result = self.operation()
            self.completed.emit(result)
        except AppStoreError as error:
            self.failed.emit(error.user_message)
        except Exception as error:
            LOGGER.exception("Fehler im App-Store-Worker.")
            self.failed.emit(f"Unerwarteter Fehler: {error}")


class RepoSettingsDialog(QDialog):
    """Dialog to configure GitHub repository source and branch."""

    def __init__(self, current_repo: str, current_ref: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GitHub-App-Quelle konfigurieren")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        info = QLabel(
            "Wähle das GitHub-Repository und den Git-Branch, aus dem Anwendungen "
            "geladen und aktualisiert werden sollen."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.repo_input = QLineEdit(current_repo)
        self.repo_input.setPlaceholderText("https://github.com/owner/repo oder owner/repo")
        self.ref_input = QLineEdit(current_ref)
        self.ref_input.setPlaceholderText("main")

        form.addRow("GitHub Repository:", self.repo_input)
        form.addRow("Branch / Tag:", self.ref_input)
        layout.addLayout(form)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    @property
    def repository(self) -> str:
        return self.repo_input.text().strip()

    @property
    def ref(self) -> str:
        return self.ref_input.text().strip() or "main"


class AppDetailDialog(QDialog):
    """Dialog showing detailed application information."""

    def __init__(self, app: CatalogApp, installed: InstalledApp | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"App-Details – {app.name}")
        self.setMinimumSize(500, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        avatar = QLabel(app.name[0].upper())
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(54, 54)
        avatar.setStyleSheet(
            """
            QLabel {
                background: #176c67;
                color: #ffffff;
                font-size: 24px;
                font-weight: bold;
                border-radius: 27px;
                border: 2px solid #35c9bd;
            }
            """
        )
        header.addWidget(avatar)

        title_box = QVBoxLayout()
        name_lbl = QLabel(app.name)
        name_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #14596a;")
        id_lbl = QLabel(f"ID: {app.id} • Kategorie: {app.category} • Autor: {app.author}")
        id_lbl.setStyleSheet("color: #71858b; font-size: 12px;")
        title_box.addWidget(name_lbl)
        title_box.addWidget(id_lbl)
        header.addLayout(title_box)
        header.addStretch(1)
        layout.addLayout(header)

        # Status
        status_box = QHBoxLayout()
        ver_text = f"Version im Store: {app.version}"
        if installed:
            ver_text += f" | Installierte Version: {installed.version}"
            if installed.installed:
                ver_text += f" (Installiert: {installed.installed})"
        ver_lbl = QLabel(ver_text)
        ver_lbl.setStyleSheet("font-weight: 600; color: #20333b;")
        status_box.addWidget(ver_lbl)
        status_box.addStretch(1)
        layout.addLayout(status_box)

        # Description
        desc_lbl = QLabel("Beschreibung:")
        desc_lbl.setStyleSheet("font-weight: bold; margin-top: 6px;")
        layout.addWidget(desc_lbl)

        desc_box = QTextEdit()
        desc_box.setReadOnly(True)
        desc_text = app.description or app.summary or "Keine Beschreibung verfügbar."
        desc_box.setPlainText(desc_text)
        desc_box.setMaximumHeight(100)
        layout.addWidget(desc_box)

        # Files
        files_lbl = QLabel(f"Enthaltene Dateien ({len(app.files)}):")
        files_lbl.setStyleSheet("font-weight: bold;")
        layout.addWidget(files_lbl)

        file_list = QTextEdit()
        file_list.setReadOnly(True)
        lines = [f"• {f} (SHA256: {sha[:12]}...)" for f, sha in app.files.items()]
        file_list.setPlainText("\n".join(lines))
        layout.addWidget(file_list)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


class AppCard(QFrame):
    """Visual card representing an application in the store."""

    open_requested = Signal(str)
    install_requested = Signal(str)
    uninstall_requested = Signal(str)
    shortcut_requested = Signal(str)
    details_requested = Signal(str)

    def __init__(
        self,
        app: CatalogApp,
        installed: InstalledApp | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.installed = installed
        self.setObjectName("appCard")
        self.setStyleSheet(
            """
            QFrame#appCard {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
                padding: 12px;
            }
            QFrame#appCard:hover {
                border-color: #35c9bd;
                background: rgba(255, 255, 255, 0.08);
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # Top row: icon/avatar + title/category + status badge
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        # Avatar
        letter = app.name[0].upper() if app.name else "A"
        avatar = QLabel(letter)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(44, 44)
        avatar.setStyleSheet(
            """
            QLabel {
                background: #176c67;
                color: #ffffff;
                font-size: 20px;
                font-weight: bold;
                border-radius: 22px;
                border: 2px solid #35c9bd;
            }
            """
        )
        top_row.addWidget(avatar)

        # Title & Meta
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        title = QLabel(app.name)
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #14596a;")
        meta = QLabel(f"v{app.version} • {app.category}")
        meta.setStyleSheet("font-size: 12px; color: #71858b;")
        info_col.addWidget(title)
        info_col.addWidget(meta)
        top_row.addLayout(info_col)
        top_row.addStretch(1)

        # Status badge
        self.status_badge = QLabel()
        self._update_status_badge()
        top_row.addWidget(self.status_badge)
        layout.addLayout(top_row)

        # Summary
        summary = QLabel(app.summary or app.description or "Keine Beschreibung verfügbar.")
        summary.setWordWrap(True)
        summary.setStyleSheet("color: #536c74; font-size: 13px; line-height: 1.3;")
        layout.addWidget(summary)

        # Action Buttons
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self.btn_details = QPushButton("Details")
        self.btn_details.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                color: #71858b;
                border: 1px solid #71858b;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                color: #e4ecee;
                border-color: #35c9bd;
            }
            """
        )
        self.btn_details.clicked.connect(lambda: self.details_requested.emit(self.app.id))
        actions_row.addWidget(self.btn_details)

        actions_row.addStretch(1)

        if self.installed is not None:
            # Check update
            has_update = False
            try:
                from appstore.core import compare_versions

                has_update = compare_versions(self.app.version, self.installed.version) > 0
            except Exception:
                has_update = self.app.version != self.installed.version

            if has_update:
                self.btn_update = QPushButton("Aktualisieren")
                self.btn_update.setStyleSheet(
                    """
                    QPushButton {
                        background: #14596a;
                        color: #ffffff;
                        border: 1px solid #35c9bd;
                        border-radius: 6px;
                        padding: 6px 14px;
                        font-weight: bold;
                        font-size: 12px;
                    }
                    QPushButton:hover { background: #176c67; }
                    """
                )
                self.btn_update.clicked.connect(lambda: self.install_requested.emit(self.app.id))
                actions_row.addWidget(self.btn_update)

            self.btn_open = QPushButton("Öffnen")
            self.btn_open.setStyleSheet(
                """
                QPushButton {
                    background: #176c67;
                    color: #ffffff;
                    border: 1px solid #35c9bd;
                    border-radius: 6px;
                    padding: 6px 14px;
                    font-weight: bold;
                    font-size: 12px;
                }
                QPushButton:hover { background: #247f79; }
                """
            )
            self.btn_open.clicked.connect(lambda: self.open_requested.emit(self.app.id))
            actions_row.addWidget(self.btn_open)

            self.btn_shortcut = QPushButton("Desktop-Icon")
            self.btn_shortcut.setToolTip("Verknüpfung auf dem Schreibtisch anlegen")
            self.btn_shortcut.setStyleSheet(
                """
                QPushButton {
                    background: rgba(255, 255, 255, 0.05);
                    color: #71858b;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: 12px;
                }
                QPushButton:hover { color: #35c9bd; border-color: #35c9bd; }
                """
            )
            self.btn_shortcut.clicked.connect(lambda: self.shortcut_requested.emit(self.app.id))
            actions_row.addWidget(self.btn_shortcut)

            self.btn_uninstall = QPushButton("Deinstallieren")
            self.btn_uninstall.setStyleSheet(
                """
                QPushButton {
                    background: rgba(154, 63, 53, 0.15);
                    color: #e08a80;
                    border: 1px solid rgba(154, 63, 53, 0.3);
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 12px;
                }
                QPushButton:hover { background: rgba(154, 63, 53, 0.35); }
                """
            )
            self.btn_uninstall.clicked.connect(lambda: self.uninstall_requested.emit(self.app.id))
            actions_row.addWidget(self.btn_uninstall)
        else:
            self.btn_install = QPushButton("Installieren")
            self.btn_install.setStyleSheet(
                """
                QPushButton {
                    background: #176c67;
                    color: #ffffff;
                    border: 1px solid #35c9bd;
                    border-radius: 6px;
                    padding: 6px 16px;
                    font-weight: bold;
                    font-size: 13px;
                }
                QPushButton:hover { background: #247f79; }
                """
            )
            self.btn_install.clicked.connect(lambda: self.install_requested.emit(self.app.id))
            actions_row.addWidget(self.btn_install)

        layout.addLayout(actions_row)

    def _update_status_badge(self) -> None:
        if self.installed is not None:
            has_update = False
            try:
                from appstore.core import compare_versions

                has_update = compare_versions(self.app.version, self.installed.version) > 0
            except Exception:
                has_update = self.app.version != self.installed.version

            if has_update:
                self.status_badge.setText("Update verfügbar")
                self.status_badge.setStyleSheet(
                    """
                    QLabel {
                        background: rgba(20, 89, 106, 0.3);
                        color: #69bdb1;
                        border: 1px solid #69bdb1;
                        border-radius: 10px;
                        padding: 3px 10px;
                        font-size: 11px;
                        font-weight: bold;
                    }
                    """
                )
            else:
                self.status_badge.setText("Installiert")
                self.status_badge.setStyleSheet(
                    """
                    QLabel {
                        background: rgba(47, 122, 79, 0.2);
                        color: #7bd3a4;
                        border: 1px solid #2f7a4f;
                        border-radius: 10px;
                        padding: 3px 10px;
                        font-size: 11px;
                        font-weight: bold;
                    }
                    """
                )
        else:
            self.status_badge.setText("Verfügbar")
            self.status_badge.setStyleSheet(
                """
                QLabel {
                    background: rgba(255, 255, 255, 0.05);
                    color: #71858b;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 10px;
                    padding: 3px 10px;
                    font-size: 11px;
                }
                """
            )


class AppStoreWindow(QMainWindow):
    """NeonVeil App Store Window with GitHub integration."""

    launch_app_requested = Signal(object)
    app_installed = Signal(str)
    app_uninstalled = Signal(str)

    def __init__(
        self,
        store: AppStore | None = None,
        desktop_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("NeonVeil App Store")
        self.setMinimumSize(680, 520)
        self.resize(840, 620)

        self.store = store or AppStore()
        self.desktop_dir = desktop_dir or (Path.home() / "Desktop")
        self.worker: AppStoreWorker | None = None

        self._active_filter = "all"  # all, installed, available, updates
        self._search_text = ""
        self._category_filter = "Alle Kategorien"

        self._build_ui()
        self.refresh_catalog(force_remote=False)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(14)

        # Header section
        header_frame = QFrame()
        header_frame.setObjectName("storeHeader")
        header_frame.setStyleSheet(
            """
            QFrame#storeHeader {
                background: rgba(23, 108, 103, 0.15);
                border: 1px solid rgba(53, 201, 189, 0.25);
                border-radius: 12px;
                padding: 14px;
            }
            """
        )
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(14)

        store_icon = QLabel("🛒")
        store_icon.setStyleSheet("font-size: 32px;")
        header_layout.addWidget(store_icon)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        heading = QLabel("NeonVeil App Store")
        heading.setStyleSheet("font-size: 22px; font-weight: bold; color: #14596a;")
        self.subtitle = QLabel(f"GitHub: {self.store.owner}/{self.store.repo} @ {self.store.ref}")
        self.subtitle.setStyleSheet("color: #536c74; font-size: 13px;")
        title_box.addWidget(heading)
        title_box.addWidget(self.subtitle)
        header_layout.addLayout(title_box)

        header_layout.addStretch(1)

        # Header buttons
        self.btn_refresh = QPushButton("↻ Aktualisieren")
        self.btn_refresh.setStyleSheet(
            """
            QPushButton {
                background: #176c67;
                color: #ffffff;
                border: 1px solid #35c9bd;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover { background: #247f79; }
            """
        )
        self.btn_refresh.clicked.connect(lambda: self.refresh_catalog(force_remote=True))
        header_layout.addWidget(self.btn_refresh)

        self.btn_settings = QPushButton("⚙ Quelle")
        self.btn_settings.setToolTip("GitHub-Repository oder Branch konfigurieren")
        self.btn_settings.setStyleSheet(
            """
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #e4ecee;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 13px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.16); }
            """
        )
        self.btn_settings.clicked.connect(self._open_repo_settings)
        header_layout.addWidget(self.btn_settings)

        main_layout.addWidget(header_frame)

        # Controls row: Search, Filter Tabs, Category Dropdown
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Apps durchsuchen...")
        self.search_input.setStyleSheet(
            """
            QLineEdit {
                background: rgba(255, 255, 255, 0.06);
                color: #e4ecee;
                border: 1px solid #536a71;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #35c9bd;
            }
            """
        )
        self.search_input.textChanged.connect(self._on_search_changed)
        ctrl_row.addWidget(self.search_input, 2)

        self.cat_combo = QComboBox()
        self.cat_combo.addItem("Alle Kategorien")
        self.cat_combo.setStyleSheet(
            """
            QComboBox {
                background: rgba(255, 255, 255, 0.06);
                color: #e4ecee;
                border: 1px solid #536a71;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 13px;
                min-width: 140px;
            }
            """
        )
        self.cat_combo.currentIndexChanged.connect(self._on_category_changed)
        ctrl_row.addWidget(self.cat_combo, 1)

        # Filter buttons
        filter_group = QWidget()
        fg_layout = QHBoxLayout(filter_group)
        fg_layout.setContentsMargins(0, 0, 0, 0)
        fg_layout.setSpacing(4)

        self.filter_buttons: dict[str, QPushButton] = {}
        filters = [
            ("all", "Alle"),
            ("installed", "Installiert"),
            ("available", "Verfügbar"),
            ("updates", "Updates"),
        ]
        for key, label in filters:
            btn = QPushButton(label)
            btn.setCheckable(True)
            if key == "all":
                btn.setChecked(True)
            self._style_filter_button(btn, is_active=(key == "all"))
            btn.clicked.connect(lambda checked=False, k=key: self._set_filter(k))
            fg_layout.addWidget(btn)
            self.filter_buttons[key] = btn

        ctrl_row.addWidget(filter_group)
        main_layout.addLayout(ctrl_row)

        # Progress / Status notification
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #176c67;
                border-radius: 4px;
                text-align: center;
                height: 14px;
            }
            QProgressBar::chunk {
                background: #35c9bd;
            }
            """
        )
        main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #65b9ae; font-size: 12px;")
        self.status_label.setVisible(False)
        main_layout.addWidget(self.status_label)

        # Scroll Area with Apps List
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )

        self.apps_container = QWidget()
        self.apps_container.setStyleSheet("background: transparent;")
        self.apps_layout = QVBoxLayout(self.apps_container)
        self.apps_layout.setContentsMargins(0, 0, 0, 0)
        self.apps_layout.setSpacing(12)
        self.scroll_area.setWidget(self.apps_container)

        main_layout.addWidget(self.scroll_area, 1)

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.quit()
            self.worker.wait(1000)
        super().closeEvent(event)

    def _style_filter_button(self, btn: QPushButton, is_active: bool) -> None:
        if is_active:
            btn.setStyleSheet(
                """
                QPushButton {
                    background: #176c67;
                    color: #ffffff;
                    border: 1px solid #35c9bd;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: bold;
                    font-size: 12px;
                }
                """
            )
        else:
            btn.setStyleSheet(
                """
                QPushButton {
                    background: rgba(255, 255, 255, 0.05);
                    color: #71858b;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    color: #e4ecee;
                    border-color: #35c9bd;
                }
                """
            )

    def _set_filter(self, key: str) -> None:
        self._active_filter = key
        for k, btn in self.filter_buttons.items():
            btn.setChecked(k == key)
            self._style_filter_button(btn, is_active=(k == key))
        self._render_apps()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self._render_apps()

    def _on_category_changed(self) -> None:
        self._category_filter = self.cat_combo.currentText()
        self._render_apps()

    def _open_repo_settings(self) -> None:
        dlg = RepoSettingsDialog(self.store.repository, self.store.ref, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.store.set_repository(dlg.repository, dlg.ref)
                self.subtitle.setText(f"GitHub: {self.store.owner}/{self.store.repo} @ {self.store.ref}")
                self.refresh_catalog(force_remote=True)
            except AppStoreError as err:
                QMessageBox.critical(self, "Ungültige Quelle", err.user_message)

    def refresh_catalog(self, force_remote: bool = False) -> None:
        self.btn_refresh.setEnabled(False)
        self.status_label.setText("Lade Store-Katalog von GitHub..." if force_remote else "Lade Store-Katalog...")
        self.status_label.setVisible(True)

        def do_fetch() -> list[CatalogApp]:
            return self.store.fetch_catalog(force_remote=force_remote)

        self.worker = AppStoreWorker(do_fetch)
        self.worker.completed.connect(self._on_catalog_loaded)
        self.worker.failed.connect(self._on_catalog_error)
        self.worker.start()

    def _on_catalog_loaded(self, apps: list[CatalogApp]) -> None:
        self.btn_refresh.setEnabled(True)
        self.status_label.setText(f"Katalog erfolgreich geladen ({len(apps)} Apps verfügbar).")
        self.status_label.setVisible(True)

        # Update categories
        cats = sorted({app.category for app in apps if app.category})
        self.cat_combo.blockSignals(True)
        cur_cat = self.cat_combo.currentText()
        self.cat_combo.clear()
        self.cat_combo.addItem("Alle Kategorien")
        for c in cats:
            self.cat_combo.addItem(c)
        idx = self.cat_combo.findText(cur_cat)
        if idx >= 0:
            self.cat_combo.setCurrentIndex(idx)
        self.cat_combo.blockSignals(False)

        self._render_apps()

    def _on_catalog_error(self, message: str) -> None:
        self.btn_refresh.setEnabled(True)
        self.status_label.setText(f"Hinweis: {message}")
        self.status_label.setVisible(True)
        # Even if remote failed, attempt rendering cached or local apps
        self._render_apps()

    def _render_apps(self) -> None:
        # Clear layout
        while self.apps_layout.count():
            item = self.apps_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        catalog_apps = self.store.list_catalog()
        installed_apps = {inst.id: inst for inst in self.store.list_installed()}

        count = 0
        for app in catalog_apps:
            installed = installed_apps.get(app.id)

            # Filter by search
            if self._search_text:
                haystack = f"{app.name} {app.id} {app.summary} {app.description} {app.category}".lower()
                if self._search_text not in haystack:
                    continue

            # Filter by category
            if self._category_filter != "Alle Kategorien" and app.category != self._category_filter:
                continue

            # Filter by tab
            is_inst = installed is not None
            has_upd = False
            if is_inst:
                try:
                    from appstore.core import compare_versions

                    has_upd = compare_versions(app.version, installed.version) > 0
                except Exception:
                    has_upd = app.version != installed.version

            if self._active_filter == "installed" and not is_inst:
                continue
            if self._active_filter == "available" and is_inst:
                continue
            if self._active_filter == "updates" and not has_upd:
                continue

            # Render card
            card = AppCard(app, installed, self.apps_container)
            card.open_requested.connect(self._launch_app)
            card.install_requested.connect(self._install_app)
            card.uninstall_requested.connect(self._uninstall_app)
            card.shortcut_requested.connect(self._create_desktop_shortcut)
            card.details_requested.connect(lambda aid: self._show_details(aid, app, installed))
            self.apps_layout.addWidget(card)
            count += 1

        if count == 0:
            empty_lbl = QLabel("Keine passenden Anwendungen gefunden.")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("color: #71858b; font-size: 15px; padding: 40px;")
            self.apps_layout.addWidget(empty_lbl)

        self.apps_layout.addStretch(1)

    def _show_details(self, app_id: str, app: CatalogApp, installed: InstalledApp | None) -> None:
        dlg = AppDetailDialog(app, installed, self)
        dlg.exec()

    def _launch_app(self, app_id: str) -> None:
        try:
            window = self.store.load_app_window(app_id)
            self.launch_app_requested.emit(window)
            window.show()
            window.raise_()
        except AppStoreError as err:
            QMessageBox.critical(self, "Fehler beim Starten", err.user_message)

    def _install_app(self, app_id: str) -> None:
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText(f"Installation von '{app_id}' gestartet...")
        self.status_label.setVisible(True)

        def progress_cb(cur: int, total: int, msg: str) -> None:
            if self.worker:
                pct = int((cur / max(1, total)) * 100)
                self.worker.progress.emit(cur, total, msg)

        def do_install() -> InstalledApp:
            return self.store.install_app(app_id, progress_callback=progress_cb)

        self.worker = AppStoreWorker(do_install)
        self.worker.progress.connect(self._on_install_progress)
        self.worker.completed.connect(lambda result: self._on_install_completed(app_id, result))
        self.worker.failed.connect(self._on_install_failed)
        self.worker.start()

    def _on_install_progress(self, cur: int, total: int, msg: str) -> None:
        pct = int((cur / max(1, total)) * 100)
        self.progress_bar.setValue(pct)
        self.status_label.setText(msg)

    def _on_install_completed(self, app_id: str, app: InstalledApp) -> None:
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"'{app.name}' erfolgreich installiert!")
        self.app_installed.emit(app_id)
        self._render_apps()

    def _on_install_failed(self, error: str) -> None:
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Installation fehlgeschlagen: {error}")
        QMessageBox.critical(self, "Installationsfehler", error)

    def _uninstall_app(self, app_id: str) -> None:
        app = self.store.get_catalog_app(app_id)
        name = app.name if app else app_id
        reply = QMessageBox.question(
            self,
            "App deinstallieren",
            f"Möchtest du '{name}' wirklich deinstallieren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self.store.uninstall_app(app_id):
                self.status_label.setText(f"'{name}' wurde erfolgreich deinstalliert.")
                self.status_label.setVisible(True)
                self.app_uninstalled.emit(app_id)
                self._render_apps()
            else:
                QMessageBox.warning(self, "Fehler", f"'{name}' konnte nicht entfernt werden.")

    def _create_desktop_shortcut(self, app_id: str) -> None:
        from apps.file_manager.shortcuts import create_desktop_shortcut

        app = self.store.get_catalog_app(app_id)
        name = app.name if app else app_id
        try:
            path = create_desktop_shortcut(self.desktop_dir, name, app_id=f"store:{app_id}")
            QMessageBox.information(
                self,
                "Verknüpfung erstellt",
                f"Eine Verknüpfung für '{name}' wurde auf dem Schreibtisch erstellt:\n{path.name}",
            )
        except Exception as error:
            QMessageBox.critical(self, "Fehler beim Erstellen", str(error))
