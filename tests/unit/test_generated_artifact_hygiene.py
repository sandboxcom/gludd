"""Focused contracts for the generated-artifact hygiene checker."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_generated_artifact_hygiene.py"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_generated_artifact_hygiene",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _messages(path: Path, kind: str) -> list[str]:
    return [finding.message for finding in checker.check_artifact(path, kind)]


def test_canonical_inventory_covers_each_generated_serialization() -> None:
    actual = {(artifact.relative_path, artifact.kind) for artifact in checker.ARTIFACTS}
    assert actual == {
        ("docs/MCP_TOOL_REFERENCE.md", "markdown"),
        ("docs/MCP_TOOLS_MANIFEST.json", "json"),
        ("docs/MCP_TOOLS_TOPICS.yml", "yaml"),
    }


@pytest.mark.parametrize(
    ("name", "kind", "content"),
    [
        ("reference.md", "markdown", b"# Reference\n\nGenerated text.\n"),
        ("manifest.json", "json", b'{"tools": []}\n'),
        ("topics.yml", "yaml", b"tools:\n  - ping\n"),
    ],
)
def test_valid_generated_artifacts_pass(
    tmp_path: Path,
    name: str,
    kind: str,
    content: bytes,
) -> None:
    assert checker.check_artifact(_write(tmp_path / name, content), kind) == []


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"# Reference  \n", "trailing whitespace"),
        (b"# Reference", "missing final newline"),
        (b"# Reference\r\n", "non-LF line ending"),
    ],
)
def test_text_postconditions_fail_closed(
    tmp_path: Path,
    content: bytes,
    expected: str,
) -> None:
    messages = _messages(_write(tmp_path / "reference.md", content), "markdown")
    assert expected in messages


def test_blank_line_before_terminal_newline_is_valid(tmp_path: Path) -> None:
    path = _write(tmp_path / "reference.md", b"# Reference\n\n")
    assert checker.check_artifact(path, "markdown") == []


def test_trailing_whitespace_reports_the_source_line(tmp_path: Path) -> None:
    path = _write(tmp_path / "reference.md", b"# Reference\nvalue\t\n")
    findings = checker.check_artifact(path, "markdown")
    assert [(finding.line, finding.message) for finding in findings] == [
        (2, "trailing whitespace"),
    ]


@pytest.mark.parametrize(
    ("name", "kind", "content", "expected"),
    [
        ("manifest.json", "json", b'{"tools": ]}\n', "invalid JSON"),
        ("topics.yml", "yaml", b"tools: [ping\n", "invalid YAML"),
    ],
)
def test_structured_artifacts_must_parse(
    tmp_path: Path,
    name: str,
    kind: str,
    content: bytes,
    expected: str,
) -> None:
    messages = _messages(_write(tmp_path / name, content), kind)
    assert any(message.startswith(expected) for message in messages)


def test_invalid_utf8_and_nul_bytes_fail_closed(tmp_path: Path) -> None:
    invalid = _write(tmp_path / "invalid.md", b"# Reference\n\xff\n")
    binary = _write(tmp_path / "binary.md", b"# Reference\n\x00\n")
    assert _messages(invalid, "markdown") == ["not valid UTF-8"]
    assert _messages(binary, "markdown") == ["contains a NUL byte"]


def test_missing_symlink_and_oversized_artifacts_fail_closed(tmp_path: Path) -> None:
    target = _write(tmp_path / "target.md", b"# Reference\n")
    link = tmp_path / "link.md"
    link.symlink_to(target)
    oversized = _write(
        tmp_path / "large.md",
        b"x" * (checker.MAX_ARTIFACT_BYTES + 1),
    )

    assert _messages(tmp_path / "missing.md", "markdown") == [
        "artifact is missing or unreadable",
    ]
    assert _messages(link, "markdown") == ["artifact must not be a symlink"]
    assert _messages(oversized, "markdown") == [
        f"artifact exceeds {checker.MAX_ARTIFACT_BYTES} byte limit",
    ]


def test_unsupported_artifact_kind_fails_closed(tmp_path: Path) -> None:
    path = _write(tmp_path / "artifact.txt", b"text\n")
    assert _messages(path, "text") == ["unsupported artifact kind: text"]


def test_inventory_uses_nul_delimited_exact_tracked_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = (
        checker.Artifact("docs/reference.md", "markdown"),
        checker.Artifact("docs/manifest.json", "json"),
    )
    observed: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=b"docs/reference.md\x00docs/manifest.json\x00",
            stderr=b"",
        )

    monkeypatch.setattr(checker.subprocess, "run", fake_run)
    discovered = checker.discover_tracked_artifacts(tmp_path, specs)

    assert observed["command"] == [
        "git",
        "-C",
        str(tmp_path),
        "ls-files",
        "-z",
        "--",
        "docs/reference.md",
        "docs/manifest.json",
    ]
    assert observed["kwargs"] == {
        "capture_output": True,
        "check": False,
        "timeout": checker.GIT_TIMEOUT_SECONDS,
    }
    assert discovered == [
        (tmp_path / "docs/reference.md", "markdown"),
        (tmp_path / "docs/manifest.json", "json"),
    ]


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess(
            ["git"],
            1,
            stdout=b"",
            stderr=b"index unavailable",
        ),
        subprocess.CompletedProcess(
            ["git"],
            0,
            stdout=b"docs/reference.md",
            stderr=b"",
        ),
        subprocess.CompletedProcess(
            ["git"],
            0,
            stdout=b"docs/other.md\x00",
            stderr=b"",
        ),
    ],
)
def test_inventory_command_and_output_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[bytes],
) -> None:
    monkeypatch.setattr(checker.subprocess, "run", lambda *args, **kwargs: result)
    specs = (checker.Artifact("docs/reference.md", "markdown"),)
    with pytest.raises(checker.InventoryError):
        checker.discover_tracked_artifacts(tmp_path, specs)


def test_inventory_timeout_and_unsafe_specs_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timed_out(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(["git"], checker.GIT_TIMEOUT_SECONDS)

    monkeypatch.setattr(checker.subprocess, "run", timed_out)
    with pytest.raises(checker.InventoryError, match="timed out"):
        checker.discover_tracked_artifacts(
            tmp_path,
            (checker.Artifact("docs/reference.md", "markdown"),),
        )

    unsafe = (checker.Artifact("../outside.json", "json"),)
    with pytest.raises(checker.InventoryError, match="unsafe"):
        checker.discover_tracked_artifacts(tmp_path, unsafe)


def test_inventory_os_error_invalid_encoding_and_invalid_specs_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = checker.Artifact("docs/reference.md", "markdown")

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise OSError("git unavailable")

    monkeypatch.setattr(checker.subprocess, "run", unavailable)
    with pytest.raises(checker.InventoryError, match="git unavailable"):
        checker.discover_tracked_artifacts(tmp_path, (spec,))

    invalid_encoding = subprocess.CompletedProcess(
        ["git"],
        0,
        stdout=b"docs/\xff.md\x00",
        stderr=b"",
    )
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: invalid_encoding,
    )
    with pytest.raises(checker.InventoryError, match="non-UTF-8"):
        checker.discover_tracked_artifacts(tmp_path, (spec,))

    invalid_specs = [
        (),
        (spec, spec),
        (checker.Artifact("docs/reference.txt", "text"),),
        tuple(spec for _ in range(checker.MAX_ARTIFACTS + 1)),
    ]
    for specs in invalid_specs:
        with pytest.raises(checker.InventoryError):
            checker.discover_tracked_artifacts(tmp_path, specs)


def test_scan_is_deterministic(tmp_path: Path) -> None:
    first = _write(tmp_path / "z.json", b"{}")
    second = _write(tmp_path / "a.md", b"bad  \n")
    findings = checker.check_artifacts(
        [(first, "json"), (second, "markdown")],
    )
    assert [(finding.path, finding.line, finding.message) for finding in findings] == [
        (str(second), 1, "trailing whitespace"),
        (str(first), None, "missing final newline"),
    ]


def test_scan_inventory_limit_and_rendering_fail_closed(tmp_path: Path) -> None:
    path = _write(tmp_path / "reference.md", b"# Reference\n")
    artifacts = [(path, "markdown")] * (checker.MAX_ARTIFACTS + 1)
    findings = checker.check_artifacts(artifacts)
    assert [finding.render() for finding in findings] == [
        f"<inventory>: artifact inventory exceeds {checker.MAX_ARTIFACTS} item limit",
    ]


def test_repository_generated_artifacts_are_tracked_and_hygienic() -> None:
    artifacts = checker.discover_tracked_artifacts(ROOT)
    assert len(artifacts) == 3
    assert checker.check_artifacts(artifacts) == []


def test_main_is_observable_for_pass_drift_and_inventory_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clean = _write(tmp_path / "reference.md", b"# Reference\n")
    monkeypatch.setattr(
        checker,
        "discover_tracked_artifacts",
        lambda root, specs=checker.ARTIFACTS: [(clean, "markdown")],
    )
    assert checker.main(["--root", str(tmp_path)]) == 0
    assert "PASS artifacts=1" in capsys.readouterr().out

    dirty = _write(tmp_path / "reference.md", b"# Reference  \n")
    monkeypatch.setattr(
        checker,
        "discover_tracked_artifacts",
        lambda root, specs=checker.ARTIFACTS: [(dirty, "markdown")],
    )
    assert checker.main(["--root", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert "reference.md:1: trailing whitespace" in output.out
    assert "FAIL violations=1 artifacts=1" in output.err

    def failed_inventory(root: Path, specs: object = checker.ARTIFACTS) -> object:
        raise checker.InventoryError("git index unavailable")

    monkeypatch.setattr(checker, "discover_tracked_artifacts", failed_inventory)
    assert checker.main(["--root", str(tmp_path)]) == 2
    assert "ERROR: git index unavailable" in capsys.readouterr().err
