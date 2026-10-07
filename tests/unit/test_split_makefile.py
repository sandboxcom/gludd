"""Focused tests for the one-time deterministic Makefile splitter."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from scripts import split_makefile


def _monolith() -> str:
    chunks = ["ROOT_VALUE := stable\n\n"]
    for index, spec in enumerate(split_makefile.PARTS[1:], start=1):
        chunks.append(f"{spec.start_marker}\n")
        chunks.append(f"target-{index}:\n\t@echo {index}\n\n")
    return "".join(chunks)


def test_partition_is_byte_exact_and_below_the_limit() -> None:
    source = _monolith()

    parts = split_makefile._partition(source)

    assert tuple(spec for spec, _ in parts) == split_makefile.PARTS
    assert "".join(text for _, text in parts) == source
    assert all(
        len(text.splitlines()) < split_makefile.MAX_LINES_EXCLUSIVE
        for _, text in parts
    )


def test_partition_rejects_missing_duplicate_and_unseparated_markers() -> None:
    source = _monolith()
    marker = split_makefile.PARTS[1].start_marker
    assert marker is not None

    with pytest.raises(split_makefile.SplitError, match="exactly once"):
        split_makefile._partition(source.replace(f"{marker}\n", "", 1))
    with pytest.raises(split_makefile.SplitError, match="exactly once"):
        split_makefile._partition(source.replace(f"{marker}\n", f"{marker}\n{marker}\n", 1))
    with pytest.raises(split_makefile.SplitError, match="blank line"):
        split_makefile._partition(source.replace(f"\n{marker}\n", f"not-blank\n{marker}\n", 1))


def test_partition_rejects_a_fragment_at_the_ceiling() -> None:
    source = ("line\n" * split_makefile.MAX_LINES_EXCLUSIVE) + _monolith()

    with pytest.raises(split_makefile.SplitError, match="line count"):
        split_makefile._partition(source)


def test_partition_rejects_markers_out_of_canonical_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parts = (
        split_makefile.PartSpec("00.mk", None),
        split_makefile.PartSpec("10.mk", "# Later"),
        split_makefile.PartSpec("20.mk", "# Earlier"),
    )
    monkeypatch.setattr(split_makefile, "PARTS", parts)

    with pytest.raises(split_makefile.SplitError, match="canonical order"):
        split_makefile._partition(
            "foundation\n\n# Earlier\nearlier:\n\t@:\n\n# Later\nlater:\n\t@:\n"
        )


def test_plan_does_not_mutate_the_monolith(tmp_path: Path) -> None:
    entrypoint = tmp_path / "Makefile"
    source = _monolith()
    entrypoint.write_text(source, encoding="utf-8")

    planned = split_makefile.split_makefile(entrypoint, apply=False)

    assert entrypoint.read_text(encoding="utf-8") == source
    assert len(planned) == len(split_makefile.PARTS) + 1
    assert not (tmp_path / "make").exists()


def test_apply_writes_an_explicit_layout_and_preserves_help(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entrypoint = tmp_path / "Makefile"
    entrypoint.write_text(_monolith(), encoding="utf-8")
    monkeypatch.setattr(split_makefile, "_make_help_output", lambda *_args, **_kwargs: b"same")

    sources = split_makefile.split_makefile(entrypoint, apply=True)

    assert sources[0] == entrypoint.resolve()
    assert [path.name for path in sources[1:]] == [
        spec.name for spec in split_makefile.PARTS
    ]
    assert "include make/00-foundation.mk" in entrypoint.read_text(encoding="utf-8")
    assert "".join(path.read_text(encoding="utf-8") for path in sources[1:]) == _monolith()
    assert split_makefile.split_makefile(entrypoint, apply=False) == sources


def test_apply_restores_the_monolith_when_help_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entrypoint = tmp_path / "Makefile"
    source = _monolith()
    entrypoint.write_text(source, encoding="utf-8")
    outputs: Iterator[bytes] = iter((b"help", b"dry", b"changed", b"dry"))
    monkeypatch.setattr(
        split_makefile,
        "_make_help_output",
        lambda *_args, **_kwargs: next(outputs),
    )

    with pytest.raises(split_makefile.SplitError, match="output changed"):
        split_makefile.split_makefile(entrypoint, apply=True)

    assert entrypoint.read_text(encoding="utf-8") == source


def test_unexpected_existing_fragment_fails_closed(tmp_path: Path) -> None:
    entrypoint = tmp_path / "Makefile"
    entrypoint.write_text(_monolith(), encoding="utf-8")
    unexpected = tmp_path / "make" / "unexpected.mk"
    unexpected.parent.mkdir()
    unexpected.write_text("unexpected:\n\t@:\n", encoding="utf-8")

    with pytest.raises(split_makefile.SplitError, match="unexpected"):
        split_makefile.split_makefile(entrypoint, apply=False)


def test_expected_existing_fragment_is_safe_during_plan(tmp_path: Path) -> None:
    entrypoint = tmp_path / "Makefile"
    entrypoint.write_text(_monolith(), encoding="utf-8")
    expected = tmp_path / "make" / split_makefile.PARTS[0].name
    expected.parent.mkdir()
    expected.write_text("# existing expected fragment\n", encoding="utf-8")

    planned = split_makefile.split_makefile(entrypoint, apply=False)

    assert len(planned) == len(split_makefile.PARTS) + 1


def test_atomic_write_removes_temporary_file_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.mk"

    def fail_replace(_source: str, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(split_makefile.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        split_makefile._atomic_write(target, "content\n")

    assert list(tmp_path.glob(".target.mk.*.tmp")) == []


def _write_split_layout(tmp_path: Path) -> Path:
    entrypoint = tmp_path / "Makefile"
    entrypoint.write_text(split_makefile._entrypoint_text(), encoding="utf-8")
    make_directory = tmp_path / "make"
    make_directory.mkdir()
    for spec in split_makefile.PARTS:
        (make_directory / spec.name).write_text("# fragment\n", encoding="utf-8")
    return entrypoint


def test_validate_split_wraps_layout_errors(tmp_path: Path) -> None:
    entrypoint = tmp_path / "Makefile"
    entrypoint.write_text(split_makefile._entrypoint_text(), encoding="utf-8")

    with pytest.raises(split_makefile.SplitError, match="missing or unreadable"):
        split_makefile._validate_split(entrypoint)


def test_validate_split_rejects_include_order_drift(tmp_path: Path) -> None:
    entrypoint = _write_split_layout(tmp_path)
    lines = entrypoint.read_text(encoding="utf-8").splitlines()
    includes = [line for line in lines if line.startswith("include ")]
    preamble = [line for line in lines if not line.startswith("include ")]
    entrypoint.write_text(
        "\n".join((*preamble, *reversed(includes), "")),
        encoding="utf-8",
    )

    with pytest.raises(split_makefile.SplitError, match="include order drifted"):
        split_makefile._validate_split(entrypoint)


def test_validate_split_rejects_fragment_at_ceiling(tmp_path: Path) -> None:
    entrypoint = _write_split_layout(tmp_path)
    oversized = tmp_path / "make" / split_makefile.PARTS[-1].name
    oversized.write_text("line\n" * split_makefile.MAX_LINES_EXCLUSIVE, encoding="utf-8")

    with pytest.raises(split_makefile.SplitError, match="exceeds split layout ceiling"):
        split_makefile._validate_split(entrypoint)


def test_make_help_output_builds_commands_and_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    responses: Iterator[subprocess.CompletedProcess[bytes]] = iter(
        (
            subprocess.CompletedProcess([], 0, stdout=b"help", stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=b"dry", stderr=b""),
            subprocess.CompletedProcess([], 3, stdout=b"", stderr=b"bad help"),
        )
    )

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return next(responses)

    monkeypatch.setattr(split_makefile.subprocess, "run", fake_run)

    assert split_makefile._make_help_output(tmp_path, dry_run=False) == b"help"
    assert split_makefile._make_help_output(tmp_path, dry_run=True) == b"dry"
    with pytest.raises(split_makefile.SplitError, match="exit 3: bad help"):
        split_makefile._make_help_output(tmp_path, dry_run=False)

    assert commands == [
        ["make", "--no-print-directory", "help"],
        ["make", "--no-print-directory", "-n", "help"],
        ["make", "--no-print-directory", "help"],
    ]


def test_cli_reports_plan_and_invalid_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    entrypoint = tmp_path / "Makefile"
    entrypoint.write_text(_monolith(), encoding="utf-8")
    assert split_makefile.main(["--entrypoint", str(entrypoint)]) == 0
    assert "PLANNED" in capsys.readouterr().out

    entrypoint.write_text("broken:\n\t@:\n", encoding="utf-8")
    assert split_makefile.main(["--entrypoint", str(entrypoint)]) == 2
    assert "ERROR" in capsys.readouterr().out
