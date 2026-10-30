"""Real root-Make startup probes and their unchanged consumer boundaries."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from tests.unit import test_reviewed_head_inputs as reviewed

ROOT = Path(__file__).resolve().parents[2]
_ROOT_MAKE_ENTRYPOINT = ROOT / "Makefile"


def _probe_shell(tmp_path: Path, *, ollama_path: Path | None = None) -> tuple[Path, Path]:
    """Delegate actual shell commands and record only fixed startup labels."""
    log = tmp_path / "startup-labels.jsonl"
    wrapper = tmp_path / "native-probe-shell"
    wrapper.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        "command = sys.argv[-1]\nlabel = None\n"
        "if command == 'infocmp >/dev/null 2>&1 && printf yes':\n"
        "    label = 'terminfo'\n"
        "elif '_NPROCESSORS_ONLN' in command and command.startswith('if [ -n '):\n"
        "    label = 'workers'\n"
        "elif command == 'git rev-parse HEAD 2>/dev/null':\n"
        "    label = 'gate-sha'\n"
        "elif command == 'command -v ollama 2>/dev/null || echo ' + chr(34) * 2:\n"
        "    label = 'ollama'\n"
        "elif command.startswith('command -v podman '):\n"
        "    label = 'container'\n"
        "if label is not None:\n"
        f"    with Path({str(log)!r}).open('a', encoding='utf-8') as output:\n"
        "        output.write(json.dumps(label) + '\\n')\n"
        + "child_environment = dict(os.environ)\n"
        + (
            "if label == 'ollama':\n"
            f"    child_environment['PATH'] = {str(ollama_path)!r}\n"
            if ollama_path is not None else ""
        )
        + "os.execve('/bin/sh', ['/bin/sh', *sys.argv[1:]], child_environment)\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper, log


def _counts(log: Path) -> Counter[str]:
    return Counter(json.loads(line) for line in log.read_text(encoding="utf-8").splitlines())


def _native_make_environment(environment: dict[str, str] | None = None) -> dict[str, str]:
    """Give each explicit native control its own Make and pytest inputs."""
    removed = {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "PYTEST_ARGS"}
    inherited = os.environ if environment is None else environment
    return {key: value for key, value in inherited.items() if key not in removed}


def _root_make(
    shell: Path, *arguments: str, make_environment: dict[str, str] | None = None,
    make_input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "-s", "-f", str(_ROOT_MAKE_ENTRYPOINT),
         *arguments, "SHELL=" + shlex.quote(str(shell))],
        cwd=ROOT, env=_native_make_environment(make_environment), input=make_input,
        capture_output=True, text=True, timeout=10, check=False,
    )


@pytest.mark.parametrize("root_probe_environment", [False, True], ids=["ordinary", "environment-priority"])
def test_native_watchdog_focus_does_not_probe_unused_local_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_probe_environment: bool,
) -> None:
    """Exercise every original native focus assertion under the real entrypoint."""
    shell, log = _probe_shell(tmp_path)
    native = cast(Callable[..., subprocess.CompletedProcess[str]], subprocess.run)
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(tmp_path / "native-resources"))
    monkeypatch.setenv("WORK_ITEM_ID", "F12.1")
    monkeypatch.delenv("_OLLAMA_BIN", raising=False)

    def measured(command: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if isinstance(command, list) and command and command[0] == "make" and kwargs.get("cwd") == ROOT:
            argv = cast(list[str], command)
            kwargs["env"] = _native_make_environment()
            return native(
                [*argv, "-f", str(_ROOT_MAKE_ENTRYPOINT), "SHELL=" + shlex.quote(str(shell)),
                 *(["-e"] if root_probe_environment else [])],
                **kwargs,
            )
        return native(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", measured)
    reviewed.test_native_focus_commands_preserve_watchdog_ownership(tmp_path, monkeypatch)
    counts = _counts(log)
    print("native-root-startup-counts", root_probe_environment, dict(sorted(counts.items())))
    assert counts["terminfo"] == counts["workers"] == counts["container"] == 16
    assert counts["ollama"] == 0, counts


@pytest.mark.parametrize("ollama_probe_priority", [False, True], ids=["ordinary", "environment-priority"])
@pytest.mark.parametrize("ollama_probe_kind", ["present", "missing", "environment", "command-line"])
def test_root_local_model_install_check_preserves_lookup_and_overrides(
    tmp_path: Path, ollama_probe_priority: bool, ollama_probe_kind: str,
) -> None:
    """Keep real command-v, alias dependencies and native missing-tool failure."""
    tools = tmp_path / "local-tools"
    tools.mkdir()
    tool = tools / "ollama"
    executed = tmp_path / "must-not-execute-ollama"
    if ollama_probe_kind != "missing":
        tool.write_text("#!/bin/sh\ntouch " + shlex.quote(str(executed)) + "\nexit 97\n", encoding="utf-8")
        tool.chmod(0o755)
    shell, log = _probe_shell(tmp_path, ollama_path=tools)
    environment = os.environ.copy()
    environment.pop("_OLLAMA_BIN", None)
    binding = str(tmp_path / "literal caller tool")
    arguments = ["-f", "-", "gludd-local-model-probe"]
    if ollama_probe_kind == "environment":
        environment["_OLLAMA_BIN"] = binding
    if ollama_probe_kind == "command-line":
        arguments.append("_OLLAMA_BIN=" + binding)
    if ollama_probe_priority:
        arguments.append("-e")
    result = _root_make(
        shell, *arguments, make_environment=environment,
        make_input=".PHONY: gludd-local-model-probe\ngludd-local-model-probe: _local-model-ollama-install-check\n",
    )
    counts = _counts(log)
    missing = ollama_probe_kind == "missing"
    overridden = ollama_probe_kind == "command-line" or (
        ollama_probe_kind == "environment" and ollama_probe_priority
    )
    print("native-local-model-counts", ollama_probe_kind, ollama_probe_priority, dict(sorted(counts.items())))
    assert result.returncode == (2 if missing else 0), result.stderr
    if missing:
        assert "ollama not found" in result.stdout
        assert "ollama found at" not in result.stdout
    else:
        assert result.stdout == "  ollama found at " + (binding if overridden else str(tool)) + "\n"
    # Preserve the legacy eager inherited-environment lookup even under -e.
    assert counts["ollama"] == (0 if ollama_probe_kind == "command-line" else 1)
    assert not executed.exists()


@pytest.mark.parametrize("startup_context_priority", [False, True], ids=["ordinary", "environment-priority"])
@pytest.mark.parametrize(("startup_context_workers", "startup_context_term", "startup_context_expected"), [
    ("0", "dumb", ""),
    ("1", "unknown", "-n 1 --dist loadgroup"),
    ("7", "screen-256color", "-n 7 --dist loadgroup"),
    ("auto", "gludd-missing-terminfo", "-n auto --dist loadgroup"),
])
def test_root_local_model_deferral_retains_terminal_workers_and_gate_capture(
    tmp_path: Path, startup_context_priority: bool, startup_context_workers: str,
    startup_context_term: str, startup_context_expected: str,
) -> None:
    shell, log = _probe_shell(tmp_path)
    environment = os.environ.copy()
    environment.pop("_OLLAMA_BIN", None)
    environment.pop("GATE_CANDIDATE_SHA", None)
    environment["TERM"] = startup_context_term
    environment["GLUDD_XDIST_WORKERS"] = startup_context_workers
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    result = _root_make(
        shell, "-f", "-", "gludd-startup-context", *(["-e"] if startup_context_priority else []),
        make_environment=environment,
        make_input=(
            ".PHONY: gludd-startup-context\ngludd-startup-context:\n"
            "\t@printf '%s\\n' 'term=$(TERM)' 'workers=$(_XD)' 'sha=$(GATE_CANDIDATE_SHA)'\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    term = "screen-256color" if startup_context_term == "screen-256color" else "xterm-256color"
    assert result.stdout.splitlines() == [
        "term=" + term, "workers=" + startup_context_expected, "sha=" + head,
    ]
    counts = _counts(log)
    assert counts["gate-sha"] == counts["terminfo"] == counts["workers"] == counts["container"] == 1
    assert counts["ollama"] == 0, counts
