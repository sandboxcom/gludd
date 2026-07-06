"""Tests for scripts/gated_merge.sh.

All tests use subprocess shims — no real git, no real pytest/make.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "gated_merge.sh"


def _write_executable(path: Path, content: str) -> None:
    """Write a shell script and make it executable."""
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_git_shim(tmpdir: Path, log_file: Path, merge_conflicts: list[str]) -> Path:
    """Create a git shim that records calls and fails on specified refs.

    Args:
        tmpdir: directory to write the shim into
        log_file: file to record all calls to
        merge_conflicts: list of ref names that should fail on 'merge'
    """
    conflict_checks = ""
    for ref in merge_conflicts:
        # Match the ref as the last argument to 'merge'
        conflict_checks += (
            f'    "{ref}") echo "git merge CONFLICT: {ref}"'
            f' >> "$LOG_FILE"; exit 1;;\n'
        )

    shim = tmpdir / "git"
    _write_executable(
        shim,
        f"""#!/usr/bin/env bash
LOG_FILE="{log_file}"
echo "git $*" >> "$LOG_FILE"

case "$1" in
  merge)
    # Check if any of the known conflict refs appear in args
    case "${{@: -1}}" in
{conflict_checks}
      *) echo "git merge OK: $*" >> "$LOG_FILE"; exit 0;;
    esac
    ;;
  "merge")
    # merge --abort
    exit 0
    ;;
  reset)
    echo "git reset $*" >> "$LOG_FILE"; exit 0;;
  rev-parse)
    echo "abc1234"; exit 0;;
  *)
    exit 0;;
esac
""",
    )
    return shim


def _make_collect_stub(tmpdir: Path, exit_code: int) -> str:
    """Create a collect command stub that returns exit_code."""
    stub = tmpdir / "collect_stub.sh"
    _write_executable(stub, f"#!/usr/bin/env bash\nexit {exit_code}\n")
    return str(stub)


def _run_script(
    *,
    base: str,
    branches: str,
    strategy: str = "stop-on-conflict",
    git_cmd: str,
    collect_cmd: str,
    manifest_path: str,
    lock_file: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run gated_merge.sh with given parameters."""
    env = os.environ.copy()
    env.update(
        {
            "BASE": base,
            "BRANCHES": branches,
            "MERGE_STRATEGY": strategy,
            "GIT_CMD": git_cmd,
            "COLLECT_CMD": collect_cmd,
            "MANIFEST": manifest_path,
        }
    )
    if lock_file is not None:
        env["LOCK_FILE"] = lock_file
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# (a) all_clean — all refs merge cleanly, collect passes
# ---------------------------------------------------------------------------
class TestAllClean:
    def test_all_clean(self, tmp_path: Path) -> None:
        log_file = tmp_path / "git_calls.log"
        log_file.write_text("")
        git_shim = _make_git_shim(tmp_path, log_file, merge_conflicts=[])
        collect_cmd = _make_collect_stub(tmp_path, 0)
        manifest = tmp_path / "manifest.txt"

        result = _run_script(
            base="main",
            branches="feat/a feat/b feat/c",
            git_cmd=str(git_shim),
            collect_cmd=collect_cmd,
            manifest_path=str(manifest),
            lock_file=str(tmp_path / "gated-merge.lock"),
        )

        rc = result.returncode
        assert rc == 0, (
            f"expected 0, got {rc}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        content = manifest.read_text()
        assert "feat/a MERGED" in content
        assert "feat/b MERGED" in content
        assert "feat/c MERGED" in content
        assert "HEAD abc1234" in content


# ---------------------------------------------------------------------------
# (b) stop_on_conflict — feat/b conflicts, stop mode
# ---------------------------------------------------------------------------
class TestStopOnConflict:
    def test_stop_on_conflict(self, tmp_path: Path) -> None:
        log_file = tmp_path / "git_calls.log"
        log_file.write_text("")
        git_shim = _make_git_shim(tmp_path, log_file, merge_conflicts=["feat/b"])
        collect_cmd = _make_collect_stub(tmp_path, 0)
        manifest = tmp_path / "manifest.txt"

        result = _run_script(
            base="main",
            branches="feat/a feat/b feat/c",
            strategy="stop-on-conflict",
            git_cmd=str(git_shim),
            collect_cmd=collect_cmd,
            manifest_path=str(manifest),
            lock_file=str(tmp_path / "gated-merge.lock"),
        )

        # stop-on-conflict exits 0 (conflict is not a hard error, just a stop)
        rc = result.returncode
        assert rc == 0, (
            f"unexpected exit {rc}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        content = manifest.read_text()
        assert "feat/a MERGED" in content
        assert "feat/b CONFLICT" in content
        assert "feat/c SKIPPED" in content

        # Confirm reset --hard main was called
        git_log = log_file.read_text()
        assert "reset --hard main" in git_log


# ---------------------------------------------------------------------------
# (c) skip_conflict — feat/b conflicts, skip mode
# ---------------------------------------------------------------------------
class TestSkipConflict:
    def test_skip_conflict(self, tmp_path: Path) -> None:
        log_file = tmp_path / "git_calls.log"
        log_file.write_text("")
        git_shim = _make_git_shim(tmp_path, log_file, merge_conflicts=["feat/b"])
        collect_cmd = _make_collect_stub(tmp_path, 0)
        manifest = tmp_path / "manifest.txt"

        result = _run_script(
            base="main",
            branches="feat/a feat/b feat/c",
            strategy="skip-conflict",
            git_cmd=str(git_shim),
            collect_cmd=collect_cmd,
            manifest_path=str(manifest),
            lock_file=str(tmp_path / "gated-merge.lock"),
        )

        rc = result.returncode
        assert rc == 0, (
            f"unexpected exit {rc}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        content = manifest.read_text()
        assert "feat/a MERGED" in content
        assert "feat/b CONFLICT" in content
        assert "feat/c MERGED" in content  # c was still attempted and succeeded


# ---------------------------------------------------------------------------
# (d) collection_fail — collect fails after first merge
# ---------------------------------------------------------------------------
class TestCollectionFail:
    def test_collection_fail(self, tmp_path: Path) -> None:
        log_file = tmp_path / "git_calls.log"
        log_file.write_text("")
        git_shim = _make_git_shim(tmp_path, log_file, merge_conflicts=[])
        collect_cmd = _make_collect_stub(tmp_path, 1)  # always fails
        manifest = tmp_path / "manifest.txt"

        result = _run_script(
            base="main",
            branches="feat/a feat/b feat/c",
            git_cmd=str(git_shim),
            collect_cmd=collect_cmd,
            manifest_path=str(manifest),
            lock_file=str(tmp_path / "gated-merge.lock"),
        )

        rc = result.returncode
        assert rc == 1, (
            f"expected 1, got {rc}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        content = manifest.read_text()
        # feat/a triggers the collection failure
        assert "feat/a FAIL" in content
        # remaining are SKIPPED
        assert "feat/b SKIPPED" in content
        assert "feat/c SKIPPED" in content

        # Confirm reset was called
        git_log = log_file.read_text()
        assert "reset --hard main" in git_log


# ---------------------------------------------------------------------------
# (e) flock_refused — second invocation cannot acquire lock
# ---------------------------------------------------------------------------
class TestFlockRefused:
    def test_flock_refused(self, tmp_path: Path) -> None:
        lock_path = str(tmp_path / "gated-merge.lock")
        log_file = tmp_path / "git_calls.log"
        log_file.write_text("")
        git_shim = _make_git_shim(tmp_path, log_file, merge_conflicts=[])
        collect_cmd = _make_collect_stub(tmp_path, 0)
        manifest = tmp_path / "manifest.txt"

        # Manually acquire the lock before launching the script
        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Now run the script — it should fail immediately
            result = _run_script(
                base="main",
                branches="feat/a feat/b",
                git_cmd=str(git_shim),
                collect_cmd=collect_cmd,
                manifest_path=str(manifest),
                lock_file=lock_path,
            )
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)

        rc = result.returncode
        assert rc == 1, (
            f"expected exit 1 (lock refused), got {rc}"
            f"\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert (
            "lock" in combined.lower()
            or "flock" in combined.lower()
            or "acquire" in combined.lower()
            or "running" in combined.lower()
        )
