"""Keep Gludd's reusable task core independent of self-improvement."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src" / "general_ludd"
DECISION_RECORD = ROOT / "docs" / "architecture" / "universal-task-core.md"

UNIVERSAL_PACKAGE_ROOTS = (
    "agents",
    "dispatch",
    "execution",
    "models",
    "runtime",
    "scheduling",
)
UNIVERSAL_MODULE_NAMES = (
    "core.py",
    "model.py",
    "provider.py",
    "runtime.py",
    "scheduler.py",
)
CAPABILITY_OWNED_ROOTS = {"self_improve", "self_update"}
FORBIDDEN_IMPORT_ROOTS = (
    "general_ludd.self_improve",
    "general_ludd.reload.self_improve",
)


def _is_universal_module(path: Path) -> bool:
    relative = path.relative_to(SOURCE_ROOT)
    if relative.parts[0] in CAPABILITY_OWNED_ROOTS:
        return False
    if relative.parts[0] in UNIVERSAL_PACKAGE_ROOTS:
        return True
    stem = path.stem
    return path.name in UNIVERSAL_MODULE_NAMES or any(
        token in stem
        for token in (
            "_model",
            "model_",
            "_provider",
            "provider_",
            "_scheduler",
            "scheduler_",
        )
    )


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(
                f"{module}.{alias.name}" if module else alias.name
                for alias in node.names
            )
    return imported


def test_universal_task_modules_do_not_depend_on_self_improvement() -> None:
    violations: list[str] = []
    protected = sorted(
        path
        for path in SOURCE_ROOT.rglob("*.py")
        if _is_universal_module(path)
    )

    assert protected, "universal task boundary selected no Python modules"
    for path in protected:
        for imported in _imports(path):
            if any(
                imported == root or imported.startswith(f"{root}.")
                for root in FORBIDDEN_IMPORT_ROOTS
            ):
                violations.append(f"{path.relative_to(ROOT)} imports {imported}")

    assert violations == [], (
        "Universal task modules must expose extension points; self_improve is a "
        "capability consumer, not a core dependency:\n" + "\n".join(violations)
    )


def test_universal_task_boundary_has_an_architecture_decision() -> None:
    text = DECISION_RECORD.read_text(encoding="utf-8")

    for heading in (
        "## Decision",
        "## Layer ownership",
        "## Migration sequence",
        "## Practitioner evidence",
    ):
        assert heading in text
    assert "self_improve -> universal core" in text
    assert "universal core -/> self_improve" in text
