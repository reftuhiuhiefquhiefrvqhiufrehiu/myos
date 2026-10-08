from pathlib import Path

from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
)


class TextEditorWindow(QMainWindow):
    def __init__(self, file_path: Path | None = None) -> None:
        super().__init__()
        self.setMinimumSize(440, 320)
        self.resize(680, 500)
        self.file_path: Path | None = None
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Text hier eingeben …")
        self.editor.document().modificationChanged.connect(self._update_title)
        self.setCentralWidget(self.editor)
        self._create_actions()
        if file_path is not None:
            self.load_file(file_path)
        else:
            self._update_title()

    def _create_actions(self) -> None:
        file_menu = self.menuBar().addMenu("Datei")
        self.new_action = QAction("Neu", self)
        self.new_action.setShortcut("Ctrl+N")
        self.new_action.triggered.connect(self.new_file)
        file_menu.addAction(self.new_action)

        self.open_action = QAction("Öffnen…", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self.open_dialog)
        file_menu.addAction(self.open_action)

        self.save_action = QAction("Speichern", self)
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.triggered.connect(self.save)
        file_menu.addAction(self.save_action)

        self.save_as_action = QAction("Speichern unter…", self)
        self.save_as_action.setShortcut("Ctrl+Shift+S")
        self.save_as_action.triggered.connect(self.save_as)
        file_menu.addAction(self.save_as_action)

    def load_file(self, file_path: Path) -> None:
        path = file_path.expanduser().absolute()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            QMessageBox.critical(
                self,
                "Datei kann nicht geöffnet werden",
                f"{path}\n\n{error}",
            )
            return
        self.file_path = path
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self._update_title()

    def save_file(self, file_path: Path) -> None:
        path = file_path.expanduser().absolute()
        try:
            path.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(
                self,
                "Datei kann nicht gespeichert werden",
                f"{path}\n\n{error}",
            )
            return
        self.file_path = path
        self.editor.document().setModified(False)
        self._update_title()

    def new_file(self) -> None:
        if not self._confirm_discard():
            return
        self.file_path = None
        self.editor.clear()
        self.editor.document().setModified(False)
        self._update_title()

    def open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, "Textdatei öffnen", str(Path.home()), "Textdateien (*)"
        )
        if path:
            self.load_file(Path(path))

    def save(self) -> None:
        if self.file_path is None:
            self.save_as()
        else:
            self.save_file(self.file_path)

    def save_as(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Textdatei speichern",
            str(self.file_path or Path.home() / "Unbenannt.txt"),
            "Textdateien (*.txt);;Alle Dateien (*)",
        )
        if path:
            self.save_file(Path(path))

    def _confirm_discard(self) -> bool:
        if not self.editor.document().isModified():
            return True
        answer = QMessageBox.question(
            self,
            "Ungespeicherte Änderungen",
            "Änderungen speichern, bevor du fortfährst?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            self.save()
            return not self.editor.document().isModified()
        return answer == QMessageBox.StandardButton.Discard

    def _update_title(self, _modified: bool | None = None) -> None:
        name = self.file_path.name if self.file_path else "Unbenannt"
        marker = " *" if self.editor.document().isModified() else ""
        self.setWindowTitle(f"{name}{marker} — Texteditor")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()
