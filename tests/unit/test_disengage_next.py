"""Tests for BP.5 granular disengage-next — single-use enforcement bypass.

Verifies:
  1. `make disengage-next` creates /tmp/gludd-disengage-next
  2. isDisengaged() returns true when the file exists
  3. The file is deleted after the first read (consume-once)
  4. A second isDisengaged() call returns false after consume
  5. Missing file → isDisengaged() returns false

The TS behavior tests invoke the actual shared.ts module via node so they
exercise the real consume-once logic rather than a Python re-implementation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED_TS = ROOT / ".opencode" / "lib" / "shared.ts"

NEXT_PATH = "/tmp/gludd-disengage-next"


def _node_is_disengaged(next_path: str) -> bool:
    """Invoke the real isDisengaged() from shared.ts with an isolated marker file.

    Uses GLUDD_DISENGAGE_NEXT_PATH so tests do not pollute the real /tmp state.
    """
    script = (
        "import('" + str(SHARED_TS) + "').then(m => {"
        "process.exit(m.isDisengaged() ? 0 : 1)"
        "}).catch(e => { console.error(e); process.exit(2); })"
    )
    env = {
        **os.environ,
        "GLUDD_DISENGAGE_NEXT_PATH": next_path,
        "GLUDD_DISENGAGE_PATH": next_path + ".persistent-noexist",
    }
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    if result.returncode == 2:
        raise RuntimeError(f"node failed to import shared.ts: {result.stderr}")
    return result.returncode == 0


class TestMakeTargetCreatesFile:
    """`make disengage-next` writes /tmp/gludd-disengage-next."""

    def setup_method(self):
        if os.path.exists(NEXT_PATH):
            os.remove(NEXT_PATH)

    def test_make_disengage_next_creates_file(self):
        result = subprocess.run(
            ["make", "disengage-next"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, (
            f"make disengage-next failed: {result.stderr}"
        )
        assert os.path.exists(NEXT_PATH), (
            "make disengage-next did not create /tmp/gludd-disengage-next"
        )

    def test_make_disengage_next_prints_confirmation(self):
        if not os.path.exists(NEXT_PATH):
            pass
        result = subprocess.run(
            ["make", "disengage-next"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
        )
        assert "DISENGAGED" in result.stdout, (
            "make disengage-next must print a DISENGAGED confirmation"
        )


class TestSharedTsConstant:
    """shared.ts defines DISENGAGE_NEXT_PATH with the right default."""

    def test_constant_exported(self):
        content = SHARED_TS.read_text(encoding="utf-8")
        assert "export const DISENGAGE_NEXT_PATH" in content, (
            "DISENGAGE_NEXT_PATH must be exported from shared.ts"
        )

    def test_default_path(self):
        content = SHARED_TS.read_text(encoding="utf-8")
        assert "/tmp/gludd-disengage-next" in content, (
            "DISENGAGE_NEXT_PATH must default to /tmp/gludd-disengage-next"
        )

    def test_env_override(self):
        content = SHARED_TS.read_text(encoding="utf-8")
        assert "GLUDD_DISENGAGE_NEXT_PATH" in content, (
            "DISENGAGE_NEXT_PATH must support GLUDD_DISENGAGE_NEXT_PATH env override"
        )


class TestIsDisengagedConsumeOnce:
    """isDisengaged() consume-once semantics over the dedicated marker file."""

    def test_returns_true_when_file_exists(self, tmp_path):
        marker = tmp_path / "next"
        marker.write_text("armed")
        assert _node_is_disengaged(str(marker)) is True, (
            "isDisengaged() must return true when the marker file exists"
        )

    def test_file_deleted_after_first_read(self, tmp_path):
        marker = tmp_path / "next"
        marker.write_text("armed")
        _node_is_disengaged(str(marker))
        assert not marker.exists(), (
            "isDisengaged() must delete the marker file on first read (consume-once)"
        )

    def test_second_call_returns_false_after_consume(self, tmp_path):
        marker = tmp_path / "next"
        marker.write_text("armed")
        first = _node_is_disengaged(str(marker))
        second = _node_is_disengaged(str(marker))
        assert first is True, "first call must return true"
        assert second is False, (
            "second call after consume must return false"
        )

    def test_missing_file_returns_false(self, tmp_path):
        marker = tmp_path / "absent"
        assert not marker.exists(), "fixture: marker should not exist"
        assert _node_is_disengaged(str(marker)) is False, (
            "isDisengaged() must return false when the marker file is absent"
        )


class TestRearmClearsNextFile:
    """rearm-enforcement and reload-enforcement clear the marker."""

    def test_rearm_removes_next_marker(self):
        Path(NEXT_PATH).write_text("armed")
        result = subprocess.run(
            ["make", "rearm-enforcement"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, f"rearm failed: {result.stderr}"
        assert not os.path.exists(NEXT_PATH), (
            "rearm-enforcement must remove /tmp/gludd-disengage-next"
        )
