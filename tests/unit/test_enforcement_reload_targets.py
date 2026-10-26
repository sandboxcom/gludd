"""Functional tests for enforcement Makefile targets.

Tests reload-enforcement, disengage-enforcement, rearm-enforcement,
enforcement-status, and verify-enforcement targets.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = SOURCE_ROOT

_STATE_NAMES = (
    "floor-override",
    "tool-streak.json",
    "mainthread-streak.json",
    "watchdog-disengage.json",
    "enhancement-ratio.json",
    "session-start.json",
    "task-deadlines.json",
    "task-stale.json",
    "multitask-state.json",
    "block-counter.json",
    "watchdog-ci.json",
)
_NATIVE_TARGETS = (
    "reload-enforcement", "disengage-enforcement", "rearm-enforcement",
    "enforcement-status", "verify-enforcement", "clean-tmp",
)


def _state_path(name: str) -> str:
    return str(ROOT / "state" / ("gludd-" + name))


def _cleanup_enf_state() -> None:
    for name in _STATE_NAMES:
        with contextlib.suppress(FileNotFoundError):
            os.remove(_state_path(name))


def _isolated_make_env(**overrides: str) -> dict[str, str]:
    """Copy unrelated carriers while isolating this fixture's Make controls."""
    env = os.environ.copy()
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "PYTEST_ARGS"):
        env.pop(name, None)
    env.update(overrides)
    return env


def _native_rules() -> str:
    """Keep complete native rules, PHONY declarations and inspection providers."""
    source = compose_makefile(SOURCE_ROOT / "Makefile")
    providers = re.search(
        r"^# Shared native Make inspection grammar\.\n.*?"
        r"^# End shared native Make inspection grammar\.\n",
        source, re.MULTILINE | re.DOTALL,
    )
    assert providers is not None
    phony = re.findall(r"^\.PHONY:(?:[^\n]*\\\n)*[^\n]*\n", source, re.MULTILINE)
    assert phony, "native fixtures require the complete PHONY declarations"
    rules = []
    for name in _NATIVE_TARGETS:
        found = re.search(
            rf"^{re.escape(name)}:[^\n]*\n(?:\t[^\n]*\n)*",
            source, re.MULTILINE,
        )
        assert found is not None, f"missing complete native rule: {name}"
        rules.append(found.group())
    return providers.group() + "".join(phony) + "\n".join(rules)


@pytest.fixture(autouse=True)
def _private_native_enforcement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Execute the real targets with owned paths and the real confined cleaner."""
    native_root = tmp_path / "native"
    state_root = native_root / "state"
    cleanup_root = native_root / "cleanup"
    scripts_root = native_root / "scripts"
    for directory in (state_root, cleanup_root, scripts_root):
        directory.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    assert uv is not None, "the installed native UV owner is required"
    (native_root / "Makefile").write_text(
        f"UV := {shlex.quote(uv)}\n"
        + _native_rules().replace("/tmp/gludd-", str(state_root / "gludd-")),
        encoding="utf-8",
    )
    (scripts_root / "verify_enforcement.py").symlink_to(
        SOURCE_ROOT / "scripts" / "verify_enforcement.py"
    )
    cleaner_source = SOURCE_ROOT / "scripts" / "clean_tmp.py"
    (scripts_root / "clean_tmp.py").write_text(
        "import importlib.util\nimport os\nfrom pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('native_clean_tmp', {str(cleaner_source)!r})\n"
        "assert spec is not None and spec.loader is not None\n"
        "owner = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(owner)\n"
        f"owner._allowed_roots = lambda: [Path({str(cleanup_root)!r}).resolve()]\n"
        f"Path({str(native_root / 'clean-ran')!r}).write_text("
        "os.environ.get('GLUDD_ENFORCEMENT_TEST_CARRIER', ''), encoding='utf-8')\n"
        "raise SystemExit(owner.main())\n",
        encoding="utf-8",
    )
    # The native metadata read resolves through this checkout's own .git file.
    # Keep common-repository paths out of subprocess environment carriers.
    (native_root / ".git").symlink_to(SOURCE_ROOT / ".git")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", native_root)
    for key, value in {
        "GLUDD_STREAK_FILE": _state_path("tool-streak.json"),
        "GLUDD_MAINTHREAD_STREAK_FILE": _state_path("mainthread-streak.json"),
        "GLUDD_MULTITASK_STATE_FILE": _state_path("multitask-state.json"),
        "GLUDD_DISENGAGE_AUDIT_PATH": _state_path("disengage-audit.jsonl"),

        "GIT_OPTIONAL_LOCKS": "0",
        "UV": uv,
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    }.items():
        monkeypatch.setenv(key, value)


class TestReloadEnforcement:
    def test_reload_resets_streak_files(self, tmp_path: Path) -> None:
        streak_path = tmp_path / "tool-streak.json"
        streak_path.write_text(
            json.dumps({"count": 99, "ts": int(time.time() * 1000)})
        )
        env = _isolated_make_env(GLUDD_STREAK_FILE=str(streak_path))

        result = subprocess.run(
            ["make", "reload-enforcement"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
            env=env,
        )

        assert result.returncode == 0, result.stderr
        streak = json.loads(streak_path.read_text())
        assert streak["count"] == 0

    def test_reload_resets_namespaced_mainthread_streak(self, tmp_path: Path) -> None:
        streak_path = tmp_path / "mainthread-streak.json"
        streak_path.write_text(json.dumps({"streak": 99}))
        env = _isolated_make_env(
            GLUDD_MAINTHREAD_STREAK_FILE=str(streak_path)
        )

        result = subprocess.run(
            ["make", "reload-enforcement"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
            env=env,
        )

        assert result.returncode == 0, result.stderr
        streak = json.loads(streak_path.read_text())
        assert streak["streak"] == 0

    def test_reload_removes_namespaced_multitask_state(self, tmp_path: Path) -> None:
        state_path = tmp_path / "multitask-state.json"
        state_path.write_text(json.dumps({"pid": 99999, "zeroStreak": 5}))
        env = _isolated_make_env(
            GLUDD_MULTITASK_STATE_FILE=str(state_path)
        )

        result = subprocess.run(
            ["make", "reload-enforcement"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert not state_path.exists()

    def test_reload_removes_disengage_signal(self) -> None:
        try:
            _cleanup_enf_state()
            with open(_state_path("watchdog-disengage.json"), "w") as f:
                json.dump({"disengage_until": 9999999999999}, f)
            result = subprocess.run(
                ["make", "reload-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists(_state_path("watchdog-disengage.json"))
        finally:
            _cleanup_enf_state()

    def test_reload_writes_floor_override(self) -> None:
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "reload-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert result.returncode == 0, result.stderr
            assert os.path.exists(_state_path("floor-override"))
        finally:
            _cleanup_enf_state()

    def test_reload_removes_enhancement_ratio(self) -> None:
        try:
            _cleanup_enf_state()
            with open(_state_path("enhancement-ratio.json"), "w") as f:
                json.dump({"wave": [], "session": {}}, f)
            result = subprocess.run(
                ["make", "reload-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists(_state_path("enhancement-ratio.json"))
        finally:
            _cleanup_enf_state()

    def test_reload_removes_multitask_state(self) -> None:
        """PID staleness guard: reload must clear multitask-state.json so a
        dead prior-process pid does not persist into the new session."""
        try:
            _cleanup_enf_state()
            with open(_state_path("multitask-state.json"), "w") as f:
                json.dump({"pid": 99999, "zeroStreak": 5}, f)
            env = _isolated_make_env()
            env.pop("GLUDD_MULTITASK_STATE_FILE", None)
            result = subprocess.run(
                ["make", "reload-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=env,
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists(_state_path("multitask-state.json"))
        finally:
            _cleanup_enf_state()


class TestDisengageEnforcement:
    def test_disengage_writes_disengage_signal(self) -> None:
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "disengage-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert result.returncode == 0, result.stderr
            assert os.path.exists(_state_path("watchdog-disengage.json"))
            with open(_state_path("watchdog-disengage.json")) as f:
                sig = json.load(f)
            assert "disengage_until" in sig
            assert sig["disengage_until"] > int(time.time() * 1000)
        finally:
            _cleanup_enf_state()

    def test_disengage_writes_block_counter(self) -> None:
        try:
            _cleanup_enf_state()
            subprocess.run(
                ["make", "disengage-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert os.path.exists(_state_path("block-counter.json"))
        finally:
            _cleanup_enf_state()

    def test_disengage_writes_ci_state(self) -> None:
        try:
            _cleanup_enf_state()
            subprocess.run(
                ["make", "disengage-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert os.path.exists(_state_path("watchdog-ci.json"))
        finally:
            _cleanup_enf_state()


class TestRearmEnforcement:
    def test_rearm_removes_disengage_when_present(self) -> None:
        try:
            _cleanup_enf_state()
            with open(_state_path("watchdog-disengage.json"), "w") as f:
                json.dump({"disengage_until": 9999999999999}, f)
            result = subprocess.run(
                ["make", "rearm-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists(_state_path("watchdog-disengage.json"))
            assert "REMOVED" in result.stdout or "REARMED" in result.stdout
        finally:
            _cleanup_enf_state()

    def test_rearm_no_op_when_not_disengaged(self) -> None:
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "rearm-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert result.returncode == 0, result.stderr
            assert "no-op" in result.stdout
        finally:
            _cleanup_enf_state()


class TestEnforcementStatus:
    def test_status_prints_valid_output(self) -> None:
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "enforcement-status"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert "ENFORCEMENT STATUS" in result.stdout
            assert "floor-override" in result.stdout
            assert "disengaged" in result.stdout
            assert "NO" in result.stdout
        finally:
            _cleanup_enf_state()

    def test_status_shows_disengaged_when_active(self) -> None:
        try:
            _cleanup_enf_state()
            with open(_state_path("watchdog-disengage.json"), "w") as f:
                json.dump({"disengage_until": 9999999999999}, f)
            result = subprocess.run(
                ["make", "enforcement-status"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
                env=_isolated_make_env(),
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert "YES" in result.stdout
        finally:
            _cleanup_enf_state()


class TestVerifyEnforcement:
    def test_passes_when_all_enforcement_active(self, monkeypatch: pytest.MonkeyPatch) -> None:
        try:
            _cleanup_enf_state()
            # ensure no disable vars leak
            monkeypatch.delenv("GLUDD_FLOOR_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_MAINTHREAD_STREAK_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_MULTITASK_FLOOR_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_STOP_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_TASK_DEADLINE_BLOCK", raising=False)
            monkeypatch.delenv("GLUDD_ENHANCEMENT_RATIO_BLOCK", raising=False)
            monkeypatch.delenv("GLUDD_CLEAN_TREE_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_VERIFIED_CLAIMS_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_SESSION_START_ENFORCE", raising=False)
            result = subprocess.run(
                ["make", "verify-enforcement"],
                capture_output=True, text=True,
                cwd=str(ROOT),
                env={**os.environ},  # inherit cleaned env
            )
            assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
            assert "PASS" in result.stdout
        finally:
            _cleanup_enf_state()

    def test_fails_when_enforcement_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "verify-enforcement"],
                capture_output=True, text=True,
                cwd=str(ROOT),
                env={**os.environ, "GLUDD_FLOOR_ENFORCE": "0"},
            )
            assert result.returncode != 0, (
                "verify-enforcement should fail when GLUDD_FLOOR_ENFORCE=0"
            )
        finally:
            _cleanup_enf_state()


@pytest.mark.parametrize("make_flags", [(), ("-e",)])
def test_native_defaults_are_confined_before_execution(make_flags: tuple[str, ...]) -> None:
    """The original shared-path boundary is visible without executing its writes."""
    result = subprocess.run(
        ["make", "--no-print-directory", *make_flags, "-n", "reload-enforcement"],
        cwd=ROOT, env=_isolated_make_env(), capture_output=True, text=True,
        check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not (ROOT / "clean-ran").exists(), "dry-run must not execute the cleaner"
    assert "/tmp/gludd-watchdog-disengage.json" not in result.stdout
    assert "/tmp/gludd-floor-override" not in result.stdout

    witness = ROOT / "unbound.mk"
    witness.write_text(_native_rules(), encoding="utf-8")
    unbound = subprocess.run(
        ["make", "--no-print-directory", *make_flags, "-n", "-f", str(witness),
         "rearm-enforcement"],
        cwd=ROOT, env=_isolated_make_env(), capture_output=True, text=True,
        check=False, timeout=15,
    )
    assert unbound.returncode == 0, unbound.stderr
    assert "rm -f /tmp/gludd-watchdog-disengage.json" in unbound.stdout


@pytest.mark.parametrize("make_flags", [(), ("-e",)])
def test_native_reload_confines_cleanup_and_keeps_unrelated_carriers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_flags: tuple[str, ...],
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    decoy = outside / "gludd-stop-state.json"
    decoy.write_bytes(b"outside-owned-state")
    candidate = ROOT / "cleanup" / "gludd-stop-state.json"
    candidate.write_bytes(b"owned-cleanup-candidate")
    original_rules = _native_rules()
    monkeypatch.setenv("GLUDD_ENFORCEMENT_TEST_CARRIER", "retained-by-real-cleaner")
    monkeypatch.setenv("MAKEFLAGS", "--environment-overrides -- fixture_input=foreign")
    monkeypatch.setenv("MFLAGS", "--silent")
    monkeypatch.setenv("MAKEOVERRIDES", "fixture_input=foreign")
    environment = _isolated_make_env()
    assert all(name not in environment for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"))
    result = subprocess.run(
        ["make", "--no-print-directory", *make_flags, "reload-enforcement"],
        cwd=ROOT, env=environment, capture_output=True, text=True,
        check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert (ROOT / "clean-ran").read_text(encoding="utf-8") == "retained-by-real-cleaner"
    assert not candidate.exists()
    assert decoy.read_bytes() == b"outside-owned-state"
    assert _native_rules() == original_rules
