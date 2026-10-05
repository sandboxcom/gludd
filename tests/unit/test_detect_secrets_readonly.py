"""Regression coverage for the non-mutating detect-secrets hook."""

from __future__ import annotations

import importlib.util
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / "scripts" / "detect_secrets_readonly.py"
PRECOMMIT_PATH = ROOT / ".pre-commit-config.yaml"
DIGEST_FIELD = "_".join(("hashed", "sec" + "ret"))


def _load_hook() -> ModuleType:
    assert HOOK_PATH.is_file(), f"read-only hook missing: {HOOK_PATH}"
    spec = importlib.util.spec_from_file_location("detect_secrets_readonly", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_scans_a_disposable_copy_and_preserves_canonical_baseline(
    tmp_path: Path,
) -> None:
    """An upstream line-number refresh must never dirty the candidate tree."""
    module = _load_hook()
    baseline = tmp_path / ".secrets.baseline"
    original_payload = {
        "results": {
            "src/example.py": [
                {
                    "type": "Secret Keyword",
                    "filename": "src/example.py",
                    DIGEST_FIELD: "known",
                    "is_verified": False,
                    "line_number": 7,
                },
            ],
        },
        "generated_at": "stable",
    }
    original = json.dumps(original_payload)
    baseline.write_text(original, encoding="utf-8")
    observed: dict[str, Any] = {}

    def runner(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["check"] = check
        disposable = Path(command[command.index("--baseline") + 1])
        observed["disposable"] = disposable
        assert disposable != baseline
        assert disposable.read_text(encoding="utf-8") == original
        refreshed = dict(original_payload)
        refreshed["generated_at"] = "upstream-mutated"
        refreshed["results"] = {
            "src/example.py": [
                {**original_payload["results"]["src/example.py"][0], "line_number": 9},
            ],
        }
        disposable.write_text(json.dumps(refreshed), encoding="utf-8")
        return subprocess.CompletedProcess(command, 3)

    result = module.run_readonly_scan(
        baseline=baseline,
        filenames=("src/example.py",),
        executable="detect-secrets-hook",
        runner=runner,
    )

    assert result == 0
    assert baseline.read_text(encoding="utf-8") == original
    assert observed["check"] is False
    assert observed["command"][-1] == "src/example.py"
    assert not observed["disposable"].exists()


@pytest.mark.parametrize("added_digest", ["new-finding", "known"])
def test_hook_rejects_new_finding_even_when_upstream_only_returns_update_status(
    tmp_path: Path,
    added_digest: str,
) -> None:
    """A refresh must reject a new identity or duplicate secret occurrence."""
    module = _load_hook()
    baseline = tmp_path / ".secrets.baseline"
    original = {
        "results": {
            "src/example.py": [
                {
                    "type": "Secret Keyword",
                    "filename": "src/example.py",
                    DIGEST_FIELD: "known",
                    "is_verified": False,
                    "line_number": 7,
                },
            ],
        },
    }
    baseline.write_text(json.dumps(original), encoding="utf-8")

    def runner(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        del check
        disposable = Path(command[command.index("--baseline") + 1])
        changed = json.loads(disposable.read_text(encoding="utf-8"))
        changed["results"]["src/example.py"].append(
            {
                "type": "Secret Keyword",
                "filename": "src/example.py",
                DIGEST_FIELD: added_digest,
                "is_verified": False,
                "line_number": 8,
            },
        )
        disposable.write_text(json.dumps(changed), encoding="utf-8")
        return subprocess.CompletedProcess(command, 3)

    assert (
        module.run_readonly_scan(
            baseline=baseline,
            filenames=("src/example.py",),
            executable="detect-secrets-hook",
            runner=runner,
        )
        == 3
    )


def test_hook_fails_closed_when_updated_disposable_baseline_is_malformed(
    tmp_path: Path,
) -> None:
    """Malformed upstream output remains a visible admission failure."""
    module = _load_hook()
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text('{"results": {}}', encoding="utf-8")

    def runner(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        del check
        disposable = Path(command[command.index("--baseline") + 1])
        disposable.write_text("not-json", encoding="utf-8")
        return subprocess.CompletedProcess(command, 3)

    assert (
        module.run_readonly_scan(
            baseline=baseline,
            filenames=(),
            executable="detect-secrets-hook",
            runner=runner,
        )
        == 3
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"results": []},
        {"results": {"src/example.py": {}}},
        {"results": {"src/example.py": [[]]}},
        {
            "results": {
                "src/example.py": [
                    {"filename": None, "type": "kind", DIGEST_FIELD: "digest"},
                ],
            },
        },
        {
            "results": {
                "src/example.py": [
                    {"filename": "src/example.py", "type": None, DIGEST_FIELD: "digest"},
                ],
            },
        },
        {
            "results": {
                "src/example.py": [
                    {"filename": "src/example.py", "type": "kind", DIGEST_FIELD: None},
                ],
            },
        },
    ],
)
def test_finding_counts_rejects_malformed_baseline_shapes(
    tmp_path: Path,
    payload: object,
) -> None:
    """Semantic comparison must fail closed on every malformed result shape."""
    module = _load_hook()
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="baseline"):
        module._finding_counts(baseline)


def test_hook_propagates_non_update_scanner_status(tmp_path: Path) -> None:
    """Statuses other than upstream's metadata-update code remain unchanged."""
    module = _load_hook()
    baseline = tmp_path / ".secrets.baseline"
    original = '{"results": {}}'
    baseline.write_text(original, encoding="utf-8")

    def runner(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        del check
        return subprocess.CompletedProcess(command, 7)

    assert (
        module.run_readonly_scan(
            baseline=baseline,
            filenames=(),
            executable="detect-secrets-hook",
            runner=runner,
        )
        == 7
    )
    assert baseline.read_text(encoding="utf-8") == original


def test_hook_cleans_disposable_baseline_when_scanner_raises(tmp_path: Path) -> None:
    """A scanner crash must not leak the temporary security artifact."""
    module = _load_hook()
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text('{"results": {}}\n', encoding="utf-8")
    observed: dict[str, Path] = {}

    def runner(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        del check
        observed["disposable"] = Path(command[command.index("--baseline") + 1])
        raise OSError("scanner unavailable")

    try:
        module.run_readonly_scan(
            baseline=baseline,
            filenames=(),
            executable="detect-secrets-hook",
            runner=runner,
        )
    except OSError as exc:
        assert str(exc) == "scanner unavailable"
    else:
        raise AssertionError("scanner failure did not propagate")

    assert not observed["disposable"].exists()


def test_hook_rejects_a_missing_canonical_baseline(tmp_path: Path) -> None:
    """Admission must fail closed before creating scanner state."""
    module = _load_hook()

    with pytest.raises(FileNotFoundError, match="baseline not found"):
        module.run_readonly_scan(
            baseline=tmp_path / "missing.json",
            filenames=(),
        )


def test_main_fails_closed_when_scanner_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing upstream executable is a visible admission failure."""
    module = _load_hook()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    assert module.main([]) == 2
    assert "detect-secrets-hook is unavailable" in capsys.readouterr().err


def test_main_forwards_explicit_baseline_and_filenames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI must preserve the exact pre-commit filename set and result."""
    module = _load_hook()
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"results": {}}\n', encoding="utf-8")
    observed: dict[str, Any] = {}

    def fake_scan(**kwargs: Any) -> int:
        observed.update(kwargs)
        return 7

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/tools/scanner")
    monkeypatch.setattr(module, "run_readonly_scan", fake_scan)

    result = module.main(
        ["--baseline", str(baseline), "src/one.py", "src/two.py"],
    )

    assert result == 7
    assert observed == {
        "baseline": baseline,
        "filenames": ("src/one.py", "src/two.py"),
        "executable": "/tools/scanner",
    }


def test_main_reports_scanner_oserror(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operational scanner errors stay fail-closed and secret-safe."""
    module = _load_hook()

    def fail_scan(**_kwargs: Any) -> int:
        raise OSError("scanner failed")

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/tools/scanner")
    monkeypatch.setattr(module, "run_readonly_scan", fail_scan)

    assert module.main([]) == 2
    assert "detect-secrets read-only scan failed: scanner failed" in capsys.readouterr().err


def test_script_entrypoint_returns_main_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Direct execution must expose the same fail-closed status as ``main``."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(sys, "argv", [str(HOOK_PATH)])

    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(HOOK_PATH), run_name="__main__")

    assert caught.value.code == 2
    assert "detect-secrets-hook is unavailable" in capsys.readouterr().err


def test_precommit_uses_readonly_hook_and_quarantines_mutating_upstream_hook() -> None:
    """Normal commit/push scans use the wrapper; upstream mutation is manual-only."""
    config = yaml.safe_load(PRECOMMIT_PATH.read_text(encoding="utf-8"))
    hooks = [
        (repo, hook)
        for repo in config["repos"]
        for hook in repo.get("hooks", [])
    ]
    upstream = next(hook for repo, hook in hooks if hook["id"] == "detect-secrets")
    readonly = next(
        hook for repo, hook in hooks if hook["id"] == "detect-secrets-readonly"
    )

    assert upstream["stages"] == ["manual"]
    assert readonly["entry"] == "uv run python scripts/detect_secrets_readonly.py"
    assert readonly["language"] == "system"
    assert readonly["pass_filenames"] is True
    assert readonly["require_serial"] is True
    assert readonly["exclude"] == upstream["exclude"]
