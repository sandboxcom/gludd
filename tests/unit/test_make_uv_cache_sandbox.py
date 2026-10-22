"""Make must never force an approval prompt through the user's uv cache."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
SAFE_CACHE = "/tmp/gludd-uv-cache-public-v2"


def _recipe(target: str) -> str:
    """Return the indented recipe body for a single Makefile target."""
    lines = compose_makefile(ROOT / "Makefile").splitlines()
    body: list[str] = []
    in_target = False
    for line in lines:
        if re.match(rf"^{re.escape(target)}:", line):
            in_target = True
            continue
        if in_target:
            if line and not line[0].isspace():
                break
            body.append(line)
    return "\n".join(body)


def test_make_overrides_ambient_uv_cache_with_writable_shared_cache() -> None:
    makefile = compose_makefile(ROOT / "Makefile")

    assert f"GLUDD_UV_CACHE_DIR ?= {SAFE_CACHE}" in makefile
    assert "override UV_CACHE_DIR := $(GLUDD_UV_CACHE_DIR)" in makefile
    assert "export UV_CACHE_DIR" in makefile
    assert "@printf '%s\\n' \"$$UV_CACHE_DIR\"" in makefile
    version_line = next(line for line in makefile.splitlines() if line.startswith("VERSION "))
    assert 'UV_CACHE_DIR="$(GLUDD_UV_CACHE_DIR)" uv version' in version_line
    assert 'uv version --short --offline --project "$(CURDIR)"' in version_line
    assert "$(UV) run" not in version_line


def test_uv_cache_path_ignores_prompt_prone_ambient_value() -> None:
    environment = {**os.environ, "UV_CACHE_DIR": "/Users/example/.cache/uv"}
    result = subprocess.run(
        ["make", "--no-print-directory", "uv-cache-path"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == SAFE_CACHE
    assert result.stderr == ""


def test_uv_cache_prune_has_timeout_or_force_skip_guard() -> None:
    """uv-cache-prune must not invoke a bare $(UV) cache prune that can hang."""
    body = _recipe("uv-cache-prune")

    assert "UV_CACHE_PRUNE_TIMEOUT" in body, (
        "uv-cache-prune must declare a bounded timeout so the prune cannot block forever"
    )
    assert "UV_CACHE_PRUNE_FORCE" in body, "uv-cache-prune must support a UV_CACHE_PRUNE_FORCE=1 override"
    assert any(
        marker in body for marker in ("UV_CACHE_PRUNE_SKIP", "UV_CACHE_PRUNE_HEARTBEAT", "UV_CACHE_PRUNE_TIMEOUT pid")
    ), "uv-cache-prune must detect active uv processes or emit timeout/heartbeat guards"

    bare_prune_pattern = re.compile(r"^\s*@?\$\(UV\) cache prune\s*(--ci)?\s*$")
    for line in body.splitlines():
        assert not bare_prune_pattern.match(line), (
            f"uv-cache-prune must not invoke a bare $(UV) cache prune that can block forever: {line!r}"
        )


def _native_prune(
    tmp_path: Path, *, native_status: int = 0, blocked_child: bool = False,
    force: bool = True, launch_expected: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    """Execute the actual Make owner against a fixture-only native UV child."""
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    (cache / "keep").write_bytes(b"owned fixture cache remains")
    (cache / "keep").chmod(0o600)
    capture = tmp_path / "native-uv.json"
    child_pid = tmp_path / "descendant.pid"
    shim = tmp_path / "fixture uv"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import json, os, signal, subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"capture = Path({str(capture)!r})\n"
        "capture.write_text(json.dumps({'argv': sys.argv[1:], 'cache': os.environ['UV_CACHE_DIR']}))\n"
        "print('fixture UV stdout', flush=True)\n"
        "print('fixture UV stderr', file=sys.stderr, flush=True)\n"
        f"if {blocked_child!r}:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"    Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "    child.wait()\n"
        "    time.sleep(60)\n"
        f"raise SystemExit({native_status})\n",
        encoding="utf-8",
    )
    shim.chmod(0o700)
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"}
    }
    command = [
        "make", "--no-print-directory", "uv-cache-prune",
        f"UV={shlex.quote(str(shim))}", f"PYTHON={shlex.quote(sys.executable)}",
        f"GLUDD_UV_CACHE_DIR={cache}", f"UV_CACHE_PRUNE_OBSERVED_ROOT={tmp_path / 'observed'}",
        f"UV_CACHE_PRUNE_FORCE={int(force)}", "UV_CACHE_PRUNE_TIMEOUT=1",
    ]
    process = subprocess.Popen(
        command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=12)
    finally:
        # The test owns this entire Make session. Old broken implementations
        # must not leave fixture descendants behind after a failing control.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
    assert process.returncode is not None
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if launch_expected:
        assert json.loads(capture.read_text()) == {"argv": ["cache", "prune", "--ci"], "cache": str(cache)}
    else:
        assert not capture.exists()
    assert (cache / "keep").read_bytes() == b"owned fixture cache remains"
    assert (cache / "keep").stat().st_mode & 0o777 == 0o600
    if child_pid.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(child_pid.read_text()), 0)
    receipt_path = tmp_path / "observed/uv-cache-prune/current.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    assert isinstance(receipt, dict)
    return result, receipt


@pytest.mark.parametrize("native_status", [0, 17], ids=["success", "native-failure"])
def test_native_prune_preserves_real_child_status(
    tmp_path: Path, native_status: int,
) -> None:
    result, receipt = _native_prune(tmp_path, native_status=native_status)
    assert result.returncode == (0 if native_status == 0 else 2), result.stdout + result.stderr
    assert receipt["exit_code"] == native_status
    assert receipt["state"] == ("passed" if native_status == 0 else "failed")
    assert "fixture UV stdout" in result.stdout
    assert "fixture UV stderr" in result.stdout + result.stderr
    assert receipt["termination_reason"] is None
    assert Path(str(receipt["log_path"])).read_text().count("fixture UV stdout") == 1


def test_native_prune_timeout_is_124_and_drains_owned_descendants(tmp_path: Path) -> None:
    result, receipt = _native_prune(tmp_path, blocked_child=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert receipt["exit_code"] == 124
    assert receipt["state"] == "timed_out"
    assert receipt["termination_reason"] == "max-runtime-timeout"



def _prune_plan(
    tmp_path: Path, options: tuple[str, ...] = (), *, indirect: bool = False,
    bindings: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run a confined offline plan through the actual public Make owner."""
    makefile = ROOT / "Makefile"
    goal = "uv-cache-prune"
    if indirect:
        makefile = tmp_path / "caller.mk"
        policy = "".join(f"fixture-prune: override {binding}\n" for binding in bindings)
        makefile.write_text(
            f"include {ROOT / 'Makefile'}\n.PHONY: fixture-prune\n{policy}fixture-prune: uv-cache-prune\n",
            encoding="utf-8",
        )
        goal = "fixture-prune"
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"}
    }
    result = subprocess.run(
        [
            "make", "--no-print-directory", "-f", str(makefile), goal,
            "UV_CACHE_PRUNE_VALIDATE_ONLY=1", "UV_CACHE_PRUNE_TIMEOUT=300",
            "UV_CACHE_PRUNE_FORCE=0", f"UV_CACHE_PRUNE_OBSERVED_ROOT={tmp_path / 'observed'}",
            "UV=false", "PYTHON=false", f"GLUDD_UV_CACHE_DIR={tmp_path / 'cache'}", *options,
        ],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10,
    )
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "observed").exists()
    return result


@pytest.mark.parametrize("indirect", [False, True], ids=["explicit", "dependency"])
def test_native_prune_offline_plan_uses_literal_root_without_launch(
    tmp_path: Path, indirect: bool,
) -> None:
    result = _prune_plan(tmp_path, indirect=indirect)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == (
        f"UV_CACHE_PRUNE_PLAN max_seconds=300 force=0 observed_root={tmp_path / 'observed'} "
        "heartbeat=5 quiet=0 retain=20\n"
    )
    assert result.stderr == ""


@pytest.mark.parametrize(
    "option",
    [
        "UV_CACHE_PRUNE_TIMEOUT=0", "UV_CACHE_PRUNE_TIMEOUT=-1",
        "UV_CACHE_PRUNE_TIMEOUT=invalid", "UV_CACHE_PRUNE_TIMEOUT=",
        "UV_CACHE_PRUNE_FORCE=2", "UV_CACHE_PRUNE_VALIDATE_ONLY=2",
        "UV_CACHE_PRUNE_OBSERVED_ROOT=",
    ],
)
def test_native_prune_refuses_invalid_policy_before_launch(tmp_path: Path, option: str) -> None:
    result = _prune_plan(tmp_path, (option,))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UV_CACHE_PRUNE_PLAN" not in result.stdout


@pytest.mark.parametrize(
    "field",
    [
        "UV_CACHE_PRUNE_TIMEOUT", "UV_CACHE_PRUNE_FORCE",
        "UV_CACHE_PRUNE_VALIDATE_ONLY", "UV_CACHE_PRUNE_OBSERVED_ROOT",
    ],
)
@pytest.mark.parametrize("syntax", ["make", "shell", "backtick"])
@pytest.mark.parametrize("indirect", [False, True], ids=["explicit", "dependency"])
def test_native_prune_policy_is_literal_before_startup(
    tmp_path: Path, field: str, syntax: str, indirect: bool,
) -> None:
    _assert_literal_prune_policy(tmp_path, field, syntax, indirect=indirect)


def _assert_literal_prune_policy(
    tmp_path: Path, field: str, syntax: str, *, indirect: bool, target_policy: bool = False,
) -> None:
    marker = tmp_path / "injected"
    payload = f"printf injected > {shlex.quote(str(marker))}"
    value = {"make": f"$(shell {payload})", "shell": f"$({payload})", "backtick": f"\x60{payload}\x60"}[syntax]
    options = (f"{field}={value}",)
    result = _prune_plan(
        tmp_path, () if target_policy else options, indirect=indirect,
        bindings=options if target_policy else (),
    )
    assert not marker.exists(), result.stdout + result.stderr
    if field == "UV_CACHE_PRUNE_OBSERVED_ROOT":
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"observed_root={value} heartbeat=5 quiet=0 retain=20" in result.stdout
    else:
        assert result.returncode == 2, result.stdout + result.stderr



def test_native_prune_busy_guard_skips_and_force_preserves_failure(tmp_path: Path) -> None:
    """A real command-shaped fixture exercises native process admission."""
    busy_uv = tmp_path / "uv"
    ready = tmp_path / "ready"
    busy_uv.write_text(
        f"#!{sys.executable}\nimport time\nfrom pathlib import Path\n"
        f"Path({str(ready)!r}).write_text('ready')\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    busy_uv.chmod(0o700)
    busy = subprocess.Popen([str(busy_uv), "run"], start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and busy.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.read_text() == "ready"
        result, receipt = _native_prune(
            tmp_path / "skip", force=False, launch_expected=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "UV_CACHE_PRUNE_SKIP active uv processes detected" in result.stdout
        assert receipt == {}
        result, receipt = _native_prune(tmp_path / "force", native_status=17)
        assert result.returncode == 2, result.stdout + result.stderr
        assert receipt["exit_code"] == 17 and receipt["state"] == "failed"
    finally:
        with suppress(ProcessLookupError):
            os.killpg(busy.pid, signal.SIGKILL)
        busy.wait(timeout=5)



@pytest.mark.parametrize(
    ("prune_policy_field", "prune_policy_value", "prune_plan_fragment"),
    [
        ("UV_CACHE_PRUNE_TIMEOUT", "7", "max_seconds=7"),
        ("UV_CACHE_PRUNE_FORCE", "1", "force=1"),
        ("UV_CACHE_PRUNE_VALIDATE_ONLY", "1", "UV_CACHE_PRUNE_PLAN"),
        ("UV_CACHE_PRUNE_OBSERVED_ROOT", "private-root", "observed_root="),
    ],
)
def test_native_prune_target_policy_preserves_native_context(
    tmp_path: Path, prune_policy_field: str, prune_policy_value: str, prune_plan_fragment: str,
) -> None:
    if prune_policy_field == "UV_CACHE_PRUNE_OBSERVED_ROOT":
        prune_policy_value = str(tmp_path / "owned observed")
        prune_plan_fragment += prune_policy_value
    options = ("UV_CACHE_PRUNE_VALIDATE_ONLY=0",) if prune_policy_field == "UV_CACHE_PRUNE_VALIDATE_ONLY" else ()
    result = _prune_plan(
        tmp_path, options, indirect=True,
        bindings=(f"{prune_policy_field} = {prune_policy_value}",),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert prune_plan_fragment in result.stdout
    assert not (tmp_path / "owned observed").exists()


@pytest.mark.parametrize(
    "prune_policy_binding",
    [
        "UV_CACHE_PRUNE_TIMEOUT=0", "UV_CACHE_PRUNE_TIMEOUT=-1",
        "UV_CACHE_PRUNE_TIMEOUT=invalid", "UV_CACHE_PRUNE_TIMEOUT=",
        "UV_CACHE_PRUNE_FORCE=2", "UV_CACHE_PRUNE_VALIDATE_ONLY=2",
        "UV_CACHE_PRUNE_OBSERVED_ROOT=",
    ],
)
def test_native_prune_target_policy_refuses_invalid(
    tmp_path: Path, prune_policy_binding: str,
) -> None:
    result = _prune_plan(tmp_path, indirect=True, bindings=(prune_policy_binding,))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "UV_CACHE_PRUNE_PLAN" not in result.stdout


@pytest.mark.parametrize(
    "prune_policy_field",
    [
        "UV_CACHE_PRUNE_TIMEOUT", "UV_CACHE_PRUNE_FORCE",
        "UV_CACHE_PRUNE_VALIDATE_ONLY", "UV_CACHE_PRUNE_OBSERVED_ROOT",
    ],
)
@pytest.mark.parametrize("prune_policy_syntax", ["make", "shell", "backtick"])
def test_native_prune_target_policy_is_literal(
    tmp_path: Path, prune_policy_field: str, prune_policy_syntax: str,
) -> None:
    _assert_literal_prune_policy(
        tmp_path, prune_policy_field, prune_policy_syntax, indirect=True, target_policy=True,
    )
