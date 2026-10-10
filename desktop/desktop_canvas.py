"""Interactive desktop canvas.

The NeonVeil desktop is a real canvas rather than an automatically laid-out
list: icons sit where the user put them, several icons can be selected at
once, and a rubber band selects a region. Drag & drop works in both
directions, so files arrive from the file manager and can be dragged back
out again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, Signal, QUrl
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


VALUE_ROLE = Qt.ItemDataRole.UserRole
PATH_ROLE = Qt.ItemDataRole.UserRole + 1

MARGIN = 22
CELL_WIDTH = 92
CELL_HEIGHT = 96
COLUMN_GAP = 10
ICON_SIZE = 46
LABEL_HEIGHT = 30

ACCENT = QColor(53, 201, 189)
TEXT = QColor(242, 247, 247)


@dataclass
class DesktopEntry:
    """One desktop icon: an application, a folder, or a file."""

    key: str
    label: str
    icon: QIcon
    position: QPoint | None = None
    app_id: str | None = None
    path: Path | None = None
    selected: bool = False

    def data(self, role: int):
        """Mirror the old ``QListWidgetItem.data`` access pattern."""
        if role == VALUE_ROLE:
            return self.key
        if role == PATH_ROLE:
            return str(self.path) if self.path is not None else None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.label
        return None

    @property
    def is_directory(self) -> bool:
        return self.path is not None and self.path.is_dir()

    def folder_target(self) -> Path | None:
        """The folder this entry represents, if any.

        A ``.desktop`` link counts only when it points at a plain folder, so
        application links are never mistaken for a drop target.
        """
        if self.path is None:
            return None
        if self.path.suffix.casefold() == ".desktop":
            from apps.file_manager.shortcuts import read_desktop_shortcut

            try:
                target, app_id = read_desktop_shortcut(self.path)
            except (OSError, ValueError):
                return None
            if app_id is None and target is not None and target.is_dir():
                return target
            return None
        return self.path if self.path.is_dir() else None


class DesktopCanvas(QWidget):
    """Desktop surface with free icon placement and multi-selection."""

    itemDoubleClicked = Signal(object)
    files_dropped = Signal(object)
    paths_dropped_into = Signal(object, object, bool)
    key_command = Signal(str)
    menu_requested = Signal(QPoint)
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None, settings=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)

        self.settings = settings
        self._entries: list[DesktopEntry] = []
        self._positions: dict[str, QPoint] = {}
        # Icons the user moved by hand keep their place; the rest reflow so they
        # always fill the columns that actually fit.
        self._placed_by_user: set[str] = set()
        self._load_positions()

        self._pressed_entry: DesktopEntry | None = None
        self._drag_offset = QPoint()
        self._press_position = QPoint()
        self._moved = False
        self._marquee_origin: QPoint | None = None
        self._marquee_rect = QRect()
        self._hover_entry: DesktopEntry | None = None

    # ------------------------------------------------------------------ setup

    def _load_positions(self) -> None:
        if self.settings is None:
            return
        try:
            raw = self.settings.value("desktop/positions", "{}", type=str)
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, list) and len(value) == 2:
                try:
                    self._positions[key] = QPoint(int(value[0]), int(value[1]))
                except (TypeError, ValueError):
                    continue

    def save_positions(self) -> None:
        """Persist what is currently on screen, whatever moved the icons."""
        if self.settings is None:
            return
        payload: dict[str, list[int]] = {}
        for entry in self._entries:
            if entry.position is None:
                continue
            point = QPoint(entry.position)
            self._positions[entry.key] = point
            payload[entry.key] = [point.x(), point.y()]
        self.settings.setValue("desktop/positions", json.dumps(payload))

    def set_entries(self, entries: list[DesktopEntry]) -> None:
        """Replace the surface contents, keeping known icon positions.

        An entry that arrives with a position already set is taken at its word,
        so callers that know where an icon belongs can place it directly.
        """
        self._entries = list(entries)
        unplaced: list[DesktopEntry] = []
        for entry in self._entries:
            if entry.position is not None:
                entry.position = self._clamp(QPoint(entry.position))
                self._positions[entry.key] = entry.position
                continue
            stored = self._positions.get(entry.key)
            if stored is not None:
                entry.position = self._clamp(QPoint(stored))
            else:
                unplaced.append(entry)
        if unplaced:
            self._place(unplaced)
        self._marquee_origin = None
        self._marquee_rect = QRect()
        self.update()

    def clear(self) -> None:
        self._entries = []
        self._positions = {}
        self._placed_by_user = set()
        self._pressed_entry = None
        self._marquee_origin = None
        self._marquee_rect = QRect()
        self.update()

    def count(self) -> int:
        return len(self._entries)

    def item(self, index: int) -> DesktopEntry | None:
        if 0 <= index < len(self._entries):
            return self._entries[index]
        return None

    @property
    def entries(self) -> list[DesktopEntry]:
        return list(self._entries)

    def selected_entries(self) -> list[DesktopEntry]:
        return [entry for entry in self._entries if entry.selected]

    # ---------------------------------------------------------------- geometry

    def _cell_origin(self, column: int, row: int) -> QPoint:
        return QPoint(MARGIN + column * (CELL_WIDTH + COLUMN_GAP), MARGIN + row * CELL_HEIGHT)

    def _slot(self, point: QPoint) -> tuple[int, int]:
        column = max(0, round((point.x() - MARGIN) / (CELL_WIDTH + COLUMN_GAP)))
        row = max(0, round((point.y() - MARGIN) / CELL_HEIGHT))
        return column, row

    def _clamp(self, point: QPoint) -> QPoint:
        limit_x = max(0, self.width() - CELL_WIDTH - MARGIN)
        limit_y = max(0, self.height() - CELL_HEIGHT - MARGIN)
        return QPoint(min(max(MARGIN, point.x()), limit_x), min(max(MARGIN, point.y()), limit_y))

    def entry_rect(self, entry: DesktopEntry) -> QRect:
        position = entry.position or QPoint(MARGIN, MARGIN)
        return QRect(position, QSize(CELL_WIDTH, CELL_HEIGHT))

    def entry_at(self, position: QPoint) -> DesktopEntry | None:
        for entry in reversed(self._entries):
            if self.entry_rect(entry).contains(position):
                return entry
        return None

    def _place(self, entries: list[DesktopEntry]) -> None:
        """Put icons into the first free slots, filling left to right."""
        columns = max(1, (self.width() - 2 * MARGIN) // (CELL_WIDTH + COLUMN_GAP))
        rows = max(1, (self.height() - 2 * MARGIN) // CELL_HEIGHT)
        taken = {
            self._slot(entry.position)
            for entry in self._entries
            if entry.position is not None
        }
        index = 0
        for entry in entries:
            while index < columns * rows and (index % columns, index // columns) in taken:
                index += 1
            column, row = index % columns, index // columns
            entry.position = self._cell_origin(column, row)
            self._positions[entry.key] = entry.position
            taken.add((column, row))
            index += 1
        self.save_positions()

    def _reflow(self) -> bool:
        """Re-place every icon the user has not moved by hand."""
        flowing = [
            entry for entry in self._entries if entry.key not in self._placed_by_user
        ]
        if not flowing:
            return False
        before = {entry.key: entry.position for entry in flowing}
        for entry in flowing:
            entry.position = None
        self._place(flowing)
        return any(entry.position != before[entry.key] for entry in flowing)

    def auto_arrange(self) -> None:
        """Snap every icon back onto the grid, in current order."""
        columns = max(1, (self.width() - 2 * MARGIN) // (CELL_WIDTH + COLUMN_GAP))
        rows = max(1, (self.height() - 2 * MARGIN) // CELL_HEIGHT)
        for index, entry in enumerate(self._entries):
            if index >= columns * rows:
                break
            entry.position = self._cell_origin(index % columns, index // columns)
            self._positions[entry.key] = entry.position
            self._placed_by_user.add(entry.key)
        self.save_positions()
        self.update()

    # --------------------------------------------------------------- selection

    def clear_selection(self) -> None:
        changed = any(entry.selected for entry in self._entries)
        for entry in self._entries:
            entry.selected = False
        if changed:
            self.selection_changed.emit()
        self.update()

    def select_all(self) -> None:
        for entry in self._entries:
            entry.selected = True
        self.selection_changed.emit()
        self.update()

    def select_only(self, entry: DesktopEntry) -> None:
        for candidate in self._entries:
            candidate.selected = candidate is entry
        self.selection_changed.emit()

    def _select_range(self, target: DesktopEntry) -> None:
        indices = [index for index, entry in enumerate(self._entries) if entry.selected]
        anchor = max(indices) if indices else 0
        last = self._entries.index(target)
        for index, entry in enumerate(self._entries):
            entry.selected = min(anchor, last) <= index <= max(anchor, last)
        self.selection_changed.emit()

    def _apply_marquee(self) -> None:
        for entry in self._entries:
            entry.selected = self.entry_rect(entry).intersects(self._marquee_rect)
        self.selection_changed.emit()

    # ------------------------------------------------------------------ events

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        position = event.position().toPoint()
        self._press_position = position
        self._moved = False
        modifiers = event.modifiers()
        entry = self.entry_at(position)

        if entry is None:
            if not modifiers & Qt.KeyboardModifier.ShiftModifier:
                self.clear_selection()
            self._marquee_origin = position
            self._marquee_rect = QRect(position, position)
            self.update()
            return

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            entry.selected = not entry.selected
            self.selection_changed.emit()
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            self._select_range(entry)
        elif not entry.selected:
            self.select_only(entry)

        if entry.selected:
            self._pressed_entry = entry
            self._drag_offset = (entry.position or position) - position
        self.update()

    def mouseMoveEvent(self, event) -> None:
        position = event.position().toPoint()

        if self._marquee_origin is not None:
            self._marquee_rect = QRect(self._marquee_origin, position).normalized()
            self._apply_marquee()
            self.update()
            return

        if self._pressed_entry is None:
            hovered = self.entry_at(position)
            if hovered is not self._hover_entry:
                self._hover_entry = hovered
                self.update()
            return

        if (position - self._press_position).manhattanLength() < QApplication.startDragDistance():
            return
        self._moved = True

        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift+drag pulls files back out to another window.
            self._start_external_drag()
            self._pressed_entry = None
            return

        target = self._clamp(position + self._drag_offset)
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            column, row = self._slot(target)
            target = self._clamp(self._cell_origin(column, row))
        self._move_selection(target)

    def mouseReleaseEvent(self, event) -> None:
        if self._marquee_origin is not None:
            self._marquee_origin = None
            self._marquee_rect = QRect()
        if self._pressed_entry is not None and self._moved:
            self.save_positions()
        self._pressed_entry = None
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        entry = self.entry_at(event.position().toPoint())
        if entry is not None:
            self.itemDoubleClicked.emit(entry)

    def _move_selection(self, target: QPoint) -> None:
        pressed = self._pressed_entry
        if pressed is None or pressed.position is None:
            return
        delta = target - pressed.position
        if delta.isNull():
            return
        selection = self.selected_entries() or [pressed]
        for entry in selection:
            moved = self._clamp((entry.position or QPoint()) + delta)
            entry.position = moved
            self._positions[entry.key] = moved
            self._placed_by_user.add(entry.key)
        self.update()

    def _start_external_drag(self) -> None:
        paths = [entry.path for entry in self.selected_entries() if entry.path is not None]
        if not paths:
            return
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        first = self.selected_entries()[0]
        if not first.icon.isNull():
            drag.setPixmap(first.icon.pixmap(QSize(ICON_SIZE, ICON_SIZE)))
        drag.setHotSpot(QPoint(ICON_SIZE // 2, ICON_SIZE // 2))
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)

    # ------------------------------------------------------------- drag & drop

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    @staticmethod
    def _drop_modifiers(event: Any) -> Qt.KeyboardModifier:
        """Modifiers of a drop event, across Qt versions.

        ``QDropEvent.keyboardModifiers()`` is deprecated in Qt 6 in favour of
        ``modifiers()``, which is not present in every binding.
        """
        getter = getattr(event, "modifiers", None)
        if callable(getter):
            return getter()
        return event.keyboardModifiers()

    def dropEvent(self, event) -> None:
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if not paths:
            event.ignore()
            return
        copy = bool(
            self._drop_modifiers(event) & Qt.KeyboardModifier.ControlModifier
        )
        entry = self.entry_at(event.position().toPoint())
        if entry is not None:
            target = entry.folder_target()
            if target is not None:
                self.paths_dropped_into.emit(paths, target, copy)
                event.acceptProposedAction()
                return
        self.files_dropped.emit(paths)
        event.acceptProposedAction()

    # -------------------------------------------------------------- keyboard

    def keyPressEvent(self, event) -> None:
        modifiers = event.modifiers()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            selection = self.selected_entries()
            if selection:
                self.itemDoubleClicked.emit(selection[0])
                return
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.key_command.emit("delete")
            return
        elif event.key() == Qt.Key.Key_A and modifiers & Qt.KeyboardModifier.ControlModifier:
            self.select_all()
            return
        elif event.key() == Qt.Key.Key_Escape:
            self.clear_selection()
            return
        elif event.key() in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            self._nudge(event.key(), fine=bool(modifiers & Qt.KeyboardModifier.ControlModifier))
            return
        super().keyPressEvent(event)

    def _nudge(self, key: int, fine: bool) -> None:
        selection = self.selected_entries()
        if not selection:
            return
        step = 1 if fine else (CELL_WIDTH + COLUMN_GAP if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) else CELL_HEIGHT)
        offsets = {
            Qt.Key.Key_Left: QPoint(-step, 0),
            Qt.Key.Key_Right: QPoint(step, 0),
            Qt.Key.Key_Up: QPoint(0, -step),
            Qt.Key.Key_Down: QPoint(0, step),
        }
        delta = offsets[key]
        for entry in selection:
            moved = self._clamp((entry.position or QPoint()) + delta)
            entry.position = moved
            self._positions[entry.key] = moved
            self._placed_by_user.add(entry.key)
        self.save_positions()
        self.update()

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(event.pos())
        super().contextMenuEvent(event)

    # ------------------------------------------------------------------ paint

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        changed = self._reflow()
        for entry in self._entries:
            if entry.position is None:
                continue
            clamped = self._clamp(entry.position)
            if clamped != entry.position:
                entry.position = clamped
                changed = True
            self._positions[entry.key] = entry.position
        if changed:
            self.save_positions()
        self.update()

    def _fallback_icon(self, entry: DesktopEntry) -> QIcon:
        if entry.is_directory:
            return self.style().standardIcon(
                self.style().StandardPixmap.SP_DirIcon
            )
        return self.style().standardIcon(self.style().StandardPixmap.SP_FileIcon)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for entry in self._entries:
            rect = self.entry_rect(entry)
            icon = entry.icon if not entry.icon.isNull() else self._fallback_icon(entry)

            if entry.selected:
                painter.setBrush(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 60))
                painter.setPen(QPen(ACCENT, 1))
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)
            elif entry is self._hover_entry:
                painter.setBrush(QColor(255, 255, 255, 28))
                painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)

            pixmap = icon.pixmap(QSize(ICON_SIZE, ICON_SIZE))
            painter.drawPixmap(
                rect.center().x() - ICON_SIZE // 2,
                rect.top() + 6,
                pixmap,
            )

            label_rect = rect.adjusted(2, 6 + ICON_SIZE + 2, -2, -2)
            metrics = painter.fontMetrics()
            text = metrics.elidedText(
                entry.label, Qt.TextElideMode.ElideRight, label_rect.width()
            )
            painter.setPen(TEXT)
            painter.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                text,
            )

        if self._marquee_rect.isValid() and self._marquee_origin is not None:
            painter.setBrush(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 45))
            painter.setPen(QPen(ACCENT, 1, Qt.PenStyle.DashLine))
            painter.drawRect(self._marquee_rect)

        painter.end()
