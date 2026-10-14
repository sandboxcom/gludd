"""Per-invocation native Galaxy evidence and diagnostic alias ownership."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, TypedDict, Unpack, cast

import pytest

if TYPE_CHECKING:
    import ansible_runtime_artifacts as owner
    import check_molecule_yaml as guard
    import stream_command as stream
else:
    from scripts import ansible_runtime_artifacts as owner
    from scripts import check_molecule_yaml as guard
    from scripts import stream_command as stream


class _CommandArguments(TypedDict):
    max_seconds: int
    environment: Mapping[str, str]
    cwd: Path


def _command(output: str, exit_code: int = 0) -> list[str]:
    return [sys.executable, "-c", f"print({output!r}); raise SystemExit({exit_code})"]


def _checked(state: Path, output: str, exit_code: int = 0) -> None:
    owner._checked_collection_command(
        _command(output, exit_code), state, 3, environment=dict(os.environ), cwd=state,
    )


def _receipts(state: Path) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((state / "observed" / "gludd-collection-stage").glob("*.json"))
        if path.name != "current.json"
    ]


def _log(receipt: dict[str, object]) -> Path:
    path = receipt["log_path"]
    assert isinstance(path, str)
    return Path(path)


def test_repeated_real_children_retain_their_own_logs_and_latest_diagnostics(tmp_path: Path) -> None:
    _checked(tmp_path, "first owned invocation")
    first_receipts = _receipts(tmp_path)
    assert len(first_receipts) == 1
    first_log = _log(first_receipts[0])
    assert first_log != tmp_path / "galaxy.log"
    first_content = first_log.read_bytes()

    _checked(tmp_path, "second owned invocation")

    receipts = _receipts(tmp_path)
    assert len(receipts) == 2
    logs = {_log(receipt) for receipt in receipts}
    assert len(logs) == 2
    assert first_log.read_bytes() == first_content == b"first owned invocation\n"
    assert {path.read_text(encoding="utf-8") for path in logs} == {
        "first owned invocation\n", "second owned invocation\n",
    }
    assert all(receipt["state"] == "passed" and receipt["exit_code"] == 0 for receipt in receipts)
    assert (tmp_path / "galaxy.log").read_text(encoding="utf-8") == "second owned invocation\n"


@pytest.mark.parametrize(("output", "exit_code"), [("[WARNING]: owned warning", 0), ("owned failure", 7)])
def test_warning_or_failure_keeps_actual_output_and_does_not_poison_next_invocation(
    tmp_path: Path, output: str, exit_code: int,
) -> None:
    _checked(tmp_path, "first clean invocation")
    with pytest.raises(ValueError, match=rf"native Galaxy collection command failed: rc={exit_code}$"):
        _checked(tmp_path, output, exit_code)
    assert (tmp_path / "galaxy.log").read_text(encoding="utf-8") == f"{output}\n"
    failed_log = next(_log(receipt) for receipt in _receipts(tmp_path) if _log(receipt).read_text() == f"{output}\n")
    _checked(tmp_path, "last clean invocation")
    assert failed_log.read_text(encoding="utf-8") == f"{output}\n"
    assert len(_receipts(tmp_path)) == 3
    assert (tmp_path / "galaxy.log").read_text(encoding="utf-8") == "last clean invocation\n"


@pytest.mark.parametrize(
    ("output", "other_output", "rejected"),
    [("owned clean output", "[WARNING]: another invocation", False),
     ("[WARNING]: owned diagnostic", "another clean invocation", True)],
)
def test_validation_uses_actual_invocation_output_when_shared_diagnostics_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, other_output: str, *, rejected: bool,
) -> None:
    original = owner._run_collection_command

    def change_alias(command: list[str], log: Path, **arguments: Unpack[_CommandArguments]) -> int:
        result = original(command, log, **arguments)
        (tmp_path / "galaxy.log").write_text(other_output, encoding="utf-8")
        return result

    monkeypatch.setattr(owner, "_run_collection_command", change_alias)
    if rejected:
        with pytest.raises(ValueError, match=r"native Galaxy collection command failed: rc=0$"):
            _checked(tmp_path, output)
    else:
        _checked(tmp_path, output)
    receipts = _receipts(tmp_path)
    assert len(receipts) == 1
    assert _log(receipts[0]).read_text(encoding="utf-8") == f"{output}\n"


def test_latest_diagnostics_replace_symlink_without_modifying_its_target(tmp_path: Path) -> None:
    other_log = tmp_path / "other-owner.log"
    other_log.write_text("other owner's immutable evidence\n", encoding="utf-8")
    (tmp_path / "galaxy.log").symlink_to(other_log)
    _checked(tmp_path, "current invocation")
    assert not (tmp_path / "galaxy.log").is_symlink()
    assert (tmp_path / "galaxy.log").read_text(encoding="utf-8") == "current invocation\n"
    assert other_log.read_text(encoding="utf-8") == "other owner's immutable evidence\n"


@pytest.mark.parametrize("output", ["fresh output", "[WARNING]: fresh diagnostic"])
def test_invocation_is_validated_before_success_or_warning_diagnostics_are_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str,
) -> None:
    diagnostic = tmp_path / "galaxy.log"
    diagnostic.write_text("earlier diagnostics\n", encoding="utf-8")
    original = guard.validate_native_lint_diagnostics
    validated: list[Path] = []

    def validate(log: Path) -> bool:
        assert log != diagnostic
        assert diagnostic.read_text(encoding="utf-8") == "earlier diagnostics\n"
        validated.append(log)
        return original(log)

    monkeypatch.setattr(guard, "validate_native_lint_diagnostics", validate)
    if output.startswith("[WARNING]"):
        with pytest.raises(ValueError, match=r"native Galaxy collection command failed: rc=0$"):
            _checked(tmp_path, output)
    else:
        _checked(tmp_path, output)
    assert len(validated) == 1
    assert validated[0].read_text(encoding="utf-8") == f"{output}\n"
    assert diagnostic.read_text(encoding="utf-8") == f"{output}\n"


@pytest.mark.parametrize("failure", ["copy", "publication"])
def test_diagnostic_io_failure_preserves_evidence_and_alias_without_orphaned_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    diagnostic = tmp_path / "galaxy.log"
    diagnostic.write_text("earlier complete diagnostics\n", encoding="utf-8")
    if failure == "copy":
        def refuse_copy(source: BinaryIO, output: BinaryIO, length: int = 0) -> None:
            output.write(source.read(2))
            raise OSError("diagnostic copy refused")

        monkeypatch.setattr(shutil, "copyfileobj", refuse_copy)
    else:
        original_replace = os.replace

        def refuse_publication(source: str | Path, destination: str | Path) -> None:
            if Path(destination) == diagnostic:
                raise OSError("diagnostic publication refused")
            original_replace(source, destination)

        monkeypatch.setattr(os, "replace", refuse_publication)
    with pytest.raises(OSError, match=f"diagnostic {failure} refused"):
        _checked(tmp_path, "complete current invocation")
    assert diagnostic.read_text(encoding="utf-8") == "earlier complete diagnostics\n"
    receipts = _receipts(tmp_path)
    assert len(receipts) == 1
    assert _log(receipts[0]).read_text(encoding="utf-8") == "complete current invocation\n"
    assert not list(tmp_path.glob(".galaxy.log.*"))


def test_direct_collection_command_preserves_explicit_log_output_contract(tmp_path: Path) -> None:
    log = tmp_path / "explicit-diagnostic.log"
    assert owner._run_collection_command(
        _command("explicit output", 6), log, max_seconds=3, environment=dict(os.environ), cwd=tmp_path,
    ) == 6
    assert log.read_text(encoding="utf-8") == "explicit output\n"


def test_observer_still_refuses_existing_evidence_without_starting_a_child(tmp_path: Path) -> None:
    existing = tmp_path / "old-evidence.log"
    existing.write_text("immutable old output\n", encoding="utf-8")
    marker = tmp_path / "child-started"
    result = stream.stream_command(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
        existing, observed_root=tmp_path / "observed", label="gludd-collection-stage",
        max_seconds=3, environment=dict(os.environ), cwd=tmp_path,
    )
    assert result == 125
    assert existing.read_text(encoding="utf-8") == "immutable old output\n"
    assert not marker.exists()
