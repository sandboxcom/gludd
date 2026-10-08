"""Behavioral tests for Windows Defender connector support helpers."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any, cast

import pytest

import general_ludd.connectors.windows_defender_support as support


def test_validate_arg_rejects_injection_and_accepts_plain_values() -> None:
    with pytest.raises(ValueError, match="must be a string"):
        support.validate_arg(cast(Any, 7), "target")
    with pytest.raises(ValueError, match="must not be empty"):
        support.validate_arg("", "target")
    with pytest.raises(ValueError, match="must not start"):
        support.validate_arg("-encoded", "target")
    with pytest.raises(ValueError, match="disallowed"):
        support.validate_arg("quick;scan", "target")

    assert support.validate_arg("QuickScan", "target") == "QuickScan"


def test_default_runner_normalizes_success_timeout_and_os_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=3,
            stdout="out",
            stderr="err",
        ),
    )
    assert support.default_runner(["powershell"]) == (3, "out", "err")

    def timeout(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired("powershell", support.DEFAULT_TIMEOUT)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert support.default_runner(["powershell"])[0] == 124

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise OSError("powershell unavailable")

    monkeypatch.setattr(subprocess, "run", unavailable)
    assert support.default_runner(["powershell"]) == (
        127,
        "",
        "powershell unavailable",
    )


def test_json_command_and_record_normalization_preserve_evidence() -> None:
    command = support.ps_command("Get-MpComputerStatus")
    assert command[:3] == ["powershell", "-NoProfile", "-NonInteractive"]
    assert command[-1] == "Get-MpComputerStatus | ConvertTo-Json -Depth 5"

    assert support.parse_json_stdout("") == []
    assert support.parse_json_stdout('{"enabled": true}') == [{"enabled": True}]
    assert support.parse_json_stdout('[{"id": 1}, false, {"id": 2}]') == [
        {"id": 1},
        {"id": 2},
    ]
    assert support.parse_json_stdout("42") == []

    normalized = support.normalize_record(
        {"enabled": True},
        4.5,
        "defender",
        "status",
        command="Get-MpComputerStatus",
    )
    assert normalized["message"] == '{"enabled": true}'
    assert normalized["labels"] == {"enabled": True}
    assert normalized["raw"]["command"] == "Get-MpComputerStatus"


def test_injected_runner_contract_is_normalized_and_fail_closed() -> None:
    assert support.run(lambda _argv: "stdout", ["command"]) == (0, "stdout", "")
    assert support.run(cast(Any, lambda _argv: (2, None, None)), ["command"]) == (
        2,
        "",
        "",
    )

    def raises(_argv: list[str]) -> str:
        raise RuntimeError("runner failed")

    assert support.run(raises, ["command"]) == (
        127,
        "",
        "RuntimeError: runner failed",
    )
    with pytest.raises(TypeError, match="runner must return"):
        support.run(cast(Any, lambda _argv: object()), ["command"])
