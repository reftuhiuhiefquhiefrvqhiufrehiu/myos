"""Guard against annotations that break imports on older Python.

NeonVeil runs on Python 3.12 in CI and on the Raspberry Pi image, while a
development machine may use 3.11 or newer. Without ``from __future__ import
annotations`` an annotation is evaluated when the class body runs, so a name
that is not bound raises ``NameError`` and the module cannot be imported at
all. Two such leftovers already broke release builds, so this stays covered.
"""

from __future__ import annotations

import ast
import builtins
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SKIPPED_PARTS = {"build", "__pycache__", ".git", ".venv"}


def collect_module_names(tree: ast.AST) -> set[str]:
    """Names available at module level, ignoring nested scopes."""
    names = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                (alias.asname or alias.name).split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global):
            names.update(node.names)
    return names


def uses_lazy_annotations(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in ast.walk(tree)
    )


def annotation_expressions(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = node.args
        candidates = (
            arguments.args
            + arguments.posonlyargs
            + arguments.kwonlyargs
            + [argument for argument in (arguments.vararg, arguments.kwarg) if argument]
        )
        return [item.annotation for item in candidates if item.annotation] + (
            [node.returns] if node.returns else []
        )
    if isinstance(node, ast.AnnAssign) and node.annotation is not None:
        return [node.annotation]
    return []


class TestAnnotationSafety(unittest.TestCase):
    def test_every_annotation_resolves(self) -> None:
        offenders: list[str] = []
        checked = 0
        for path in sorted(ROOT_DIR.rglob("*.py")):
            if any(part in SKIPPED_PARTS for part in path.parts):
                continue
            relative = path.relative_to(ROOT_DIR)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError) as error:
                self.fail(f"{relative} konnte nicht gelesen werden: {error}")
            checked += 1
            names = collect_module_names(tree)
            lazy = uses_lazy_annotations(tree)
            for node in ast.walk(tree):
                for annotation in annotation_expressions(node):
                    for sub in ast.walk(annotation):
                        if (
                            isinstance(sub, ast.Name)
                            and sub.id not in names
                            and not lazy
                        ):
                            offenders.append(f"{relative}:{sub.lineno} '{sub.id}'")

        self.assertGreater(checked, 20, "Die Prüfung hat zu wenig Dateien gesehen")
        self.assertEqual(
            offenders, [], "Annotationen mit ungebundenem Namen (Importfehler)"
        )


if __name__ == "__main__":
    unittest.main()
