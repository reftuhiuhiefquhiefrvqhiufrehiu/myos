from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMessageBox,
    QProgressDialog,
    QWidget,
)

from apps.file_manager.transfer import (
    CANCEL,
    RENAME,
    REPLACE,
    SKIP,
    ConflictDecision,
    TransferResult,
    human_size,
    path_size,
    transfer_items,
)

PROGRESS_THRESHOLD = 8 * 1024 * 1024


def run_transfer(
    parent: QWidget | None,
    sources: Iterable[Path],
    destination: Path,
    *,
    move: bool,
    progress_threshold: int = PROGRESS_THRESHOLD,
) -> TransferResult:
    sources = [Path(source) for source in sources]
    destination = Path(destination)
    remembered: dict[str, str | None] = {"action": None, "new_name": None}

    def decide_conflict(source: Path, target: Path) -> ConflictDecision:
        if remembered["action"] in {REPLACE, SKIP}:
            return ConflictDecision(remembered["action"])
        decision = _ask_conflict(parent, source, target)
        if decision.apply_to_all and decision.action in {REPLACE, SKIP}:
            remembered["action"] = decision.action
        return decision

    total = sum(path_size(source) for source in sources)
    dialog: QProgressDialog | None = None
    if total >= progress_threshold and total > 0:
        verb = "Verschieben" if move else "Kopieren"
        dialog = QProgressDialog(
            f"{verb} läuft…", "Abbrechen", 0, total, parent
        )
        dialog.setWindowTitle(f"{verb} von Elementen")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)
        dialog.show()
        QApplication.processEvents()

    def progress(done: int, total_bytes: int, name: str) -> None:
        if dialog is None:
            return
        dialog.setLabelText(
            f"{name}\n{human_size(done)} von {human_size(max(total_bytes, 1))}"
        )
        dialog.setValue(min(done, dialog.maximum()))
        QApplication.processEvents()

    def should_cancel() -> bool:
        return dialog is not None and dialog.wasCanceled()

    try:
        result = transfer_items(
            sources,
            destination,
            move=move,
            decide_conflict=decide_conflict,
            progress=progress,
            should_cancel=should_cancel,
        )
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()

    if result.errors:
        QMessageBox.warning(
            parent,
            "Einige Elemente konnten nicht übertragen werden",
            "\n".join(result.errors[:8]),
        )
    return result


def _ask_conflict(parent: QWidget | None, source: Path, target: Path) -> ConflictDecision:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Element existiert bereits")
    box.setText(f"Im Ziel existiert bereits „{target.name}“.")
    box.setInformativeText(
        f"Quelle: {source.name}\n"
        "Möchtest du das vorhandene Element ersetzen, überspringen oder umbenennen?"
    )
    replace_button = box.addButton("Ersetzen", QMessageBox.ButtonRole.DestructiveRole)
    skip_button = box.addButton("Überspringen", QMessageBox.ButtonRole.RejectRole)
    rename_button = box.addButton("Umbenennen…", QMessageBox.ButtonRole.ActionRole)
    cancel_button = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
    checkbox = box.setCheckBox(
        _create_checkbox("Für alle folgenden Elemente anwenden")
    )
    box.setDefaultButton(skip_button)
    box.exec()

    clicked = box.clickedButton()
    if clicked is cancel_button or clicked is None:
        return ConflictDecision(CANCEL)
    apply_all = bool(checkbox and checkbox.isChecked())
    if clicked is replace_button:
        return ConflictDecision(REPLACE, apply_to_all=apply_all)
    if clicked is rename_button:
        new_name, accepted = QInputDialog.getText(
            parent, "Umbenennen", "Neuer Name:", text=human_unique_name(target.name)
        )
        if not accepted or not new_name or Path(new_name).name != new_name:
            return ConflictDecision(SKIP)
        return ConflictDecision(RENAME, new_name=new_name)
    return ConflictDecision(SKIP, apply_to_all=apply_all)


def human_unique_name(name: str) -> str:
    path = Path(name)
    stem = path.stem if path.suffix else path.name
    suffix = path.suffix
    return f"{stem} (Kopie){suffix}"


def _create_checkbox(text: str):
    from PySide6.QtWidgets import QCheckBox

    checkbox = QCheckBox(text)
    return checkbox
