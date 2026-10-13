"""Output contracts for disposable detect-secrets baseline refreshes."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / "scripts" / "detect_secrets_readonly.py"
DIGEST_FIELD = "hashed_secret"
DIAGNOSTIC = (
    "detect-secrets: disposable baseline copy updated; no new findings. "
    "Canonical baseline unchanged.\n"
)


def _load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("readonly_diagnostics_subject", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _native_update(disposable: Path) -> str:
    return (
        "The baseline file was updated.\n"
        "Probably to keep line numbers of secrets up-to-date.\n"
        f"Please `git add {disposable}`, thank you.\n\n\n"
    )


def _baseline(tmp_path: Path) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "results": {
                    "source.py": [
                        {
                            "filename": "source.py",
                            "type": "Secret Keyword",
                            DIGEST_FIELD: "known",
                            "line_number": 7,
                        },
                    ],
                },
            },
        ),
        encoding="utf-8",
    )
    return path


def test_metadata_refresh_replaces_only_exact_native_update_instructions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A clean refresh preserves other messages and explains the disposable copy."""
    module = _load_hook()
    baseline = _baseline(tmp_path)
    original = baseline.read_bytes()
    observed: list[Path] = []
    prefix = "native progress\nPlease `git add other-file`, thank you.\n"
    suffix = "native trailing diagnostic without a newline"
    stderr = "native warning\nYour baseline file is unstaged.\n"

    def runner(
        command: list[str], *, check: bool,
        capture_output: bool = False, text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        disposable = Path(command[command.index("--baseline") + 1])
        observed.append(disposable)
        updated = json.loads(disposable.read_text(encoding="utf-8"))
        updated["results"]["source.py"][0]["line_number"] = 9
        disposable.write_text(json.dumps(updated), encoding="utf-8")
        stdout = prefix + _native_update(disposable) + suffix
        if not capture_output:
            print(stdout, end="")
            print(stderr, end="", file=module.sys.stderr)
        return subprocess.CompletedProcess(
            command, 3, stdout=stdout if capture_output else None,
            stderr=stderr if capture_output else None,
        )

    assert module.run_readonly_scan(baseline=baseline, filenames=(), runner=runner) == 0
    captured = capsys.readouterr()
    assert captured.out == prefix + DIAGNOSTIC + suffix
    assert captured.err == stderr
    assert baseline.read_bytes() == original
    assert not observed[0].exists()


@pytest.mark.parametrize("status", [0, 1, 2, 7])
def test_non_update_statuses_forward_all_native_output_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], status: int,
) -> None:
    """Even familiar metadata text must remain untouched on other statuses."""
    module = _load_hook()
    baseline = _baseline(tmp_path)
    observed: list[str] = []
    stderr = "scanner failure diagnostic without a newline"

    def runner(
        command: list[str], *, check: bool,
        capture_output: bool = False, text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del check, text
        disposable = Path(command[command.index("--baseline") + 1])
        stdout = "native finding diagnostic\n" + _native_update(disposable)
        observed.append(stdout)
        if not capture_output:
            print(stdout, end="")
            print(stderr, end="", file=module.sys.stderr)
        return subprocess.CompletedProcess(
            command, status, stdout=stdout if capture_output else None,
            stderr=stderr if capture_output else None,
        )

    assert module.run_readonly_scan(baseline=baseline, filenames=(), runner=runner) == status
    captured = capsys.readouterr()
    assert captured.out == observed[0]
    assert captured.err == stderr


@pytest.mark.parametrize("addition", ["digest", "detector", "path", "duplicate", "malformed"])
def test_update_failures_keep_native_findings_and_instructions_visible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], addition: str,
) -> None:
    """Every added identity, occurrence or malformed result stays fail-closed."""
    module = _load_hook()
    baseline = _baseline(tmp_path)
    observed: list[str] = []
    stderr = "native error\n"

    def runner(
        command: list[str], *, check: bool,
        capture_output: bool = False, text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del check, text
        disposable = Path(command[command.index("--baseline") + 1])
        changed = json.loads(disposable.read_text(encoding="utf-8"))
        entry = dict(changed["results"]["source.py"][0])
        if addition == "digest":
            entry[DIGEST_FIELD] = "new-finding"
        elif addition == "detector":
            entry["type"] = "Different Detector"
        elif addition == "path":
            entry["filename"] = "other.py"
        changed["results"]["source.py"].append(entry)
        disposable.write_text(
            "not-json" if addition == "malformed" else json.dumps(changed),
            encoding="utf-8",
        )
        stdout = "native finding diagnostic\n" + _native_update(disposable)
        observed.append(stdout)
        if not capture_output:
            print(stdout, end="")
            print(stderr, end="", file=module.sys.stderr)
        return subprocess.CompletedProcess(
            command, 3, stdout=stdout if capture_output else None,
            stderr=stderr if capture_output else None,
        )

    assert module.run_readonly_scan(baseline=baseline, filenames=(), runner=runner) == 3
    captured = capsys.readouterr()
    assert captured.out == observed[0]
    assert captured.err == stderr


def test_update_with_unreadable_identity_evidence_keeps_all_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The output rewrite requires a completed finding-identity comparison."""
    module = _load_hook()
    baseline = _baseline(tmp_path)
    observed: list[str] = []

    def unreadable(_path: Path) -> None:
        raise OSError("baseline unavailable")

    monkeypatch.setattr(module, "_finding_counts", unreadable)

    def runner(
        command: list[str], *, check: bool,
        capture_output: bool = False, text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del check, text
        disposable = Path(command[command.index("--baseline") + 1])
        stdout = _native_update(disposable)
        observed.append(stdout)
        if not capture_output:
            print(stdout, end="")
        return subprocess.CompletedProcess(
            command, 3, stdout=stdout if capture_output else None,
        )

    assert module.run_readonly_scan(baseline=baseline, filenames=(), runner=runner) == 3
    assert capsys.readouterr().out == observed[0]


def test_metadata_success_keeps_unknown_stdout_and_all_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Near matches, canonical paths and stderr are never broadly filtered."""
    module = _load_hook()
    baseline = _baseline(tmp_path)
    observed: list[tuple[str, str]] = []

    def runner(
        command: list[str], *, check: bool,
        capture_output: bool = False, text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del check, text
        disposable = Path(command[command.index("--baseline") + 1])
        stdout = _native_update(baseline) + _native_update(disposable).replace(
            "Probably", "Unexpectedly", 1,
        )
        stderr = _native_update(disposable) + "unrelated warning"
        observed.append((stdout, stderr))
        if not capture_output:
            print(stdout, end="")
            print(stderr, end="", file=module.sys.stderr)
        return subprocess.CompletedProcess(
            command, 3, stdout=stdout if capture_output else None,
            stderr=stderr if capture_output else None,
        )

    assert module.run_readonly_scan(baseline=baseline, filenames=(), runner=runner) == 0
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == observed[0]


def test_runner_captures_native_text_without_changing_scanner_arguments(tmp_path: Path) -> None:
    """Output handling belongs in the existing runner, without scanner flags."""
    module = _load_hook()
    baseline = _baseline(tmp_path)
    observed: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        assert command[:2] == ["native-hook", "--baseline"]
        assert command[3:] == ["source.py", "other.py"]
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    assert module.run_readonly_scan(
        baseline=baseline, filenames=("source.py", "other.py"),
        executable="native-hook", runner=runner,
    ) == 0
    assert observed == {"check": False, "capture_output": True, "text": True}
