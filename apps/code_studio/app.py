import html
import json
import re
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QProcess, QRegularExpression, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView


LANGUAGE_BY_SUFFIX = {
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TSX",
    ".json": "JSON",
    ".md": "Markdown",
    ".txt": "Text",
    ".py": "Python",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".svg": "SVG",
}


def language_for_path(file_path: str | Path) -> str:
    suffix = Path(file_path).suffix.lower()
    return LANGUAGE_BY_SUFFIX.get(suffix, "Text")


class LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(self.editor.line_number_area_size(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_line_numbers(event)


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self.language = "Text"
        self.rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._build_rules()

    def set_language(self, language: str) -> None:
        self.language = language
        self._build_rules()
        self.rehighlight()

    def _build_rules(self) -> None:
        self.rules = []
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#146f78"))
        keyword_format.setFontWeight(QFont.Weight.Bold)
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#9b572b"))
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#71858b"))
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#7657a5"))

        if self.language in {"HTML", "SVG"}:
            patterns = [
                (r"</?[A-Za-z][A-Za-z0-9:-]*", keyword_format),
                (r"[A-Za-z_:][-A-Za-z0-9_:.]*(?=\s*=)", keyword_format),
                (r'"[^"]*"|\'[^\']*\'', string_format),
                (r"<!--.*-->", comment_format),
            ]
        elif self.language in {"CSS"}:
            patterns = [
                (r"[.#]?[A-Za-z_][A-Za-z0-9_-]*(?=\s*\{)", keyword_format),
                (r'"[^"]*"|\'[^\']*\'', string_format),
                (r"/\*.*\*/", comment_format),
                (r"#[0-9a-fA-F]{3,8}\b", number_format),
            ]
        elif self.language in {"JavaScript", "TypeScript", "TSX", "Python"}:
            patterns = [
                (
                    r"\b(const|let|var|function|return|if|else|for|while|class|new|"
                    r"import|from|export|default|async|await|try|catch|throw|"
                    r"def|self|true|false|null|None)\b",
                    keyword_format,
                ),
                (r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`', string_format),
                (r"//.*|#.*", comment_format),
                (r"\b\d+(?:\.\d+)?\b", number_format),
            ]
        elif self.language == "JSON":
            patterns = [
                (r'"(?:\\.|[^"\\])*"(?=\s*:)', keyword_format),
                (r'"(?:\\.|[^"\\])*"', string_format),
                (r"\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?\b", number_format),
            ]
        else:
            patterns = []
        self.rules = [
            (QRegularExpression(pattern), text_format)
            for pattern, text_format in patterns
        ]

    def highlightBlock(self, text: str) -> None:
        for expression, text_format in self.rules:
            matches = expression.globalMatch(text)
            while matches.hasNext():
                match = matches.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), text_format)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self.highlighter = CodeHighlighter(self.document())
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_area_width()
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.setStyleSheet(
            "QPlainTextEdit { background: #f7fafb; color: #183840; "
            "selection-background-color: #b8d9dc; border: 1px solid #a8bec2; }"
        )

    def line_number_area_size(self):
        digits = len(str(max(1, self.blockCount())))
        return self.fontMetrics().horizontalAdvance("9") * digits + 14

    def _update_line_number_area_width(self, _count: int = 0) -> None:
        self.setViewportMargins(self.line_number_area_size(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.line_number_area.setGeometry(
            rect.left(),
            rect.top(),
            self.line_number_area_size(),
            rect.height(),
        )

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#e9f0f2"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor("#58747c"))
                painter.drawText(
                    0,
                    top,
                    self.line_number_area.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1


class CodeStudioWindow(QMainWindow):
    open_local_url_requested = Signal(str)

    def __init__(self, file_path: str | Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Code Studio")
        self.setMinimumSize(560, 360)
        self.resize(960, 620)
        self.file_path: Path | None = None
        self.project_process: QProcess | None = None
        self.project_directory: Path | None = None
        self.local_url = ""

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        file_toolbar = QHBoxLayout()
        self.new_button = QPushButton("Neu")
        self.open_button = QPushButton("Öffnen…")
        self.save_button = QPushButton("Speichern")
        self.preview_button = QPushButton("Vorschau")
        self.preview_format = QComboBox()
        self.preview_format.addItems(["Automatisch", "HTML", "CSS", "JavaScript"])
        for widget in (
            self.new_button,
            self.open_button,
            self.save_button,
            self.preview_button,
            self.preview_format,
        ):
            file_toolbar.addWidget(widget)
        file_toolbar.addStretch(1)
        layout.addLayout(file_toolbar)

        run_toolbar = QHBoxLayout()
        self.run_button = QPushButton("JavaScript ausführen")
        self.project_button = QPushButton("Projekt starten (npm run dev)")
        self.stop_button = QPushButton("Server stoppen")
        self.open_browser_button = QPushButton("Lokale Seite öffnen")
        self.stop_button.setEnabled(False)
        self.open_browser_button.setEnabled(False)
        run_toolbar.addWidget(self.run_button)
        run_toolbar.addWidget(self.project_button)
        run_toolbar.addWidget(self.stop_button)
        run_toolbar.addWidget(self.open_browser_button)
        run_toolbar.addStretch(1)
        layout.addLayout(run_toolbar)

        self.editor = CodeEditor()
        self.editor.setPlaceholderText("HTML, CSS, JavaScript oder Markdown hier eingeben …")
        self.editor.textChanged.connect(self._update_status)

        self.preview_view = QWebEngineView()
        self.preview_view.setHtml(
            "<html><body style='font-family: sans-serif; color: #123; background: #f7f9fa; padding: 2rem;'>"
            "Öffne eine Datei oder schreibe Code. Die Vorschau startet erst nach Klick auf „Vorschau“."
            "</body></html>"
        )
        self.preview_status = QLabel(
            "Quelltext bleibt zunächst sicher. Vorschau und Code-Ausführung starten erst nach deiner Bestätigung."
        )
        self.preview_status.setWordWrap(True)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.addWidget(self.editor)
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.addWidget(self.preview_view, 1)
        preview_layout.addWidget(self.preview_status)
        splitter.addWidget(editor_widget)
        splitter.addWidget(preview_widget)
        splitter.setSizes([440, 520])
        layout.addWidget(splitter, 1)

        self.new_button.clicked.connect(self.new_file)
        self.open_button.clicked.connect(self.open_dialog)
        self.save_button.clicked.connect(self.save_file)
        self.preview_button.clicked.connect(self.preview_current_file)
        self.run_button.clicked.connect(self.run_javascript_file)
        self.project_button.clicked.connect(self.run_existing_project)
        self.stop_button.clicked.connect(self.stop_project)
        self.open_browser_button.clicked.connect(
            lambda: self.open_local_url_requested.emit(self.local_url)
        )

        self.status_label = QLabel("Bereit")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.setCentralWidget(container)

        if file_path is not None:
            self.load_file(file_path)
        else:
            self._update_status()

    @staticmethod
    def _safe_preview_html(title: str, payload: str) -> str:
        return (
            "<!doctype html><html lang='de'><head><meta charset='utf-8'><title>"
            f"{html.escape(title)}</title>"
            "<style>body { font-family: sans-serif; margin: 1.25rem; background: #f5f7f8; color: #15313b; } "
            "pre { white-space: pre-wrap; word-break: break-word; } "
            "code { font-family: ui-monospace, monospace; }</style></head><body>"
            f"{payload}</body></html>"
        )

    @staticmethod
    def _js_string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")

    @staticmethod
    def _wrap_script_preview(title: str, source: str) -> str:
        encoded_source = CodeStudioWindow._js_string(source)
        safe_title = html.escape(title)
        script = (
            "(() => {"
            f"const source = {encoded_source};"
            "const output = document.getElementById('output');"
            "const safeConsole = {"
            "log: (...values) => { output.textContent += values.map(String).join(' ') + '\\n'; },"
            "info: (...values) => { output.textContent += values.map(String).join(' ') + '\\n'; },"
            "error: (...values) => { output.textContent += values.map(String).join(' ') + '\\n'; }"
            "};"
            "try { new Function('console', source)(safeConsole); }"
            "catch (error) { output.textContent += error.stack || String(error); }"
            "})();"
        )
        return (
            "<!doctype html><html lang='de'><head><meta charset='utf-8'><title>"
            f"{safe_title}</title><style>body {{ font-family: sans-serif; background: #f3f7f8; "
            "color: #123; padding: 1rem; }} pre { white-space: pre-wrap; }</style></head>"
            "<body><h2>JavaScript-Ausgabe</h2><pre id='output'></pre>"
            f"<script>{script}</script></body></html>"
        )

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _update_status(self, _value: object | None = None) -> None:
        name = self.file_path.name if self.file_path else "Unbenannt"
        dirty = " *" if self.editor.document().isModified() else ""
        self.setWindowTitle(f"{name}{dirty} — Code Studio")
        label = language_for_path(self.file_path or "unsaved.txt")
        state = "geändert" if self.editor.document().isModified() else "bereit"
        self.status_label.setText(f"{name} · {label} · {state}")

    def new_file(self) -> None:
        self.file_path = None
        self.editor.clear()
        self.editor.document().setModified(False)
        self._set_editor_language("Text")
        self._show_source_only("Leeres Dokument. Die Vorschau startet erst nach deiner Auswahl.")
        self._update_status()

    def open_dialog(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Datei öffnen",
            str(Path.home()),
            "Quelltext (*.html *.htm *.css *.js *.mjs *.jsx *.ts *.tsx *.json *.md *.txt *.py *.yaml *.yml *.svg);;Alle Dateien (*)",
        )
        if path:
            self.load_file(Path(path))

    def load_file(self, file_path: str | Path) -> bool:
        path = Path(file_path).expanduser().absolute()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            QMessageBox.critical(
                self,
                "Datei kann nicht geöffnet werden",
                f"{path}\n\n{error}",
            )
            return False
        self.file_path = path
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self._set_editor_language(language_for_path(path))
        self._show_source_only(
            f"Quelltext geladen: {path.name}. Klicke auf „Vorschau“, um die Datei bewusst zu rendern."
        )
        self._update_status()
        return True

    def save_file(self) -> bool:
        path = self.file_path
        if path is None:
            selected_path, _filter = QFileDialog.getSaveFileName(
                self,
                "Datei speichern",
                str(Path.home() / "neonveil-datei.html"),
                "HTML (*.html *.htm);;CSS (*.css);;JavaScript (*.js *.mjs);;TypeScript (*.ts *.tsx);;JSON (*.json);;Markdown (*.md);;Text (*.txt);;Alle Dateien (*)",
            )
            if not selected_path:
                return False
            path = Path(selected_path).expanduser().absolute()
        try:
            path.write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(
                self,
                "Datei kann nicht gespeichert werden",
                f"{path}\n\n{error}",
            )
            return False
        self.file_path = path
        self.editor.document().setModified(False)
        self._set_editor_language(language_for_path(path))
        self._update_status()
        self.preview_status.setText(
            "Datei gespeichert. Die Vorschau startet nur nach einem eigenen Klick auf „Vorschau“."
        )
        return True

    def _set_editor_language(self, language: str) -> None:
        self.editor.highlighter.set_language(language)

    def _selected_preview_language(self) -> str:
        choice = self.preview_format.currentText()
        if choice != "Automatisch":
            return choice
        suffix = self.file_path.suffix.lower() if self.file_path else ""
        return {
            ".html": "HTML",
            ".htm": "HTML",
            ".css": "CSS",
            ".js": "JavaScript",
            ".mjs": "JavaScript",
        }.get(suffix, "Text")

    def _show_source_only(self, message: str) -> None:
        source = html.escape(self.editor.toPlainText())
        title = self.file_path.name if self.file_path else "Quelltext"
        self.preview_view.setHtml(
            self._safe_preview_html(title, f"<h2>Quelltext (nicht ausgeführt)</h2><pre>{source}</pre>")
        )
        self.preview_status.setText(message)

    @staticmethod
    def _confirm_runtime_action(title: str, question: str) -> bool:
        return (
            QMessageBox.question(
                None,
                title,
                question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def preview_current_file(self) -> bool:
        source = self.editor.toPlainText()
        language = self._selected_preview_language()
        title = self.file_path.name if self.file_path else "Unbenannt"
        base_url = (
            QUrl.fromLocalFile(str(self.file_path.parent) + "/")
            if self.file_path
            else QUrl()
        )
        if language == "HTML":
            confirmed = self._confirm_runtime_action(
                "HTML-Vorschau bestätigen",
                "HTML kann Skripte ausführen sowie lokale Dateien oder Netzwerkadressen referenzieren. "
                "Nur Vorschau für eigenen oder vertrauenswürdigen Quelltext starten?",
            )
            if not confirmed:
                self._show_source_only("Vorschau abgelehnt. Der Quelltext wurde nicht ausgeführt.")
                return False
            self.preview_view.setHtml(source, base_url)
            self.preview_status.setText(
                f"HTML-Vorschau aktiv: {title}. Eingebundene externe Ressourcen benötigen Netzwerkzugriff."
            )
            return True
        if language == "JavaScript":
            confirmed = self._confirm_runtime_action(
                "JavaScript-Vorschau bestätigen",
                "Dieses JavaScript wird jetzt im Vorschaufenster ausgeführt und kann auf Netzwerkfunktionen zugreifen. "
                "Nur eigenen oder vertrauenswürdigen Quelltext starten?",
            )
            if not confirmed:
                self._show_source_only("Vorschau abgelehnt. Der Quelltext wurde nicht ausgeführt.")
                return False
            self.preview_view.setHtml(self._wrap_script_preview(title, source), base_url)
            self.preview_status.setText(
                "JavaScript wurde nach deiner Bestätigung im Browser-Vorschaufenster ausgeführt. Netzwerkzugriff ist möglich."
            )
            return True
        if language == "CSS":
            css = re.sub(r"</style", r"<\\/style", source, flags=re.IGNORECASE)
            document = self._safe_preview_html(
                title,
                "<h2>CSS-Vorschau</h2><div class='sample'>"
                "Gestalte diesen Beispieltext mit deinem CSS.</div>",
            ).replace(
                "</head>",
                f"<style>{css}</style></head>",
            )
            self.preview_view.setHtml(document, base_url)
            self.preview_status.setText(
                "CSS-Vorschau lokal. CSS kann referenzierte Bilder oder Schriften aus dem Netzwerk laden."
            )
            return True
        self._show_source_only(
            f"{language_for_path(self.file_path or 'unsaved.txt')}: keine ausführbare Vorschau für dieses Format."
        )
        return False

    def run_javascript_file(self) -> None:
        path = self.file_path
        if path is None or path.suffix.lower() not in {".js", ".mjs"}:
            self._set_status("Speichere zuerst eine JavaScript-Datei (.js oder .mjs).")
            return
        if self.editor.document().isModified():
            if not self.save_file():
                self._set_status("Nicht ausgeführt: Änderungen konnten nicht gespeichert werden.")
                return
            path = self.file_path
        if path is None:
            self._set_status("JavaScript-Datei fehlt.")
            return
        node_path = shutil.which("node") or shutil.which("nodejs")
        if node_path is None:
            self._set_status("Node.js fehlt. Im Basisbild wird nodejs aus Debian installiert.")
            return
        if not self._confirm_runtime_action(
            "JavaScript ausführen",
            f"Möchtest du „{path.name}“ lokal mit Node.js ausführen? Das Skript erhält deine Benutzerrechte.",
        ):
            self._set_status("Ausführen abgebrochen.")
            return
        try:
            result = subprocess.run(
                [node_path, str(path)],
                cwd=str(path.parent),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.preview_status.setText(f"JavaScript-Ausführung fehlgeschlagen: {error}")
            self._set_status(f"Fehler: {error}")
            return
        output = (
            f"<h3>Exit-Code: {result.returncode}</h3>"
            f"<h4>Ausgabe</h4><pre>{html.escape(result.stdout or '(keine Ausgabe)')}</pre>"
            f"<h4>Fehlerausgabe</h4><pre>{html.escape(result.stderr or '(keine Fehlerausgabe)')}</pre>"
        )
        self.preview_view.setHtml(self._safe_preview_html(path.name, output))
        self._set_status(f"JavaScript beendet · Exit-Code {result.returncode}")

    def run_existing_project(self) -> None:
        base_dir = self.file_path.parent if self.file_path else Path.home()
        directory = QFileDialog.getExistingDirectory(
            self,
            "Projektordner wählen",
            str(base_dir),
        )
        if not directory:
            self._set_status("Projektstart abgebrochen: kein Ordner gewählt.")
            return
        project_dir = Path(directory).expanduser().absolute()
        package_json = project_dir / "package.json"
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "Kein Projekt gefunden",
                "In diesem Ordner gibt es keine package.json.",
            )
            return
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "Projektdatei ungültig", f"package.json kann nicht gelesen werden:\n{error}")
            return
        if not isinstance(package, dict) or not isinstance(package.get("scripts"), dict) or "dev" not in package["scripts"]:
            QMessageBox.warning(
                self,
                "Kein Startskript gefunden",
                "package.json enthält kein „dev“-Skript. Lege eines an, zum Beispiel „next dev“ für Next.js.",
            )
            return
        npm_path = shutil.which("npm")
        if npm_path is None:
            self._set_status("npm fehlt. Das Paket npm wird im neonveil-Basisbild bereitgestellt.")
            return
        if not self._confirm_runtime_action(
            "Projekt starten",
            f"Möchtest du in „{project_dir}“ „npm run dev“ starten?\n\n"
            + (
                "Der Ordner node_modules fehlt. Führe zuerst „npm install“ mit Internetverbindung aus.\n\n"
                if not (project_dir / "node_modules").is_dir()
                else ""
            )
            + "Next.js- und TypeScript-Abhängigkeiten müssen normalerweise zuerst mit npm install "
            "aus dem Internet installiert werden. Ein vorhandenes Projekt kann danach lokal starten.",
        ):
            self._set_status("Projektstart abgebrochen.")
            return
        if self.project_process is not None and self.project_process.state() != QProcess.ProcessState.NotRunning:
            self._set_status("Ein Projektserver läuft bereits. Stoppe ihn zuerst.")
            return
        self.project_directory = project_dir
        self.local_url = ""
        self.open_browser_button.setEnabled(False)
        self.project_process = QProcess(self)
        self.project_process.setWorkingDirectory(str(project_dir))
        self.project_process.setProgram(npm_path)
        self.project_process.setArguments(["run", "dev"])
        self.project_process.readyReadStandardOutput.connect(self._read_project_output)
        self.project_process.readyReadStandardError.connect(self._read_project_output)
        self.project_process.finished.connect(self._project_finished)
        self.project_process.errorOccurred.connect(self._project_error)
        self.project_process.start()
        self.stop_button.setEnabled(True)
        self._set_status(f"Projekt wird gestartet: {project_dir}")
        self.preview_status.setText(
            "Projektserver wird gestartet. URL und Fehlermeldungen erscheinen hier."
        )

    def stop_project(self) -> None:
        if self.project_process is None or self.project_process.state() == QProcess.ProcessState.NotRunning:
            self._set_status("Es läuft kein Projektserver.")
            self.stop_button.setEnabled(False)
            return
        self.project_process.terminate()
        self._set_status("Server wird beendet …")
        QTimer.singleShot(3000, self._kill_project_if_running)

    def _kill_project_if_running(self) -> None:
        if self.project_process is not None and self.project_process.state() != QProcess.ProcessState.NotRunning:
            self.project_process.kill()

    def _read_project_output(self) -> None:
        if self.project_process is None:
            return
        stdout = self.project_process.readAllStandardOutput().data().decode(
            "utf-8", errors="replace"
        )
        stderr = self.project_process.readAllStandardError().data().decode(
            "utf-8", errors="replace"
        )
        output = (stdout + stderr).strip()
        if output:
            self.preview_status.setText(html.escape(output))
            self._set_status(output[-220:])
            match = re.search(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/[^\s]*)?", output)
            if match is not None:
                self.local_url = match.group(0).rstrip(".,;)")
                self.open_browser_button.setEnabled(True)
                self.preview_status.setText(
                    f"{html.escape(output)}\n\nLokale Seite erkannt: {html.escape(self.local_url)}"
                )

    def _project_error(self, error: QProcess.ProcessError) -> None:
        self.stop_button.setEnabled(False)
        self._set_status(f"Projekt konnte nicht gestartet werden: {error}")
        self.preview_status.setText(
            "Projektstart fehlgeschlagen. Prüfe npm, package.json und die lokalen Abhängigkeiten."
        )

    def _project_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self.stop_button.setEnabled(False)
        self._set_status(f"Projekt beendet mit Exit-Code {exit_code}.")
        self.preview_status.setText(f"Projekt beendet mit Exit-Code {exit_code}.")
