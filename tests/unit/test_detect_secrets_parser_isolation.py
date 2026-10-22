"""Pinned native grammar must not execute scan extensions during preflight."""

from __future__ import annotations

import importlib.abc
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

import pytest

from tests.unit.test_detect_secrets_full_scan import ScanHarness
from tests.unit.test_detect_secrets_full_scan import full_scan as full_scan


def _extension_import_tripwire(
    root: Path, parser_extension_kind: str,
) -> tuple[Path, Path]:
    marker = root / f"{parser_extension_kind}-imported.txt"
    extension = root / f"custom {parser_extension_kind}.py"
    extension.write_text(
        "from pathlib import Path\n"
        "def fixture_filter(filename: str = \"\") -> bool:\n"
        "    return False\n"
        f"Path({str(marker)!r}).write_text(\"unexpected extension import\", encoding=\"utf-8\")\n"
        "raise AssertionError(\"raw grammar validation executed a custom extension\")\n",
        encoding="utf-8",
    )
    return extension, marker


@pytest.mark.parametrize("parser_extension_kind", ["plugin", "filter"])
def test_raw_native_grammar_never_imports_custom_extension_files(
    full_scan: ScanHarness, parser_extension_kind: str,
) -> None:
    """Run real grammar validation; only the established child runner is mocked."""
    root = full_scan.baseline.parent
    extension, marker = _extension_import_tripwire(root, parser_extension_kind)
    source_bytes = extension.read_bytes()
    target = root / "synthetic input.txt"
    target.write_text("synthetic nonsecret input\n", encoding="utf-8")
    if parser_extension_kind == "plugin":
        arguments = ("--plugin", str(extension), str(target))
    else:
        arguments = ("--filter", f"file://{extension}::fixture_filter", str(target))
    assert not marker.exists()
    assert full_scan.module.run_readonly_scan(
        baseline=full_scan.baseline, filenames=arguments,
        executable=str(full_scan.native), runner=full_scan.runner, scan_cores=2,
    ) == 0
    assert not marker.exists()
    assert extension.read_bytes() == source_bytes
    full_scan.assert_full_argv(arguments)
    full_scan.assert_preserved()


class _DetectSecretsImportTripwire(importlib.abc.MetaPathFinder):
    """Fail an actual package import without replacing its parser or public API."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if fullname == "detect_secrets" or fullname.startswith("detect_secrets."):
            self.attempts.append(fullname)
            raise AssertionError("hook-mode API imported the native scan grammar")
        return None


def test_default_hook_api_never_imports_detect_secrets(
    full_scan: ScanHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evict cached package modules so the import fence cannot pass from caching."""
    tripwire = _DetectSecretsImportTripwire()
    arguments = ("src/hook-only.py", "src/file with spaces.py")
    with monkeypatch.context() as guarded:
        for name in tuple(sys.modules):
            if name == "detect_secrets" or name.startswith("detect_secrets."):
                guarded.delitem(sys.modules, name)
        guarded.setattr(sys, "meta_path", [tripwire, *sys.meta_path])
        assert full_scan.module.run_readonly_scan(
            baseline=full_scan.baseline, filenames=arguments, runner=full_scan.runner,
        ) == 0
        assert tripwire.attempts == []
        assert not any(
            name == "detect_secrets" or name.startswith("detect_secrets.")
            for name in sys.modules
        )
    assert len(full_scan.runner.calls) == 1
    call = full_scan.runner.calls[0]
    assert call.command == (
        "detect-secrets-hook", "--baseline", str(call.disposable), *arguments,
    )
    full_scan.assert_preserved()
