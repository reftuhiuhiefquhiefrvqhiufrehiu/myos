"""Behaviour tests for the interactive desktop canvas.

Covers the desktop interactions a user expects: dragging icons to free
positions, rubber-band selection, multi-selection, keyboard operation and
drag & drop in both directions.
"""

import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QIcon, QMouseEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from desktop.desktop_canvas import (
    CELL_HEIGHT,
    CELL_WIDTH,
    MARGIN,
    DesktopCanvas,
    DesktopEntry,
)
from shell import DesktopShell

LEFT = Qt.MouseButton.LeftButton
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


def make_entry(key: str, label: str, position: QPoint | None = None) -> DesktopEntry:
    return DesktopEntry(key=key, label=label, icon=QIcon(), position=position)


def mouse_event(kind, position: QPoint, button, buttons, modifiers) -> QMouseEvent:
    """Build a mouse event with explicit modifiers.

    ``QTest.mouseMove`` accepts no modifier argument, and the canvas needs the
    control/shift state while dragging, so the events are built directly.
    """
    return QMouseEvent(
        kind, QPointF(position), QPointF(position), button, buttons, modifiers
    )


class DesktopCanvasTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.settings = QSettings("neonveil-test", "desktop-canvas")
        self.settings.clear()
        self.canvas = DesktopCanvas(settings=self.settings)
        self.canvas.resize(900, 700)
        self.canvas.show()

    def tearDown(self) -> None:
        self.canvas.close()
        self.canvas.deleteLater()
        self.app.processEvents()
        self.settings.clear()

    def centre_of(self, entry: DesktopEntry, widget: QWidget | None = None) -> QPoint:
        return (widget or self.canvas).entry_rect(entry).center()

    def press(self, position: QPoint, modifiers=Qt.KeyboardModifier.NoModifier, widget: QWidget | None = None) -> None:
        (widget or self.canvas).mousePressEvent(
            mouse_event(QEvent.Type.MouseButtonPress, position, LEFT, LEFT, modifiers)
        )

    def move(self, position: QPoint, modifiers=Qt.KeyboardModifier.NoModifier, widget: QWidget | None = None) -> None:
        (widget or self.canvas).mouseMoveEvent(
            mouse_event(QEvent.Type.MouseMove, position, LEFT, LEFT, modifiers)
        )

    def release(self, position: QPoint, modifiers=Qt.KeyboardModifier.NoModifier, widget: QWidget | None = None) -> None:
        (widget or self.canvas).mouseReleaseEvent(
            mouse_event(QEvent.Type.MouseButtonRelease, position, LEFT, LEFT, modifiers)
        )

    def drag(
        self,
        start: QPoint,
        end: QPoint,
        modifiers=Qt.KeyboardModifier.NoModifier,
        widget: QWidget | None = None,
    ) -> None:
        between = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)
        self.press(start, modifiers, widget)
        self.move(between, modifiers, widget)
        self.move(end, modifiers, widget)
        self.release(end, modifiers, widget)


class TestDesktopCanvasLayout(DesktopCanvasTestCase):
    def test_new_icons_are_placed_without_overlap(self) -> None:
        entries = [make_entry(f"app:{name}", name) for name in ("a", "b", "c")]
        self.canvas.set_entries(entries)

        rectangles = [self.canvas.entry_rect(entry) for entry in self.canvas.entries]
        for index, first in enumerate(rectangles):
            for second in rectangles[index + 1 :]:
                self.assertFalse(
                    first.intersects(second), "Icons dürfen sich nicht überlappen"
                )
        for rectangle in rectangles:
            self.assertTrue(self.canvas.rect().contains(rectangle))

    def test_positions_survive_a_reload(self) -> None:
        entries = [make_entry("app:files", "Dateien")]
        self.canvas.set_entries(entries)
        entry = self.canvas.entries[0]
        entry.position = QPoint(400, 300)
        self.canvas.save_positions()

        reloaded = DesktopCanvas(settings=self.settings)
        reloaded.set_entries([make_entry("app:files", "Dateien")])
        self.assertEqual(reloaded.entries[0].position, QPoint(400, 300))

    def test_icons_stay_inside_a_smaller_window(self) -> None:
        self.canvas.set_entries([make_entry("app:files", "Dateien")])
        entry = self.canvas.entries[0]
        entry.position = QPoint(800, 600)
        self.canvas.save_positions()
        self.canvas.resize(300, 200)
        self.assertTrue(self.canvas.rect().contains(self.canvas.entry_rect(entry)))

    def test_auto_arrange_snaps_every_icon_to_the_grid(self) -> None:
        entries = [make_entry(f"file:{name}", name) for name in ("a", "b", "c", "d")]
        self.canvas.set_entries(entries)
        for entry in self.canvas.entries:
            entry.position = QPoint(37, 41)
        self.canvas.auto_arrange()

        for entry in self.canvas.entries:
            self.assertTrue(
                (entry.position.x() - MARGIN) % (CELL_WIDTH + 10) == 0
                or entry.position.x() == MARGIN,
                f"{entry.label} sitzt nicht im Raster",
            )
            self.assertTrue((entry.position.y() - MARGIN) % CELL_HEIGHT == 0)

    def test_icons_fill_the_available_columns(self) -> None:
        # Placement happens before the surface has its final size in the shell,
        # so the icons must reflow once the real width is known.
        self.canvas.set_entries([make_entry(f"app:{name}", name) for name in "abcdefghi"])
        used_columns = {entry.position.x() for entry in self.canvas.entries}
        expected = max(
            1, (self.canvas.width() - 2 * MARGIN) // (CELL_WIDTH + 10)
        )
        self.assertEqual(len(used_columns), expected)

    def test_a_moved_icon_keeps_its_place_on_resize(self) -> None:
        entries = [make_entry("app:files", "Dateien"), make_entry("app:code", "Code")]
        self.canvas.set_entries(entries)
        first, second = self.canvas.entries
        self.drag(self.centre_of(first), self.centre_of(first) + QPoint(300, 200))
        moved = QPoint(first.position)
        second_position = QPoint(second.position)

        self.canvas.resize(600, 400)

        self.assertEqual(first.position, moved, "Vom Nutzer verschobenes Icon wandert nicht")
        self.assertNotEqual(second.position, second_position)


class TestDesktopCanvasDragging(DesktopCanvasTestCase):
    def test_dragging_moves_an_icon(self) -> None:
        self.canvas.set_entries([make_entry("app:files", "Dateien")])
        entry = self.canvas.entries[0]
        start = self.centre_of(entry)
        original = QPoint(entry.position)

        self.drag(start, start + QPoint(180, 120))

        self.assertNotEqual(entry.position, original)
        self.assertTrue(self.canvas.rect().contains(self.canvas.entry_rect(entry)))

    def test_dragging_snaps_to_the_grid(self) -> None:
        self.canvas.set_entries([make_entry("app:files", "Dateien")])
        entry = self.canvas.entries[0]
        start = self.centre_of(entry)

        self.drag(start, start + QPoint(70, 35))

        self.assertEqual((entry.position.x() - MARGIN) % (CELL_WIDTH + 10), 0)
        self.assertEqual((entry.position.y() - MARGIN) % CELL_HEIGHT, 0)

    def test_control_drag_places_freely(self) -> None:
        self.canvas.set_entries([make_entry("app:files", "Dateien")])
        entry = self.canvas.entries[0]
        start = self.centre_of(entry)

        self.drag(start, start + QPoint(70, 35), modifiers=CTRL)

        self.assertNotEqual((entry.position.x() - MARGIN) % (CELL_WIDTH + 10), 0)

    def test_dragging_one_icon_keeps_the_others_in_place(self) -> None:
        entries = [make_entry("app:a", "A"), make_entry("app:b", "B")]
        self.canvas.set_entries(entries)
        first, second = self.canvas.entries
        second_position = QPoint(second.position)

        self.drag(self.centre_of(first), self.centre_of(first) + QPoint(150, 0))

        self.assertEqual(second.position, second_position)

    def test_dropped_position_is_persisted(self) -> None:
        self.canvas.set_entries([make_entry("app:files", "Dateien")])
        entry = self.canvas.entries[0]
        start = self.centre_of(entry)
        self.drag(start, start + QPoint(200, 150))
        moved = QPoint(entry.position)

        reloaded = DesktopCanvas(settings=self.settings)
        reloaded.set_entries([make_entry("app:files", "Dateien")])
        self.assertEqual(reloaded.entries[0].position, moved)


class TestDesktopCanvasSelection(DesktopCanvasTestCase):
    def test_click_selects_a_single_icon(self) -> None:
        entries = [make_entry("app:a", "A"), make_entry("app:b", "B")]
        self.canvas.set_entries(entries)
        first, second = self.canvas.entries

        self.press(self.centre_of(first))
        self.release(self.centre_of(first))

        self.assertTrue(first.selected)
        self.assertFalse(second.selected)

    def test_clicking_empty_space_clears_the_selection(self) -> None:
        entries = [make_entry("app:a", "A")]
        self.canvas.set_entries(entries)
        entry = self.canvas.entries[0]
        self.press(self.centre_of(entry))
        self.release(self.centre_of(entry))
        self.assertTrue(entry.selected)

        empty = QPoint(self.canvas.width() - 10, self.canvas.height() - 10)
        self.press(empty)
        self.release(empty)

        self.assertFalse(entry.selected)

    def test_control_click_toggles_an_icon(self) -> None:
        entries = [make_entry("app:a", "A"), make_entry("app:b", "B")]
        self.canvas.set_entries(entries)
        first, second = self.canvas.entries

        self.press(self.centre_of(first))
        self.release(self.centre_of(first))
        self.press(self.centre_of(second), CTRL)
        self.release(self.centre_of(second), CTRL)

        self.assertTrue(first.selected)
        self.assertTrue(second.selected)

        self.press(self.centre_of(second), CTRL)
        self.release(self.centre_of(second), CTRL)
        self.assertFalse(second.selected)

    def test_shift_click_selects_a_range(self) -> None:
        entries = [make_entry(f"app:{name}", name) for name in ("a", "b", "c")]
        self.canvas.set_entries(entries)
        first, _, third = self.canvas.entries

        self.press(self.centre_of(first))
        self.release(self.centre_of(first))
        self.press(self.centre_of(third), SHIFT)
        self.release(self.centre_of(third), SHIFT)

        self.assertEqual(len(self.canvas.selected_entries()), 3)

    def test_rubber_band_selects_every_icon_it_touches(self) -> None:
        entries = [make_entry(f"app:{name}", name) for name in ("a", "b", "c", "d")]
        self.canvas.set_entries(entries)
        positions = [entry.position for entry in self.canvas.entries]
        for entry, position in zip(self.canvas.entries, positions):
            entry.position = QPoint(position)

        # Drag an empty region that covers the first two icons only.
        origin = self.centre_of(self.canvas.entries[0])
        corner = QPoint(origin.x() + CELL_WIDTH, origin.y() + CELL_HEIGHT)
        empty_start = QPoint(MARGIN - 6, MARGIN - 6)

        self.press(empty_start)
        self.move(empty_start + QPoint(10, 10))
        self.move(corner)
        self.release(corner)

        selected = self.canvas.selected_entries()
        self.assertEqual(len(selected), 2)
        self.assertIn(self.canvas.entries[0], selected)
        self.assertIn(self.canvas.entries[1], selected)

    def test_control_a_selects_all_and_escape_clears(self) -> None:
        entries = [make_entry(f"app:{name}", name) for name in ("a", "b", "c")]
        self.canvas.set_entries(entries)

        QTest.keyClick(self.canvas, Qt.Key.Key_A, CTRL)
        self.assertEqual(len(self.canvas.selected_entries()), 3)

        QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
        self.assertEqual(self.canvas.selected_entries(), [])

    def test_arrow_keys_nudge_the_selection(self) -> None:
        self.canvas.set_entries([make_entry("app:files", "Dateien")])
        entry = self.canvas.entries[0]
        self.press(self.centre_of(entry))
        self.release(self.centre_of(entry))
        original = QPoint(entry.position)

        QTest.keyClick(self.canvas, Qt.Key.Key_Right)

        self.assertEqual(entry.position, original + QPoint(CELL_WIDTH + 10, 0))
        QTest.keyClick(self.canvas, Qt.Key.Key_Down, CTRL)
        self.assertEqual(entry.position, original + QPoint(CELL_WIDTH + 10, 1))

    def test_double_click_emits_the_entry(self) -> None:
        self.canvas.set_entries([make_entry("app:files", "Dateien")])
        entry = self.canvas.entries[0]
        received = []
        self.canvas.itemDoubleClicked.connect(received.append)

        QTest.mouseDClick(self.canvas, LEFT, Qt.KeyboardModifier.NoModifier, self.centre_of(entry))

        self.assertEqual(received, [entry])


class TestDesktopCanvasDragDrop(DesktopCanvasTestCase):
    def _drop(self, position: QPoint, paths: list[Path], modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        from PySide6.QtCore import QMimeData, QUrl
        from PySide6.QtGui import QDropEvent

        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        event = QDropEvent(
            QPointF(position),
            Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
            mime_data,
            LEFT,
            modifiers,
        )
        self.canvas.dropEvent(event)

    def test_files_dropped_on_empty_space_request_shortcuts(self) -> None:
        received = []
        self.canvas.files_dropped.connect(received.append)
        document = Path("/tmp/does-not-matter.txt")

        self._drop(QPoint(800, 600), [document])

        self.assertEqual(received, [[document]])

    def test_files_dropped_on_a_folder_go_inside_it(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="test_canvas_drop_"))
        self.addCleanup(shutil.rmtree, root, True)
        folder = root / "Ziel"
        folder.mkdir()
        document = root / "note.txt"
        document.write_text("data", encoding="utf-8")

        self.canvas.set_entries(
            [make_entry(f"file:{folder}", "Ziel", position=QPoint(200, 200))]
        )
        self.canvas.entries[0].path = folder
        received = []
        self.canvas.paths_dropped_into.connect(lambda p, t, c: received.append((p, t, c)))

        self._drop(QPoint(246, 248), [document], CTRL)

        self.assertEqual(received, [([document], folder, True)])

    def test_files_dropped_on_a_file_stay_on_the_desktop(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="test_canvas_drop_"))
        self.addCleanup(shutil.rmtree, root, True)
        document = root / "note.txt"
        document.write_text("data", encoding="utf-8")
        self.canvas.set_entries(
            [make_entry(f"file:{document}", "Notiz", position=QPoint(200, 200))]
        )
        self.canvas.entries[0].path = document
        received = []
        self.canvas.files_dropped.connect(received.append)

        self._drop(QPoint(246, 248), [root / "anderes.txt"])

        self.assertEqual(len(received), 1)


class TestDesktopShellInteractions(DesktopCanvasTestCase):
    """The desktop surface wired to the real NeonVeil shell."""

    def setUp(self) -> None:
        super().setUp()
        self.root = Path(self.settings.fileName()).parent / "desktop-shell"
        self.root.mkdir(parents=True, exist_ok=True)
        self.desktop_path = self.root / "Desktop"
        self.desktop_path.mkdir(exist_ok=True)
        self.shell = DesktopShell(desktop_path=self.desktop_path, settings=self.settings)
        self.shell.resize(900, 700)
        self.addCleanup(self.shell.close)
        self.addCleanup(self.shell.deleteLater)
        self.addCleanup(self.app.processEvents)

    def test_shell_icons_can_be_rearranged_and_it_persists(self) -> None:
        canvas = self.shell.shortcuts
        self.shell.show()
        self.app.processEvents()
        first = canvas.item(0)
        original = QPoint(first.position)
        start = self.centre_of(first, canvas)

        self.drag(start, start + QPoint(240, 180), widget=canvas)

        self.assertNotEqual(first.position, original)
        stored = self.settings.value("desktop/positions", "{}", type=str)
        self.assertIn(first.key, stored)

    def test_dropped_files_become_shortcuts(self) -> None:
        from apps.file_manager.shortcuts import create_desktop_shortcut

        document = self.root / "note.txt"
        document.write_text("hello", encoding="utf-8")
        link = create_desktop_shortcut(self.desktop_path, "Notiz", target=document)
        self.shell.show()
        self.app.processEvents()

        self.shell.refresh_shortcuts()

        keys = [
            self.shell.shortcuts.item(index).key
            for index in range(self.shell.shortcuts.count())
        ]
        self.assertIn(f"file:{link}", keys)

    def test_enter_opens_the_selected_entry(self) -> None:
        self.shell.show()
        self.shell.shortcuts.item(0).selected = True
        launched = []
        self.shell.application_requested.connect(launched.append)

        QTest.keyClick(self.shell.shortcuts, Qt.Key.Key_Return)

        self.assertEqual(launched, ["files"])


if __name__ == "__main__":
    unittest.main()
