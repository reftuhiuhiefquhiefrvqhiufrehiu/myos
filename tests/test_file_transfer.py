import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from apps.file_manager import transfer
from apps.file_manager.transfer import (
    CANCEL,
    RENAME,
    REPLACE,
    SKIP,
    ConflictDecision,
    human_size,
    path_size,
    transfer_items,
    unique_destination,
    validate_transfer,
)


def accept_all(action, new_name=None):
    return lambda _source, _target: ConflictDecision(action, new_name=new_name)


class TransferEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_path_size_counts_files_and_nested_directories(self) -> None:
        folder = self.root / "folder"
        (folder / "nested").mkdir(parents=True)
        (folder / "a.bin").write_bytes(b"x" * 100)
        (folder / "nested" / "b.bin").write_bytes(b"y" * 250)
        self.assertEqual(path_size(folder / "a.bin"), 100)
        self.assertEqual(path_size(folder), 350)

    def test_unique_destination_preserves_suffix(self) -> None:
        (self.root / "note.txt").touch()
        self.assertEqual(unique_destination(self.root, "note.txt").name, "note (2).txt")
        (self.root / "note (2).txt").touch()
        self.assertEqual(unique_destination(self.root, "note.txt").name, "note (3).txt")

    def test_copy_and_move_files_and_directories(self) -> None:
        source_dir = self.root / "source"
        target_dir = self.root / "target"
        (source_dir / "sub").mkdir(parents=True)
        target_dir.mkdir()
        (source_dir / "sub" / "file.txt").write_text("data", encoding="utf-8")

        result = transfer_items(
            [source_dir],
            target_dir,
            move=False,
            decide_conflict=accept_all(SKIP),
        )
        self.assertEqual(result.errors, [])
        self.assertTrue((target_dir / "source" / "sub" / "file.txt").is_file())
        self.assertTrue(source_dir.is_dir())

        moved_result = transfer_items(
            [source_dir],
            target_dir,
            move=True,
            decide_conflict=accept_all(REPLACE),
        )
        self.assertEqual(moved_result.errors, [])
        self.assertFalse(source_dir.exists())

    def test_conflict_skip_replace_and_rename(self) -> None:
        source_dir = self.root / "source"
        target_dir = self.root / "target"
        source_dir.mkdir()
        target_dir.mkdir()
        source = source_dir / "note.txt"
        source.write_text("new", encoding="utf-8")
        target = target_dir / "note.txt"
        target.write_text("old", encoding="utf-8")

        skipped = transfer_items(
            [source], target_dir, move=False, decide_conflict=accept_all(SKIP)
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(len(skipped.skipped), 1)

        replaced = transfer_items(
            [source], target_dir, move=False, decide_conflict=accept_all(REPLACE)
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(len(replaced.replaced), 1)

        renamed = transfer_items(
            [source],
            target_dir,
            move=False,
            decide_conflict=accept_all(RENAME, "renamed.txt"),
        )
        self.assertTrue((target_dir / "renamed.txt").is_file())
        self.assertEqual(len(renamed.renamed), 1)

    def test_conflict_cancel_aborts_transfer(self) -> None:
        source_dir = self.root / "source"
        target_dir = self.root / "target"
        source_dir.mkdir()
        target_dir.mkdir()
        source = source_dir / "note.txt"
        source.write_text("new", encoding="utf-8")
        (target_dir / "note.txt").write_text("old", encoding="utf-8")

        result = transfer_items(
            [source], target_dir, move=False, decide_conflict=accept_all(CANCEL)
        )
        self.assertTrue(result.cancelled)
        self.assertEqual((target_dir / "note.txt").read_text(encoding="utf-8"), "old")

    def test_transfer_reports_insufficient_space(self) -> None:
        source = self.root / "big.bin"
        source.write_bytes(b"x" * 4096)
        target_dir = self.root / "target"
        target_dir.mkdir()
        usage = types.SimpleNamespace(total=100, used=100, free=0)
        with patch.object(transfer.shutil, "disk_usage", return_value=usage):
            result = transfer_items(
                [source], target_dir, move=False, decide_conflict=accept_all(SKIP)
            )
        self.assertTrue(result.errors)
        self.assertIn("Speicherplatz", result.errors[0])
        self.assertFalse((target_dir / "big.bin").exists())

    def test_transfer_reports_permission_error_per_item(self) -> None:
        source = self.root / "note.txt"
        source.write_text("data", encoding="utf-8")
        target_dir = self.root / "target"
        target_dir.mkdir()
        with patch.object(
            transfer, "copy_file", side_effect=PermissionError("Kein Zugriff")
        ):
            result = transfer_items(
                [source], target_dir, move=False, decide_conflict=accept_all(SKIP)
            )
        self.assertEqual(len(result.errors), 1)
        self.assertIn("note.txt", result.errors[0])

    def test_transfer_can_be_cancelled_mid_copy(self) -> None:
        source = self.root / "large.bin"
        source.write_bytes(b"x" * (3 * 1024 * 1024))
        target_dir = self.root / "target"
        target_dir.mkdir()
        seen = {"done": 0}

        def progress(done, _total, _name):
            seen["done"] = done

        def should_cancel():
            return seen["done"] >= 1024 * 1024

        result = transfer_items(
            [source],
            target_dir,
            move=False,
            decide_conflict=accept_all(SKIP),
            progress=progress,
            should_cancel=should_cancel,
        )
        self.assertTrue(result.cancelled)
        self.assertFalse(result.copied)

    def test_validate_transfer_rejects_folder_into_itself(self) -> None:
        folder = self.root / "folder"
        inner = folder / "inner"
        inner.mkdir(parents=True)
        valid, errors = validate_transfer([folder], inner)
        self.assertEqual(valid, [])
        self.assertTrue(errors)

    def test_progress_is_reported(self) -> None:
        source = self.root / "data.txt"
        source.write_text("hello", encoding="utf-8")
        target_dir = self.root / "target"
        target_dir.mkdir()
        updates = []
        transfer_items(
            [source],
            target_dir,
            move=False,
            decide_conflict=accept_all(SKIP),
            progress=lambda done, total, name: updates.append((done, total, name)),
        )
        self.assertEqual(updates[-1][0], updates[-1][1])
        self.assertEqual(updates[-1][2], "data.txt")

    def test_human_size_formats_units(self) -> None:
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(2048), "2.0 KB")


if __name__ == "__main__":
    unittest.main()
