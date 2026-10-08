from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TerminalWindow(QMainWindow):
    def __init__(self, working_directory: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Terminal")
        self.setMinimumSize(440, 220)
        self.resize(560, 280)
        self.working_directory = (working_directory or Path.home()).absolute()
        self.process: QProcess | None = None

        content = QWidget()
        layout = QVBoxLayout(content)
        description = QLabel(
            "Öffne ein echtes Linux-Terminal mit deiner Benutzer-Shell. "
            "Befehle laufen lokal auf diesem Raspberry Pi."
        )
        description.setWordWrap(True)
        self.directory_label = QLabel()
        self.directory_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        controls = QHBoxLayout()
        self.choose_button = QPushButton("Arbeitsordner wählen…")
        self.choose_button.clicked.connect(self.choose_directory)
        self.launch_button = QPushButton("Terminal öffnen")
        self.launch_button.clicked.connect(self.launch_terminal)
        controls.addWidget(self.choose_button)
        controls.addStretch(1)
        controls.addWidget(self.launch_button)

        layout.addWidget(description)
        layout.addWidget(self.directory_label)
        layout.addStretch(1)
        layout.addLayout(controls)
        self.setCentralWidget(content)
        self._update_directory_label()

    @staticmethod
    def terminal_command(working_directory: Path) -> tuple[str, list[str]]:
        return "qterminal", ["--workdir", str(working_directory)]

    def choose_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Arbeitsordner wählen", str(self.working_directory)
        )
        if directory:
            self.working_directory = Path(directory).absolute()
            self._update_directory_label()

    def launch_terminal(self) -> None:
        program, arguments = self.terminal_command(self.working_directory)
        process = QProcess(QApplication.instance())
        process.setWorkingDirectory(str(self.working_directory))
        process.errorOccurred.connect(self._show_start_error)
        process.finished.connect(self._terminal_finished)
        self.launch_button.setEnabled(False)
        process.start(program, arguments)
        self.process = process

    def _update_directory_label(self) -> None:
        self.directory_label.setText(f"Arbeitsordner: {self.working_directory}")

    def _show_start_error(self, _error: QProcess.ProcessError) -> None:
        self.launch_button.setEnabled(True)
        message = self.process.errorString() if self.process else "Unbekannter Fehler"
        QMessageBox.critical(
            self,
            "Terminal kann nicht gestartet werden",
            f"qterminal konnte nicht gestartet werden.\n\n{message}",
        )

    def _terminal_finished(self, exit_code: int, _exit_status) -> None:
        self.launch_button.setEnabled(True)
        if exit_code != 0:
            QMessageBox.warning(
                self,
                "Terminal beendet",
                f"qterminal wurde mit Fehlercode {exit_code} beendet.",
            )
