"""Harmless behavioral admission for bounded, pinned Windows NSIS acquisition."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PINNED_COMMAND = (
    "choco", "install", "nsis", "--version=3.12.0", "-y", "--no-progress", "--execution-timeout=180",
)
FEED_FAILURE = (
    "Unable to connect to source 'https://community.chocolatey.org/api/v2/':\n"
    " Failed to fetch results from V2 feed at "
    "'https://community.chocolatey.org/api/v2/Packages(Id='nsis',Version='3.12.0')' "
    "with following message : Response status code does not indicate success: 504 (Gateway Timeout).\n"
    "nsis not installed. The package was not found with the source(s) listed.\n"
    "Version was specified as '3.12.0'. It is possible that version does not exist for 'nsis'.\n"
)


def _module() -> ModuleType:
    return importlib.import_module("scripts.windows_nsis_acquisition")


def _fake_installs(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[tuple[int, str]],
) -> tuple[ModuleType, list[Path], list[str]]:
    module = _module()
    calls: list[Path] = []
    probes: list[str] = []

    def install(command: tuple[str, ...], log: Path, emit: object) -> int:
        assert tuple(command) == PINNED_COMMAND
        code, output = outcomes[len(calls)]
        calls.append(log)
        log.write_text(output, encoding="utf-8")
        return code

    monkeypatch.setattr(module, "_install", install)
    monkeypatch.setattr(module, "_verify_version", lambda emit: probes.append("version"))
    return module, calls, probes


def _fake_installed_compiler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    program_files = tmp_path / "Program Files (x86)"
    compiler = program_files / "NSIS" / "makensis.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_text("harmless installed-compiler fixture", encoding="utf-8")
    monkeypatch.setenv("PROGRAMFILES(X86)", str(program_files))
    return compiler


@pytest.mark.parametrize("status", [502, 503, 504])
def test_transient_feed_failure_then_success_retries_exact_pin_and_verifies_once(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    failure = FEED_FAILURE.replace("504 (Gateway Timeout)", f"{status} (transient)")
    module, calls, probes = _fake_installs(monkeypatch, [(1, failure), (0, "installed")])
    sleeps: list[float] = []
    events: list[str] = []
    module.install_nsis(PINNED_COMMAND, sleep=sleeps.append, emit=events.append)
    assert len(calls) == 2 and len(set(calls)) == 2
    assert calls[0].parent.name.startswith("gludd-windows-nsis-")
    assert not calls[0].parent.exists()
    assert sleeps == [5.0] and probes == ["version"]
    assert any("GLUDD-NSIS-ATTEMPT attempt=1/3" in event for event in events)
    assert any("GLUDD-NSIS-ATTEMPT attempt=2/3" in event for event in events)
    assert any("GLUDD-NSIS-RETRY" in event and "wait_seconds=5" in event for event in events)
    assert any("GLUDD-NSIS-ACQUISITION-PASS" in event for event in events)


def test_transient_exhaustion_is_finite_fail_closed_and_never_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, calls, probes = _fake_installs(monkeypatch, [(1, FEED_FAILURE)] * 3)
    sleeps: list[float] = []
    events: list[str] = []
    with pytest.raises(module.NsisAcquisitionError) as failure:
        module.install_nsis(PINNED_COMMAND, sleep=sleeps.append, emit=events.append)
    assert failure.value.returncode == 1
    assert len(calls) == 3 and len(set(calls)) == 3
    assert sleeps == [5.0, 10.0] and probes == []
    assert not calls[0].parent.exists()
    assert sum("GLUDD-NSIS-ATTEMPT" in event for event in events) == 3
    assert sum("GLUDD-NSIS-RETRY" in event for event in events) == 2
    assert not any("ACQUISITION-PASS" in event for event in events)


@pytest.mark.parametrize("output", [
    "nsis was not found with the source(s) listed; version 3.12.0 may not exist",
    "Response status code does not indicate success: 404 (Not Found)",
    "Response status code does not indicate success: 401 (Unauthorized)",
    "Response status code does not indicate success: 403 (Forbidden)",
    "checksum did not match",
    "Hashes do not match",
    "Access to the path was denied",
    "Unable to create an SSL/TLS secure channel",
    "Error while running chocolateyInstall.ps1",
    "unclassified installation failure",
    FEED_FAILURE + "\nchecksum did not match",
    FEED_FAILURE + "\nHashes do not match",
    FEED_FAILURE + "\nAccess to the path was denied",
    FEED_FAILURE + "\nResponse status code does not indicate success: 403 (Forbidden)",
])
def test_nontransient_install_failure_stops_before_retry_or_version_check(
    monkeypatch: pytest.MonkeyPatch, output: str,
) -> None:
    module, calls, probes = _fake_installs(monkeypatch, [(1, output)])
    sleeps: list[float] = []
    with pytest.raises(module.NsisAcquisitionError):
        module.install_nsis(PINNED_COMMAND, sleep=sleeps.append)
    assert len(calls) == 1 and probes == [] and sleeps == []
    assert not calls[0].parent.exists()


@pytest.mark.parametrize("code", [7, 3010, 1641, 143])
def test_other_nonzero_child_codes_never_become_success_or_retry(
    monkeypatch: pytest.MonkeyPatch, code: int,
) -> None:
    module, calls, probes = _fake_installs(monkeypatch, [(code, FEED_FAILURE)])
    sleeps: list[float] = []
    with pytest.raises(module.NsisAcquisitionError):
        module.install_nsis(PINNED_COMMAND, sleep=sleeps.append)
    assert len(calls) == 1 and probes == [] and sleeps == []


@pytest.mark.parametrize("output", [
    "installer file 504.exe was not found",
    "HTTP 504 was observed in a previous job",
    "Response status code does not indicate success: 504 (Gateway Timeout)",
    FEED_FAILURE.replace("https://community.chocolatey.org/api/v2/", "https://untrusted.invalid/api/v2/"),
    FEED_FAILURE.replace("504 (Gateway Timeout)", "404 (Not Found)"),
    FEED_FAILURE + FEED_FAILURE.replace("504 (Gateway Timeout)", "404 (Not Found)"),
])
def test_retry_requires_current_official_feed_failure_not_incidental_status_text(output: str) -> None:
    assert _module()._is_transient_feed_failure(output) is False


@pytest.mark.parametrize("command", [
    (),
    ("choco", "install", "nsis", "-y"),
    (*PINNED_COMMAND[:3], "--version=latest", *PINNED_COMMAND[4:]),
    (*PINNED_COMMAND[:3], "--version=3.11.0", *PINNED_COMMAND[4:]),
    (*PINNED_COMMAND, "--force"),
    (*PINNED_COMMAND, "--ignore-checksums"),
    (*PINNED_COMMAND, "--source=https://untrusted.invalid/"),
])
def test_unknown_unpinned_or_bypass_command_rejected_before_acquisition(
    monkeypatch: pytest.MonkeyPatch, command: tuple[str, ...],
) -> None:
    module, calls, probes = _fake_installs(monkeypatch, [])
    with pytest.raises(module.NsisAcquisitionError) as failure:
        module.install_nsis(command)
    assert failure.value.returncode == 2
    assert calls == [] and probes == []


@pytest.mark.parametrize("timeout_kind", ["subprocess", "psutil"])
def test_real_install_owner_preserves_live_console_logs_and_visible_heartbeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, timeout_kind: str,
) -> None:
    module = _module()
    process = Mock()
    timeout = (
        subprocess.TimeoutExpired(PINNED_COMMAND, 15)
        if timeout_kind == "subprocess" else module.psutil.TimeoutExpired(15, pid=12345)
    )
    process.wait.side_effect = [timeout, 0]
    process.poll.return_value = 0
    spawn = Mock(return_value=process)
    monkeypatch.setattr(module.psutil, "Popen", spawn)
    events: list[str] = []
    log = tmp_path / "attempt-1.log"
    assert module._install(PINNED_COMMAND, log, events.append) == 0
    arguments, keywords = spawn.call_args
    assert arguments == ([*PINNED_COMMAND, f"--log-file={log}"],)
    assert not keywords.get("shell", False)
    assert "stdout" not in keywords and "stderr" not in keywords
    assert any("GLUDD-NSIS-HEARTBEAT" in event and "elapsed_seconds=15" in event for event in events)
    assert process.wait.call_args_list[0].kwargs["timeout"] == 15


@pytest.mark.parametrize("timeout_kind", ["subprocess", "psutil"])
def test_real_install_owner_times_out_and_cleans_only_owned_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, timeout_kind: str,
) -> None:
    module = _module()
    child = Mock()
    process = Mock()
    process.wait.side_effect = (
        subprocess.TimeoutExpired(PINNED_COMMAND, 15)
        if timeout_kind == "subprocess" else module.psutil.TimeoutExpired(15, pid=12345)
    )
    process.poll.return_value = None
    process.children.return_value = [child]
    monkeypatch.setattr(module.psutil, "Popen", Mock(return_value=process))
    reap = Mock(return_value=([child, process], []))
    monkeypatch.setattr(module.psutil, "wait_procs", reap)
    events: list[str] = []
    with pytest.raises(module.NsisAcquisitionError) as failure:
        module._install(PINNED_COMMAND, tmp_path / "attempt-1.log", events.append)
    assert failure.value.returncode == 124
    assert process.wait.call_count == 12
    child.kill.assert_called_once_with()
    process.kill.assert_called_once_with()
    process.children.assert_called_once_with(recursive=True)
    assert reap.call_args.args == ([child, process],)
    assert reap.call_args.kwargs == {"timeout": 10}
    assert any("GLUDD-NSIS-CLEANUP" in event for event in events)


@pytest.mark.parametrize("output", ["v3.12", "v3.12.0\n", "3.12\r\n"])
def test_compiler_version_probe_accepts_only_the_pinned_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output: str,
) -> None:
    module = _module()
    compiler = _fake_installed_compiler(monkeypatch, tmp_path)
    run = Mock(return_value=subprocess.CompletedProcess(["makensis.exe", "/VERSION"], 0, output))
    monkeypatch.setattr(module.subprocess, "run", run)
    events: list[str] = []
    module._verify_version(events.append)
    assert run.call_args.args == ([str(compiler), "/VERSION"],)
    assert run.call_args.kwargs["timeout"] == 15
    assert any("GLUDD-NSIS-VERSION" in event for event in events)


@pytest.mark.parametrize("code,output", [(0, "v3.11"), (0, "v3.12.1"), (0, ""), (1, "v3.12")])
def test_compiler_probe_error_or_wrong_version_is_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int, output: str,
) -> None:
    module = _module()
    _fake_installed_compiler(monkeypatch, tmp_path)
    monkeypatch.setattr(
        module.subprocess, "run", Mock(return_value=subprocess.CompletedProcess("makensis.exe", code, output)),
    )
    with pytest.raises(module.NsisAcquisitionError):
        module._verify_version(lambda message: None)


@pytest.mark.parametrize("location", ["missing-environment", "missing-file", "relative-directory"])
def test_compiler_location_must_be_explicit_existing_vendor_install_not_path_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, location: str,
) -> None:
    module = _module()
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    if location == "missing-file":
        monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    elif location == "relative-directory":
        monkeypatch.setenv("PROGRAMFILES(X86)", "untrusted-relative-directory")
    run = Mock(return_value=subprocess.CompletedProcess("makensis.exe", 0, "v3.12"))
    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(module.NsisAcquisitionError, match=r"compiler|ProgramFiles"):
        module._verify_version(lambda message: None)
    run.assert_not_called()


def test_missing_attempt_log_does_not_authorize_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    install = Mock(return_value=1)
    verify = Mock()
    monkeypatch.setattr(module, "_install", install)
    monkeypatch.setattr(module, "_verify_version", verify)
    sleeps: list[float] = []
    with pytest.raises(module.NsisAcquisitionError):
        module.install_nsis(PINNED_COMMAND, sleep=sleeps.append)
    install.assert_called_once()
    verify.assert_not_called()
    assert sleeps == []


def test_attempt_diagnostic_read_has_finite_size_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    module, calls, probes = _fake_installs(monkeypatch, [(1, FEED_FAILURE)])
    monkeypatch.setattr(module, "MAX_LOG_BYTES", 16)
    sleeps: list[float] = []
    with pytest.raises(module.NsisAcquisitionError, match="diagnostic bound"):
        module.install_nsis(PINNED_COMMAND, sleep=sleeps.append)
    assert len(calls) == 1 and probes == [] and sleeps == []
    assert not calls[0].parent.exists()


def test_retry_callback_rejects_missing_bounded_next_action(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _, _ = _fake_installs(monkeypatch, [(0, "installed")])
    actual_retrying = module.Retrying
    capture_retryer = Mock(wraps=actual_retrying)
    monkeypatch.setattr(module, "Retrying", capture_retryer)
    module.install_nsis(PINNED_COMMAND)
    state = module.RetryCallState(retry_object=actual_retrying(), fn=None, args=(), kwargs={})
    assert state.next_action is None
    with pytest.raises(module.NsisAcquisitionError, match="bounded next action"):
        capture_retryer.call_args.kwargs["before_sleep"](state)


def test_successful_install_with_bad_compiler_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    module, calls, _ = _fake_installs(monkeypatch, [(0, "installed")])

    def bad_version(emit: object) -> None:
        raise module.NsisAcquisitionError("wrong compiler version")

    monkeypatch.setattr(module, "_verify_version", bad_version)
    sleeps: list[float] = []
    with pytest.raises(module.NsisAcquisitionError, match="wrong compiler version"):
        module.install_nsis(PINNED_COMMAND, sleep=sleeps.append)
    assert len(calls) == 1 and sleeps == [] and not calls[0].parent.exists()


def test_cli_rejects_failure_without_leaking_a_success_marker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()

    def fail(command: tuple[str, ...]) -> None:
        raise module.NsisAcquisitionError("feed retries exhausted", returncode=1)

    monkeypatch.setattr(module, "install_nsis", fail)
    assert module.main(list(PINNED_COMMAND)) == 1
    output = capsys.readouterr().out
    assert "::error::GLUDD-NSIS-ACQUISITION-FAILED" in output
    assert "ACQUISITION-PASS" not in output


def test_cli_propagates_missing_executable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()

    def fail(command: tuple[str, ...]) -> None:
        raise FileNotFoundError("choco is unavailable")

    monkeypatch.setattr(module, "install_nsis", fail)
    assert module.main(list(PINNED_COMMAND)) == 1
    assert "choco is unavailable" in capsys.readouterr().out


def test_cli_success_routes_its_actual_argv_without_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    install = Mock()
    monkeypatch.setattr(module, "install_nsis", install)
    monkeypatch.setattr(module.sys, "argv", ["windows_nsis_acquisition.py", *PINNED_COMMAND])
    assert module.main() == 0
    install.assert_called_once_with(list(PINNED_COMMAND))


def test_owned_cleanup_reports_an_already_exited_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    child = Mock(pid=23456)
    child.kill.side_effect = module.psutil.NoSuchProcess(child.pid)
    process = Mock(pid=12345)
    process.children.return_value = [child]
    reap = Mock(return_value=([child, process], []))
    monkeypatch.setattr(module.psutil, "wait_procs", reap)
    events: list[str] = []
    module._cleanup_owned_tree(process, events.append)
    process.kill.assert_called_once_with()
    assert reap.call_args.args == ([child, process],)
    assert any("GLUDD-NSIS-CLEANUP-ALREADY-EXITED pid=23456" in event for event in events)


def test_owned_cleanup_does_not_hide_permission_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    process = Mock(pid=12345)
    process.children.return_value = []
    process.kill.side_effect = module.psutil.AccessDenied(process.pid)
    with pytest.raises(module.psutil.AccessDenied):
        module._cleanup_owned_tree(process, lambda message: None)


def test_owned_cleanup_rejects_a_process_that_did_not_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    process = Mock(pid=12345)
    process.children.return_value = []
    monkeypatch.setattr(module.psutil, "wait_procs", Mock(return_value=([], [process])))
    with pytest.raises(module.NsisAcquisitionError, match="cleanup failed"):
        module._cleanup_owned_tree(process, lambda message: None)


def test_workflow_executes_bounded_pinned_helper_before_strict_installer_build() -> None:
    windows = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())["jobs"]["windows"]
    step = next(step for step in windows["steps"] if step.get("name") == "Build NSIS installer")
    run = step["run"]
    assert step["timeout-minutes"] == 12
    assert step.get("continue-on-error", False) is False
    assert "python -W error scripts/windows_nsis_acquisition.py choco install nsis --version=3.12.0" in run
    assert "--execution-timeout=180" in run and "--no-sync --python 3.12" in run
    assert run.index("windows_nsis_acquisition.py") < run.index("if ($LASTEXITCODE -ne 0)")
    assert run.index("if ($LASTEXITCODE -ne 0)") < run.index("$makensis /WX")
    assert 'Join-Path ${env:ProgramFiles(x86)} "NSIS\\makensis.exe"' in run
    assert "Get-Command makensis.exe" not in run
    assert "throw \"Chocolatey failed to install NSIS 3.12.0" in run
    assert "Get-FileHash" in run and "makensis did not produce" in run
