"""Hermetic paths and cleanup fixtures for hook runtime tests."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_OPENCODE_DIR = Path(os.environ.get("OPENCODE_DIR", str(ROOT / ".opencode"))).resolve()
PLUGIN_DIR = _OPENCODE_DIR / "plugin"
LIB_DIR = _OPENCODE_DIR / "lib"

# Skip the entire module when the plugin directory is absent.
# This lets operators move `.opencode/` aside as a workaround for broken plugins
# without the test suite reporting failures. When `.opencode/` IS present, every
# test runs and must pass — no vacuous pass.
_PLUGINS_PRESENT = PLUGIN_DIR.is_dir() and any(PLUGIN_DIR.glob("*.ts"))
pytestmark = pytest.mark.skipif(
    not _PLUGINS_PRESENT,
    reason=f"no plugins found under {PLUGIN_DIR} (set OPENCODE_DIR=... to test a different location)",
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_tmp_counter = 0

_GLOBAL_RUNTIME_STATE_NAMES = frozenset(
    {
        "gludd-block-counter.json",
        "gludd-dispatch-outcomes.json",
        "gludd-force-dispatch.json",
        "gludd-hot-delegate.js",
        "gludd-hot-enforce-session-start.js",
        "gludd-hot-enforce-verified-claims.js",
        "gludd-multitask-state.json",
        "gludd-persist-stop-block.json",
        "gludd-post-results-state.json",
        "gludd-text-only-state.json",
        "gludd-tool-streak.json",
        "gludd-watchdog-disengage.json",
    }
)

_LEGACY_WORKSPACE_TEMP_NAMES = (
    "_hook_test_dirty_temp.txt",
    "_hook_test_dirty_temp2.txt",
    "_hook_test_dirty_temp3.txt",
    "_hook_test_dirty_runtime.txt",
    "_hook_test_dirty_disabled.txt",
    "_hook_test_dirty_subagent.txt",
    "_hook_test_dirty_nondispatch.txt",
)

def _runtime_state_root() -> Path:
    configured = os.environ.get("GLUDD_RUNTIME_TEST_STATE_DIR")
    if configured:
        return Path(configured).resolve()
    return Path(tempfile.gettempdir()).resolve()


def _runtime_state_path(path: str) -> str:
    """Redirect known machine-global state into the verifier-owned directory."""
    configured = os.environ.get("GLUDD_RUNTIME_TEST_STATE_DIR")
    candidate = Path(path)
    if configured and candidate.parent == Path("/tmp") and candidate.name in _GLOBAL_RUNTIME_STATE_NAMES:
        return str(Path(configured).resolve() / candidate.name)
    return path


def _dirty_test_path(label: str) -> str:
    """Return a dirty-fixture path INSIDE the checkout (scripts/).

    The enforce-clean-tree hook runs `git status --porcelain` at the repo
    root; a fixture outside the checkout is invisible to it, so the deny
    path can never fire and the deny-expecting tests would be vacuously
    broken. The session-scoped cleanup fixture globs and removes
    `scripts/gludd-hook-test-dirty-*.txt` leftovers so a crashed test can
    never leave the real tree dirty (which would self-lock the plugin).
    """
    return str(ROOT / "scripts" / f"gludd-hook-test-dirty-{label}-{os.getpid()}.txt")


def _remove_legacy_workspace_artifacts() -> None:
    """Remove exact historical harness artifacts and dirty-fixture leftovers.

    The dirty-fixture glob is bounded to `scripts/gludd-hook-test-dirty-*.txt`
    — files this harness itself creates — never arbitrary repo files.
    """
    for name in _LEGACY_WORKSPACE_TEMP_NAMES:
        with contextlib.suppress(OSError):
            (ROOT / "scripts" / name).unlink()
    for leftover in (ROOT / "scripts").glob("gludd-hook-test-dirty-*.txt"):
        with contextlib.suppress(OSError):
            leftover.unlink()


@pytest.fixture(scope="session", autouse=True)
def _cleanup_legacy_workspace_artifacts() -> Iterator[None]:
    """Clean legacy checkout artifacts and dirty-fixture leftovers before and after the runtime suite."""
    _remove_legacy_workspace_artifacts()
    yield
    _remove_legacy_workspace_artifacts()

def _clean_state_files(*paths: str) -> None:
    """Remove state files before/after tests without touching live global state."""
    for path in paths:
        with contextlib.suppress(OSError):
            os.unlink(_runtime_state_path(path))


def _with_open_work(env: dict[str, str], tmp_tasks: str) -> tuple[dict[str, str], str]:
    """Create a temp TASKS.md with unchecked items so openWorkExists() returns true."""
    tasks_path = os.path.join("/tmp", f"gludd-test-tasks-{os.getpid()}.md")
    with open(tasks_path, "w") as f:
        f.write("- [ ] test task 1\n- [ ] test task 2\n")
    env["GLUDD_TASKS_MD"] = tasks_path
    return env, tasks_path


def _hermetic_project_root(tmp_path: Path) -> Path:
    """Create an isolated project root with deterministic pending-work signals.

    The fixture produces one unchecked milestone-range TASKS.md item and one
    backlog item outside the active milestone, a green .gate-status, empty
    BUGS.md/config/ratchet.yml, and an empty .ci-status.  This lets
    enforce-stop runtime tests assert on specific block reasons without
    coupling to the real repository's CI/gate state.
    """
    root = tmp_path / "project_root"
    root.mkdir(parents=True)
    (root / "config").mkdir()
    (root / "BUGS.md").write_text("")
    (root / "config" / "ratchet.yml").write_text("")
    (root / ".gate-status").write_text("=== GATE: PASSED ===\n")
    (root / ".ci-status").write_text("")
    tasks = (
        "The v0.1.1 milestone is the exact task set S83.157-S83.168.\n\n"
        "- [ ] S83.157 milestone-range task\n"
        "- [ ] S84.001 backlog item\n"
    )
    (root / "TASKS.md").write_text(tasks)
    return root
