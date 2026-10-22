"""Public full-scan regression contract for the read-only secrets adapter."""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/detect_secrets_readonly.py"
Runner = Callable[..., subprocess.CompletedProcess[str]]


class Finding(TypedDict):
    filename: NotRequired[str]
    type: str
    hashed_secret: str
    line_number: int
    is_verified: bool


class BaselinePayload(TypedDict):
    results: dict[str, list[Finding]]
    generated_at: str


class ScanAPI(Protocol):
    def run_readonly_scan(
        self, *, baseline: Path, filenames: Sequence[str], runner: Runner,
        executable: str = "detect-secrets-hook", scan_cores: int | None = None,
    ) -> int:
        """Run the real public adapter with a confined upstream runner."""
        ...

    def main(self, argv: Sequence[str] | None = None) -> int:
        """Expose the original public CLI result."""
        ...


def _finding(
    digest: str = "fixture-digest-a", *, filename: str = "src/known.py",
    detector: str = "Synthetic Detector", line: int = 7,
) -> Finding:
    return {
        "filename": filename, "type": detector, "hashed_secret": digest,
        "line_number": line, "is_verified": False,
    }


def _payload(findings: list[Finding] | None = None) -> BaselinePayload:
    return {
        "results": {"src/known.py": [_finding()] if findings is None else findings},
        "generated_at": "synthetic-before",
    }


def _encoded(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")


@dataclass(frozen=True)
class ScanCall:
    command: tuple[str, ...]
    check: bool
    capture_output: bool
    text: bool
    disposable: Path
    initial_bytes: bytes
    mode: int


@dataclass
class SyntheticRunner:
    calls: list[ScanCall] = field(default_factory=list)
    updated: bytes | None = None
    status: int = 0
    error: bool = False

    def __call__(
        self, command: list[str], *, check: bool, capture_output: bool, text: bool,
    ) -> subprocess.CompletedProcess[str]:
        disposable = Path(command[command.index("--baseline") + 1])
        self.calls.append(ScanCall(
            tuple(command), check, capture_output, text, disposable,
            disposable.read_bytes(), stat.S_IMODE(disposable.stat().st_mode),
        ))
        if self.error:
            raise OSError("synthetic scanner unavailable")
        if self.updated is not None:
            disposable.write_bytes(self.updated)
        return subprocess.CompletedProcess(
            command, self.status, "synthetic scanner stdout\n", "synthetic scanner stderr\n",
        )


@dataclass
class ScanHarness:
    module: ScanAPI
    runner: SyntheticRunner
    baseline: Path
    original: bytes
    mode: int
    inode: int
    private: Path
    binaries: Path
    native: Path
    hook: Path

    def assert_preserved(self) -> None:
        assert self.baseline.read_bytes() == self.original
        assert stat.S_IMODE(self.baseline.stat().st_mode) == self.mode
        assert self.baseline.stat().st_ino == self.inode
        assert not list(self.private.iterdir())
        for call in self.runner.calls:
            assert call.disposable.parent == self.private
            assert call.disposable != self.baseline
            assert call.initial_bytes == self.original
            assert call.mode == 0o600
            assert call.check is False
            assert call.capture_output is True
            assert call.text is True
            assert not call.disposable.exists()

    def assert_full_argv(self, filenames: Sequence[str], *, cores: int = 2) -> None:
        assert len(self.runner.calls) == 1
        call = self.runner.calls[0]
        assert call.command == (
            str(self.native), "--cores", str(cores), "scan", "--baseline",
            str(call.disposable), *filenames,
        )


@pytest.fixture
def full_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScanHarness:
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    binaries = tmp_path / "bin"
    binaries.mkdir()
    native = binaries / "detect-secrets"
    hook = binaries / "detect-secrets-hook"
    for executable in (native, hook):
        executable.write_text("synthetic placeholder; never executed\n", encoding="utf-8")
        executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(binaries))
    runner = SyntheticRunner()
    # Install only the actual runner before import so the public API's existing
    # default argument binds this spy. No scanner or API function is replaced.
    monkeypatch.setattr(subprocess, "run", runner)
    spec = importlib.util.spec_from_file_location("gludd_readonly_full_scan", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = tmp_path / ".secrets.baseline"
    original = _encoded(_payload())
    baseline.write_bytes(original)
    baseline.chmod(0o640)
    return ScanHarness(
        cast(ScanAPI, module), runner, baseline, original,
        stat.S_IMODE(baseline.stat().st_mode), baseline.stat().st_ino,
        private, binaries, native, hook,
    )


_ADDED_FINDINGS = (
    pytest.param([_finding(), _finding("fixture-digest-b")], id="new-digest"),
    pytest.param([_finding(), _finding(filename="src/other.py")], id="new-filename"),
    pytest.param([_finding(), _finding(detector="Other Synthetic Detector")], id="new-detector"),
    pytest.param([_finding(), _finding()], id="duplicate-occurrence"),
)


@pytest.mark.parametrize("full_scan_findings", _ADDED_FINDINGS)
def test_successful_full_scan_rejects_added_identity_or_occurrence(
    full_scan: ScanHarness, full_scan_findings: list[Finding],
) -> None:
    filenames = ("src/file with spaces.py", "--exclude-files", "^fixture-only$", "--all-files")
    full_scan.runner.updated = _encoded(_payload(full_scan_findings))
    assert full_scan.module.run_readonly_scan(
        baseline=full_scan.baseline, filenames=filenames, executable=str(full_scan.native),
        runner=full_scan.runner, scan_cores=2,
    ) == 1
    full_scan.assert_full_argv(filenames)
    full_scan.assert_preserved()


def test_counter_comparison_rejects_duplicate_even_when_total_count_is_unchanged(
    full_scan: ScanHarness,
) -> None:
    initial = _payload([_finding(), _finding("fixture-digest-b")])
    full_scan.original = _encoded(initial)
    full_scan.baseline.write_bytes(full_scan.original)
    full_scan.runner.updated = _encoded(_payload([
        _finding("fixture-digest-b"), _finding("fixture-digest-b"),
    ]))
    assert full_scan.module.run_readonly_scan(
        baseline=full_scan.baseline, filenames=(), executable=str(full_scan.native),
        runner=full_scan.runner, scan_cores=2,
    ) == 1
    full_scan.assert_full_argv(())
    full_scan.assert_preserved()


def test_filename_fallback_uses_result_path_as_identity(full_scan: ScanHarness) -> None:
    entry = _finding()
    del entry["filename"]
    full_scan.original = _encoded(_payload([entry]))
    full_scan.baseline.write_bytes(full_scan.original)
    full_scan.runner.updated = _encoded({
        "results": {"src/moved.py": [entry]}, "generated_at": "synthetic-after",
    })
    assert full_scan.module.run_readonly_scan(
        baseline=full_scan.baseline, filenames=(), executable=str(full_scan.native),
        runner=full_scan.runner, scan_cores=2,
    ) == 1
    full_scan.assert_preserved()


_CLEAN_UPDATES = (
    pytest.param({
        "results": {"src/known.py": [{**_finding(line=99), "is_verified": True}]},
        "generated_at": "synthetic-after",
    }, id="metadata-only"),
    pytest.param({
        "results": {"metadata-bucket.py": [_finding()]},
        "generated_at": "synthetic-after",
    }, id="explicit-filename-survives-bucket-move"),
    pytest.param({"results": {"src/known.py": []}}, id="finding-removal"),
    pytest.param({"results": {}}, id="all-findings-removed"),
)


@pytest.mark.parametrize("full_scan_update", _CLEAN_UPDATES)
def test_successful_full_scan_allows_metadata_or_removal(
    full_scan: ScanHarness, full_scan_update: object,
) -> None:
    full_scan.runner.updated = _encoded(full_scan_update)
    assert full_scan.module.run_readonly_scan(
        baseline=full_scan.baseline, filenames=(), executable=str(full_scan.native),
        runner=full_scan.runner, scan_cores=2,
    ) == 0
    full_scan.assert_full_argv(())
    full_scan.assert_preserved()


@pytest.mark.parametrize("full_scan_native_status", [1, 2, 3, 7])
def test_native_nonzero_status_precedes_postscan_inventory_validation(
    full_scan: ScanHarness, capsys: pytest.CaptureFixture[str], full_scan_native_status: int,
) -> None:
    full_scan.runner.status = full_scan_native_status
    full_scan.runner.updated = b"not valid JSON"
    assert full_scan.module.run_readonly_scan(
        baseline=full_scan.baseline, filenames=(), executable=str(full_scan.native),
        runner=full_scan.runner, scan_cores=2,
    ) == full_scan_native_status
    captured = capsys.readouterr()
    assert captured.out == "synthetic scanner stdout\n"
    assert captured.err == "synthetic scanner stderr\n"
    full_scan.assert_preserved()


_MALFORMED_BASELINES = (
    pytest.param(b"not valid JSON", id="invalid-json"),
    pytest.param(_encoded([]), id="nonobject-root"),
    pytest.param(_encoded({"results": []}), id="nonobject-results"),
    pytest.param(_encoded({"results": {"src/known.py": {}}}), id="nonlist-findings"),
    pytest.param(_encoded({"results": {"src/known.py": [[]]}}), id="nonobject-finding"),
    pytest.param(_encoded({
        "results": {"src/known.py": [{**_finding(), "type": ""}]},
    }), id="empty-detector"),
)


@pytest.mark.parametrize("full_scan_malformed", _MALFORMED_BASELINES)
@pytest.mark.parametrize("full_scan_phase", ["before", "after"])
def test_full_scan_rejects_malformed_inventory_and_always_cleans(
    full_scan: ScanHarness, full_scan_malformed: bytes, full_scan_phase: str,
) -> None:
    if full_scan_phase == "before":
        full_scan.original = full_scan_malformed
        full_scan.baseline.write_bytes(full_scan.original)
    else:
        full_scan.runner.updated = full_scan_malformed
    with pytest.raises(ValueError):
        full_scan.module.run_readonly_scan(
            baseline=full_scan.baseline, filenames=(), executable=str(full_scan.native),
            runner=full_scan.runner, scan_cores=2,
        )
    assert len(full_scan.runner.calls) == (0 if full_scan_phase == "before" else 1)
    full_scan.assert_preserved()


def test_runner_oserror_propagates_and_removes_private_copy(full_scan: ScanHarness) -> None:
    full_scan.runner.error = True
    with pytest.raises(OSError, match="synthetic scanner unavailable"):
        full_scan.module.run_readonly_scan(
            baseline=full_scan.baseline, filenames=(), executable=str(full_scan.native),
            runner=full_scan.runner, scan_cores=2,
        )
    assert len(full_scan.runner.calls) == 1
    full_scan.assert_preserved()


@pytest.mark.parametrize("full_scan_invalid_cores", [0, -1, True, False])
def test_invalid_core_count_is_rejected_before_temp_creation_or_child(
    full_scan: ScanHarness, monkeypatch: pytest.MonkeyPatch, full_scan_invalid_cores: int,
) -> None:
    blocker = full_scan.private.parent / "temp-creation-tripwire"
    blocker.write_bytes(b"not a directory")
    monkeypatch.setattr(tempfile, "tempdir", str(blocker))
    with pytest.raises(ValueError, match="core"):
        full_scan.module.run_readonly_scan(
            baseline=full_scan.baseline, filenames=(), executable=str(full_scan.native),
            runner=full_scan.runner, scan_cores=full_scan_invalid_cores,
        )
    assert blocker.read_bytes() == b"not a directory"
    assert full_scan.runner.calls == []
    full_scan.assert_preserved()


@pytest.mark.parametrize("full_scan_none_binding", ["omitted", "explicit-none"])
def test_none_preserves_exact_hook_argv_and_status3_metadata_behavior(
    full_scan: ScanHarness, full_scan_none_binding: str,
) -> None:
    full_scan.runner.status = 3
    full_scan.runner.updated = _encoded(_payload([_finding(line=22)]))
    arguments = ("src/one.py", "src/file with spaces.py")
    if full_scan_none_binding == "omitted":
        result = full_scan.module.run_readonly_scan(
            baseline=full_scan.baseline, filenames=arguments, runner=full_scan.runner,
        )
    else:
        result = full_scan.module.run_readonly_scan(
            baseline=full_scan.baseline, filenames=arguments, runner=full_scan.runner,
            scan_cores=None,
        )
    assert result == 0
    assert len(full_scan.runner.calls) == 1
    call = full_scan.runner.calls[0]
    assert call.command == (
        "detect-secrets-hook", "--baseline", str(call.disposable), *arguments,
    )
    full_scan.assert_preserved()


def test_cli_full_scan_selects_native_executable_and_preserves_all_arg_tokens(
    full_scan: ScanHarness,
) -> None:
    arguments = ("--all-files", "--exclude-files", "^synthetic-only$", "src/spaced name.py")
    assert full_scan.module.main([
        "--baseline", str(full_scan.baseline), "--scan-cores", "3", "--", *arguments,
    ]) == 0
    full_scan.assert_full_argv(arguments, cores=3)
    full_scan.assert_preserved()


def test_cli_default_retains_hook_selection_and_native_argv(full_scan: ScanHarness) -> None:
    assert full_scan.module.main(["--baseline", str(full_scan.baseline), "src/one.py"]) == 0
    assert len(full_scan.runner.calls) == 1
    call = full_scan.runner.calls[0]
    assert call.command == (str(full_scan.hook), "--baseline", str(call.disposable), "src/one.py")
    full_scan.assert_preserved()


def test_cli_full_scan_missing_executable_does_not_fall_back_to_hook(
    full_scan: ScanHarness, capsys: pytest.CaptureFixture[str],
) -> None:
    full_scan.native.unlink()
    assert full_scan.hook.exists()
    assert full_scan.module.main([
        "--baseline", str(full_scan.baseline), "--scan-cores", "2",
    ]) == 2
    assert "detect-secrets" in capsys.readouterr().err
    assert full_scan.runner.calls == []
    full_scan.assert_preserved()


@pytest.mark.parametrize("full_scan_cli_cores", ["0", "-1", "bad", ""])
def test_cli_invalid_cores_fail_before_runner(
    full_scan: ScanHarness, capsys: pytest.CaptureFixture[str], full_scan_cli_cores: str,
) -> None:
    try:
        result = full_scan.module.main([
            "--baseline", str(full_scan.baseline), "--scan-cores", full_scan_cli_cores,
        ])
    except SystemExit as exc:
        assert type(exc.code) is int
        result = exc.code
    assert result == 2
    assert "core" in capsys.readouterr().err
    assert full_scan.runner.calls == []
    full_scan.assert_preserved()


@pytest.mark.parametrize("full_scan_cli_phase", ["before", "after"])
def test_cli_malformed_inventory_returns2_and_cleans(
    full_scan: ScanHarness, capsys: pytest.CaptureFixture[str], full_scan_cli_phase: str,
) -> None:
    if full_scan_cli_phase == "before":
        full_scan.original = b"not valid JSON"
        full_scan.baseline.write_bytes(full_scan.original)
    else:
        full_scan.runner.updated = b"not valid JSON"
    assert full_scan.module.main([
        "--baseline", str(full_scan.baseline), "--scan-cores", "2",
    ]) == 2
    assert capsys.readouterr().err
    assert len(full_scan.runner.calls) == (0 if full_scan_cli_phase == "before" else 1)
    full_scan.assert_preserved()


def test_cli_runner_oserror_returns2_and_cleans(
    full_scan: ScanHarness, capsys: pytest.CaptureFixture[str],
) -> None:
    full_scan.runner.error = True
    assert full_scan.module.main([
        "--baseline", str(full_scan.baseline), "--scan-cores", "2",
    ]) == 2
    assert "synthetic scanner unavailable" in capsys.readouterr().err
    assert len(full_scan.runner.calls) == 1
    full_scan.assert_preserved()


@pytest.mark.parametrize(
    "full_scan_baseline_option",
    ["--baseline", "--baseline=", "--basel", "--basel="],
)
def test_full_scan_refuses_native_baseline_override_before_child(
    full_scan: ScanHarness, full_scan_baseline_option: str,
) -> None:
    foreign = full_scan.private.parent / "foreign-baseline.json"
    original = _encoded(_payload())
    foreign.write_bytes(original)
    arguments = (
        (full_scan_baseline_option + str(foreign),)
        if full_scan_baseline_option.endswith("=")
        else (full_scan_baseline_option, str(foreign))
    )
    with pytest.raises(ValueError, match="disposable baseline"):
        full_scan.module.run_readonly_scan(
            baseline=full_scan.baseline, filenames=arguments,
            executable=str(full_scan.native), runner=full_scan.runner, scan_cores=2,
        )
    assert full_scan.runner.calls == []
    assert foreign.read_bytes() == original
    full_scan.assert_preserved()


@pytest.mark.parametrize(
    "full_scan_literal_arguments",
    [
        ("--exclude-files=--baseline",),
        ("--", "--baseline", "synthetic-filename.py"),
    ],
    ids=["option-value", "literal-filename"],
)
def test_full_scan_preserves_literal_tokens_without_baseline_override(
    full_scan: ScanHarness, full_scan_literal_arguments: tuple[str, ...],
) -> None:
    assert full_scan.module.run_readonly_scan(
        baseline=full_scan.baseline, filenames=full_scan_literal_arguments,
        executable=str(full_scan.native), runner=full_scan.runner, scan_cores=2,
    ) == 0
    full_scan.assert_full_argv(full_scan_literal_arguments)
    full_scan.assert_preserved()


@pytest.mark.parametrize(
    "full_scan_invalid_arguments",
    [
        ("--help",),
        ("--unsupported-native-option=synthetic-private-value",),
        ("--base=synthetic-private-value",),
    ],
    ids=["help-is-not-scan", "invalid-option", "ambiguous-option"],
)
def test_full_scan_parse_exit_is_content_free_refusal_before_child(
    full_scan: ScanHarness, capsys: pytest.CaptureFixture[str],
    full_scan_invalid_arguments: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="native full-scan arguments"):
        full_scan.module.run_readonly_scan(
            baseline=full_scan.baseline, filenames=full_scan_invalid_arguments,
            executable=str(full_scan.native), runner=full_scan.runner, scan_cores=2,
        )
    captured = capsys.readouterr()
    assert "synthetic-private-value" not in captured.out + captured.err
    assert "usage:" not in captured.out + captured.err
    assert full_scan.runner.calls == []
    full_scan.assert_preserved()


@pytest.mark.parametrize(
    "full_scan_noninventory_arguments",
    [
        ("--string",),
        ("--string=synthetic-private-value",),
        ("--string", "synthetic-private-value"),
        ("--str=synthetic-private-value",),
        ("--list-all-plugins",),
        ("--list-all",),
        ("--only-allowlisted",),
        ("--only-allowlist",),
    ],
    ids=[
        "string-stdin", "string-equals", "string-value", "string-abbreviation",
        "plugin-list", "plugin-list-abbreviation", "allowlisted", "allowlisted-abbreviation",
    ],
)
def test_full_scan_noninventory_mode_refuses_before_child(
    full_scan: ScanHarness, capsys: pytest.CaptureFixture[str],
    full_scan_noninventory_arguments: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="refresh the private inventory"):
        full_scan.module.run_readonly_scan(
            baseline=full_scan.baseline, filenames=full_scan_noninventory_arguments,
            executable=str(full_scan.native), runner=full_scan.runner, scan_cores=2,
        )
    captured = capsys.readouterr()
    assert "synthetic-private-value" not in captured.out + captured.err
    assert full_scan.runner.calls == []
    full_scan.assert_preserved()


@pytest.mark.parametrize(
    "full_scan_inventory_arguments",
    [
        ("--string=",),
        ("--exclude-lines=--string",),
        ("--exclude-lines=--list-all-plugins",),
        ("--exclude-lines=--only-allowlisted",),
        ("--", "--string", "--list-all-plugins", "--only-allowlisted"),
    ],
    ids=["empty-string-falls-through", "string-regex", "plugin-regex", "allowlist-regex", "filenames"],
)
def test_full_scan_inventory_arguments_keep_native_meaning(
    full_scan: ScanHarness, full_scan_inventory_arguments: tuple[str, ...],
) -> None:
    assert full_scan.module.run_readonly_scan(
        baseline=full_scan.baseline, filenames=full_scan_inventory_arguments,
        executable=str(full_scan.native), runner=full_scan.runner, scan_cores=2,
    ) == 0
    full_scan.assert_full_argv(full_scan_inventory_arguments)
    full_scan.assert_preserved()
