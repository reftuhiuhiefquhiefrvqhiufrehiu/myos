from __future__ import annotations

import errno
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

CHUNK_SIZE = 1024 * 1024

REPLACE = "replace"
SKIP = "skip"
RENAME = "rename"
CANCEL = "cancel"

ConflictHandler = Callable[[Path, Path], "ConflictDecision"]
ProgressHandler = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


class TransferCancelled(Exception):
    """Raised internally when the user cancels a running transfer."""


@dataclass
class ConflictDecision:
    action: str
    new_name: str | None = None
    apply_to_all: bool = False


@dataclass
class TransferResult:
    copied: list[Path] = field(default_factory=list)
    moved: list[Path] = field(default_factory=list)
    replaced: list[Path] = field(default_factory=list)
    renamed: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def changed(self) -> list[Path]:
        return [*self.copied, *self.moved]

    def summary(self) -> str:
        parts = []
        if self.copied:
            parts.append(f"{len(self.copied)} kopiert")
        if self.moved:
            parts.append(f"{len(self.moved)} verschoben")
        if self.replaced:
            parts.append(f"{len(self.replaced)} ersetzt")
        if self.renamed:
            parts.append(f"{len(self.renamed)} umbenannt")
        if self.skipped:
            parts.append(f"{len(self.skipped)} übersprungen")
        if self.cancelled:
            parts.append("abgebrochen")
        return ", ".join(parts) if parts else "Keine Änderungen"


def path_size(path: Path) -> int:
    try:
        if path.is_symlink() or not path.is_dir():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                    stack.append(Path(entry.path))
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def unique_destination(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists() and not candidate.is_symlink():
        return candidate
    path = Path(name)
    stem = path.stem if path.suffix else path.name
    suffix = path.suffix
    index = 2
    while True:
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        index += 1


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def check_free_space(destination: Path, required: int) -> None:
    if required <= 0:
        return
    try:
        available = shutil.disk_usage(destination).free
    except OSError:
        return
    if required > available:
        raise OSError(
            errno.ENOSPC,
            "Nicht genügend Speicherplatz am Ziel: benötigt "
            f"{human_size(required)}, verfügbar {human_size(available)}.",
        )


class _Progress:
    def __init__(
        self,
        total: int,
        progress: ProgressHandler | None,
        should_cancel: CancelCheck | None,
    ) -> None:
        self.total = total
        self.done = 0
        self._progress = progress
        self._should_cancel = should_cancel

    def start(self, name: str) -> None:
        self.check()
        if self._progress is not None:
            self._progress(self.done, self.total, name)

    def advance(self, amount: int, name: str) -> None:
        self.done += amount
        if self._progress is not None:
            self._progress(self.done, self.total, name)
        self.check()

    def check(self) -> None:
        if self._should_cancel is not None and self._should_cancel():
            raise TransferCancelled()


def copy_file(source: Path, target: Path, progress: _Progress) -> None:
    name = source.name
    progress.start(name)
    with open(source, "rb") as source_file, open(target, "wb") as target_file:
        while True:
            chunk = source_file.read(CHUNK_SIZE)
            if not chunk:
                break
            target_file.write(chunk)
            progress.advance(len(chunk), name)
    shutil.copystat(source, target, follow_symlinks=False)


def copy_directory(source: Path, target: Path, progress: _Progress) -> None:
    target.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copystat(source, target, follow_symlinks=False)
    except OSError:
        pass
    with os.scandir(source) as entries:
        for entry in entries:
            entry_source = Path(entry.path)
            entry_target = target / entry.name
            if entry.is_symlink():
                _copy_symlink(entry_source, entry_target, progress)
            elif entry.is_dir(follow_symlinks=False):
                copy_directory(entry_source, entry_target, progress)
            else:
                copy_file(entry_source, entry_target, progress)


def _copy_symlink(source: Path, target: Path, progress: _Progress) -> None:
    progress.check()
    if target.exists() or target.is_symlink():
        _remove_path(target)
    os.symlink(os.readlink(source), target)


def _copy_item(source: Path, target: Path, progress: _Progress) -> None:
    if source.is_symlink():
        _copy_symlink(source, target, progress)
    elif source.is_dir():
        copy_directory(source, target, progress)
    else:
        copy_file(source, target, progress)


def _same_filesystem(source: Path, target_parent: Path) -> bool:
    try:
        return os.lstat(source).st_dev == os.stat(target_parent).st_dev
    except OSError:
        return False


def _move_item(source: Path, target: Path, progress: _Progress) -> None:
    if not source.is_symlink() and _same_filesystem(source, target.parent):
        progress.check()
        os.rename(source, target)
        return
    _copy_item(source, target, progress)
    _remove_path(source)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _is_descendant(candidate: Path, ancestor: Path) -> bool:
    try:
        candidate.relative_to(ancestor)
        return True
    except ValueError:
        return False


def validate_transfer(
    sources: Iterable[Path], destination: Path
) -> tuple[list[Path], list[str]]:
    errors: list[str] = []
    valid: list[Path] = []
    destination = Path(destination).absolute()
    if not destination.is_dir():
        return [], [f"{destination}: Der Zielordner ist nicht verfügbar."]
    for source in sources:
        source = Path(source)
        if not source.exists() and not source.is_symlink():
            errors.append(f"{source.name}: Das Element existiert nicht mehr.")
            continue
        try:
            source_real = source.resolve()
            destination_real = destination.resolve()
        except (OSError, RuntimeError) as error:
            errors.append(f"{source.name}: Ziel kann nicht geprüft werden: {error}")
            continue
        if source.is_dir() and not source.is_symlink() and (
            source_real == destination_real
            or _is_descendant(destination_real, source_real)
        ):
            errors.append(
                f"{source.name}: Ein Ordner kann nicht in sich selbst kopiert werden."
            )
            continue
        valid.append(source)
    return valid, errors


def transfer_items(
    sources: Iterable[Path],
    destination: Path,
    *,
    move: bool,
    decide_conflict: ConflictHandler,
    progress: ProgressHandler | None = None,
    should_cancel: CancelCheck | None = None,
) -> TransferResult:
    destination = Path(destination)
    result = TransferResult()
    valid_sources, errors = validate_transfer(sources, destination)
    result.errors.extend(errors)
    total = sum(path_size(source) for source in valid_sources)
    state = _Progress(total, progress, should_cancel)

    try:
        check_free_space(destination, total)
    except OSError as error:
        result.errors.append(error.strerror or str(error))
        return result

    for source in valid_sources:
        try:
            _transfer_single(source, destination, move, decide_conflict, state, result)
        except TransferCancelled:
            result.cancelled = True
            break
        except OSError as error:
            result.errors.append(f"{source.name}: {error.strerror or error}")
            continue
    return result


def _transfer_single(
    source: Path,
    destination: Path,
    move: bool,
    decide_conflict: ConflictHandler,
    state: _Progress,
    result: TransferResult,
) -> None:
    target = destination / source.name
    decision: ConflictDecision | None = None

    if target.exists() or target.is_symlink():
        try:
            same = source.resolve() == target.resolve()
        except (OSError, RuntimeError):
            same = False
        if same:
            result.skipped.append(source)
            return
        decision = decide_conflict(source, target)
        if decision.action == CANCEL:
            raise TransferCancelled()
        if decision.action == SKIP:
            result.skipped.append(source)
            return
        if decision.action == RENAME:
            target = unique_destination(destination, decision.new_name or source.name)
        elif decision.action == REPLACE:
            _remove_path(target)

    if move:
        _move_item(source, target, state)
        result.moved.append(target)
    else:
        _copy_item(source, target, state)
        result.copied.append(target)

    if decision is not None:
        if decision.action == REPLACE:
            result.replaced.append(target)
        elif decision.action == RENAME:
            result.renamed.append(target)
