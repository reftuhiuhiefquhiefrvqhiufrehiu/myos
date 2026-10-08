import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from apps.code_studio.app import (
    CodeStudioWindow,
    LANGUAGE_BY_SUFFIX,
    language_for_path,
)


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class FakeProcess:
    def __init__(self, _parent=None) -> None:
        self.readyReadStandardOutput = FakeSignal()
        self.readyReadStandardError = FakeSignal()
        self.finished = FakeSignal()
        self.errorOccurred = FakeSignal()
        self.directory = ""
        self.program = ""
        self.arguments = []
        self.started = False

    def setWorkingDirectory(self, directory: str) -> None:
        self.directory = directory

    def setProgram(self, program: str) -> None:
        self.program = program

    def setArguments(self, arguments: list[str]) -> None:
        self.arguments = arguments

    def start(self) -> None:
        self.started = True


class CodeStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def make_window(self, path: Path | None = None) -> CodeStudioWindow:
        window = CodeStudioWindow(path)
        self.addCleanup(self.close_window, window)
        return window

    def close_window(self, window: CodeStudioWindow) -> None:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_language_map_covers_common_web_and_project_files(self) -> None:
        expected = {
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
        }
        for suffix, language in expected.items():
            with self.subTest(suffix=suffix):
                self.assertEqual(language_for_path(f"project/file{suffix}"), language)
                self.assertEqual(LANGUAGE_BY_SUFFIX[suffix], language)
        self.assertEqual(language_for_path("unknown.bin"), "Text")

    def test_opening_html_shows_escaped_source_without_preview_execution(self) -> None:
        path = self.root / "index.html"
        source = "<script>window.didRun = true;</script><h1>Hello</h1>"
        path.write_text(source, encoding="utf-8")
        with patch.object(
            CodeStudioWindow, "_confirm_runtime_action", return_value=True
        ) as confirm:
            window = self.make_window(path)
        confirm.assert_not_called()
        self.assertEqual(window.editor.toPlainText(), source)
        self.assertEqual(window.file_path, path)
        self.assertIn("Quelltext geladen", window.preview_status.text())
        self.assertFalse(window.editor.document().isModified())

    def test_save_updates_file_but_does_not_automatically_run_preview(self) -> None:
        path = self.root / "page.html"
        path.write_text("<h1>old</h1>", encoding="utf-8")
        window = self.make_window(path)
        window.editor.setPlainText("<script>window.changed = true;</script>")
        with patch.object(
            CodeStudioWindow, "_confirm_runtime_action", return_value=True
        ) as confirm:
            self.assertTrue(window.save_file())

        self.assertEqual(path.read_text(encoding="utf-8"), window.editor.toPlainText())
        self.assertFalse(window.editor.document().isModified())
        confirm.assert_not_called()
        self.assertIn("Vorschau startet nur", window.preview_status.text())

    def test_save_as_creates_a_new_source_file_without_running_it(self) -> None:
        path = self.root / "new script.js"
        window = self.make_window()
        source = "console.log('not run during save')"
        window.editor.setPlainText(source)
        with patch(
            "apps.code_studio.app.QFileDialog.getSaveFileName",
            return_value=(str(path), "JavaScript (*.js *.mjs)"),
        ), patch.object(
            CodeStudioWindow, "_confirm_runtime_action"
        ) as confirm:
            self.assertTrue(window.save_file())

        self.assertEqual(path.read_text(encoding="utf-8"), source)
        self.assertEqual(window.file_path, path)
        self.assertEqual(language_for_path(window.file_path), "JavaScript")
        confirm.assert_not_called()

    def test_preview_rejection_keeps_html_as_escaped_source(self) -> None:
        path = self.root / "index.html"
        path.write_text("<script>window.bad = true;</script>", encoding="utf-8")
        window = self.make_window(path)
        with patch.object(
            CodeStudioWindow, "_confirm_runtime_action", return_value=False
        ) as confirm:
            self.assertFalse(window.preview_current_file())
        confirm.assert_called_once()
        self.assertIn("abgelehnt", window.preview_status.text().lower())

    def test_accepted_html_preview_requires_confirmation_before_rendering(self) -> None:
        path = self.root / "index.html"
        path.write_text("<script>window.example = true;</script>", encoding="utf-8")
        window = self.make_window(path)
        with patch.object(
            CodeStudioWindow, "_confirm_runtime_action", return_value=True
        ) as confirm, patch.object(window.preview_view, "setHtml") as set_html:
            self.assertTrue(window.preview_current_file())

        confirm.assert_called_once()
        set_html.assert_called_once()
        self.assertIn("Netzwerkzugriff", window.preview_status.text())

    def test_javascript_preview_uses_unsaved_buffer_and_requires_confirmation(self) -> None:
        path = self.root / "sample.js"
        path.write_text("console.log('saved version')", encoding="utf-8")
        window = self.make_window(path)
        current_source = "console.log('unsaved version')"
        window.editor.setPlainText(current_source)
        with patch.object(
            CodeStudioWindow, "_confirm_runtime_action", return_value=True
        ) as confirm, patch.object(window.preview_view, "setHtml") as set_html:
            self.assertTrue(window.preview_current_file())

        confirm.assert_called_once()
        rendered_html = set_html.call_args.args[0]
        self.assertIn("unsaved version", rendered_html)
        self.assertNotIn("console.log('saved version')", rendered_html)

    def test_javascript_preview_encodes_source_and_never_embeds_output_as_markup(self) -> None:
        source = "console.log('</script><img src=x>');"
        preview = CodeStudioWindow._wrap_script_preview("test.js", source)
        self.assertIn("const source =", preview)
        self.assertNotIn("console.log('</script>", preview)
        self.assertIn("output.textContent", preview)

    def test_node_execution_requires_confirmation_and_uses_argument_array(self) -> None:
        path = self.root / "safe script.js"
        path.write_text("process.exit(9)", encoding="utf-8")
        window = self.make_window(path)
        with patch("apps.code_studio.app.shutil.which", return_value="/usr/bin/node"), patch.object(
            CodeStudioWindow, "_confirm_runtime_action", return_value=True
        ), patch("apps.code_studio.app.subprocess.run") as run:
            run.return_value.stdout = "result"
            run.return_value.stderr = ""
            run.return_value.returncode = 0
            window.run_javascript_file()

        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/node", str(path)])
        self.assertEqual(kwargs["cwd"], str(path.parent))
        self.assertNotIn("shell", kwargs)

    def test_node_execution_is_not_started_when_confirmation_is_declined(self) -> None:
        path = self.root / "sample.js"
        path.write_text("throw new Error('test')", encoding="utf-8")
        window = self.make_window(path)
        with patch("apps.code_studio.app.shutil.which", return_value="/usr/bin/node"), patch.object(
            CodeStudioWindow, "_confirm_runtime_action", return_value=False
        ), patch("apps.code_studio.app.subprocess.run") as run:
            window.run_javascript_file()
        run.assert_not_called()
        self.assertIn("abgebrochen", window.status_label.text().lower())

    def test_npm_project_folder_selection_and_launch_are_mocked(self) -> None:
        project_dir = self.root / "web project"
        project_dir.mkdir()
        (project_dir / "package.json").write_text(
            json.dumps({"scripts": {"dev": "next dev"}}),
            encoding="utf-8",
        )
        fake_process = FakeProcess()
        window = self.make_window()
        with patch(
            "apps.code_studio.app.QFileDialog.getExistingDirectory",
            return_value=str(project_dir),
        ), patch("apps.code_studio.app.shutil.which", return_value="/usr/bin/npm"), patch.object(
            CodeStudioWindow, "_confirm_runtime_action", return_value=True
        ), patch("apps.code_studio.app.QProcess", return_value=fake_process):
            window.run_existing_project()

        self.assertTrue(fake_process.started)
        self.assertEqual(fake_process.directory, str(project_dir))
        self.assertEqual(fake_process.program, "/usr/bin/npm")
        self.assertEqual(fake_process.arguments, ["run", "dev"])
        self.assertTrue(window.stop_button.isEnabled())

    def test_npm_project_launch_requires_a_dev_script(self) -> None:
        project_dir = self.root / "not-a-dev-project"
        project_dir.mkdir()
        (project_dir / "package.json").write_text(
            json.dumps({"scripts": {"build": "next build"}}),
            encoding="utf-8",
        )
        window = self.make_window()
        with patch(
            "apps.code_studio.app.QFileDialog.getExistingDirectory",
            return_value=str(project_dir),
        ), patch("apps.code_studio.app.shutil.which", return_value="/usr/bin/npm"), patch.object(
            CodeStudioWindow, "_confirm_runtime_action"
        ) as confirm, patch("apps.code_studio.app.QProcess") as process, patch(
            "apps.code_studio.app.QMessageBox.warning"
        ):
            window.run_existing_project()

        confirm.assert_not_called()
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
