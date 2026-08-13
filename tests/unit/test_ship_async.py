"""Tests for scripts/ship_async.sh — non-blocking ship pipeline."""
import os
import pathlib
import subprocess
import time

SCRIPT = pathlib.Path(__file__).parent.parent.parent / "scripts" / "ship_async.sh"


def make_temp_repo(tmp_path):
    """Create a git repo with master at commit1, feature at commit2."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    # commit1 on master
    (repo / "file.txt").write_text("v1")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "c1"], check=True, capture_output=True)
    commit1 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True
    ).stdout.strip()
    # commit2 on feature branch
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature"], check=True, capture_output=True)
    (repo / "file.txt").write_text("v2")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "c2"], check=True, capture_output=True)
    commit2 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True
    ).stdout.strip()
    # go back to master
    subprocess.run(["git", "-C", str(repo), "checkout", "master"], check=True, capture_output=True)
    return repo, commit1, commit2


def make_diverged_repo(tmp_path):
    """Create a git repo with diverged master and feature branches."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    # base commit
    (repo / "file.txt").write_text("base")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
    # diverge: make master advance
    (repo / "file.txt").write_text("master-v2")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "master-v2"], check=True, capture_output=True)
    master_tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True
    ).stdout.strip()
    # feature branch from base (not from master tip)
    subprocess.run(["git", "-C", str(repo), "checkout", "HEAD~1", "-b", "feature"], check=True, capture_output=True)
    (repo / "other.txt").write_text("feature")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "feature-v1"], check=True, capture_output=True)
    feature_tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "master"], check=True, capture_output=True)
    return repo, master_tip, feature_tip


class TestShipAsync:
    def test_gate_pass_fast_forwards(self, tmp_path):
        """Gate pass: master should be fast-forwarded to feature tip."""
        repo, _commit1, commit2 = make_temp_repo(tmp_path)
        status_file = tmp_path / ".ship-status"
        lock_file = tmp_path / "ship.lock"

        env = os.environ.copy()
        env["GATE_CMD"] = "exit 0"
        env["SHIP_LOCK_FILE"] = str(lock_file)
        env["REPO_DIR"] = str(repo)
        env["STATUS_FILE"] = str(status_file)

        result = subprocess.run(
            ["bash", str(SCRIPT), "feature", "master"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        master_sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "master"],
            check=True, capture_output=True, text=True
        ).stdout.strip()
        assert master_sha == commit2, f"master should be at commit2 ({commit2}), got {master_sha}"

        status = status_file.read_text()
        assert "SHIP PASS" in status, f"Expected SHIP PASS in status, got: {status}"

    def test_gate_fail_leaves_target_unchanged(self, tmp_path):
        """Gate fail: master should remain at commit1."""
        repo, commit1, _commit2 = make_temp_repo(tmp_path)
        status_file = tmp_path / ".ship-status"
        lock_file = tmp_path / "ship.lock"

        env = os.environ.copy()
        env["GATE_CMD"] = "exit 1"
        env["SHIP_LOCK_FILE"] = str(lock_file)
        env["REPO_DIR"] = str(repo)
        env["STATUS_FILE"] = str(status_file)

        result = subprocess.run(
            ["bash", str(SCRIPT), "feature", "master"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, f"Expected non-zero exit, got {result.returncode}"

        master_sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "master"],
            check=True, capture_output=True, text=True
        ).stdout.strip()
        assert master_sha == commit1, f"master should still be at commit1 ({commit1}), got {master_sha}"

        status = status_file.read_text()
        assert "SHIP FAIL gate" in status, f"Expected 'SHIP FAIL gate' in status, got: {status}"

    def test_non_ff_refused(self, tmp_path):
        """Non-ff merge: should fail and leave TARGET unchanged."""
        repo, master_tip, _feature_tip = make_diverged_repo(tmp_path)
        status_file = tmp_path / ".ship-status"
        lock_file = tmp_path / "ship.lock"

        env = os.environ.copy()
        env["GATE_CMD"] = "exit 0"
        env["SHIP_LOCK_FILE"] = str(lock_file)
        env["REPO_DIR"] = str(repo)
        env["STATUS_FILE"] = str(status_file)

        result = subprocess.run(
            ["bash", str(SCRIPT), "feature", "master"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit for non-ff, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        current_master = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "master"],
            check=True, capture_output=True, text=True
        ).stdout.strip()
        assert current_master == master_tip, f"master should still be at {master_tip}, got {current_master}"

        if status_file.exists():
            status = status_file.read_text()
            assert "SHIP FAIL" in status, f"Expected SHIP FAIL in status, got: {status}"

    def test_concurrent_lock_refused(self, tmp_path):
        """Second concurrent invocation must be refused quickly."""
        repo, _commit1, _commit2 = make_temp_repo(tmp_path)
        status_file = tmp_path / ".ship-status"
        lock_file = tmp_path / "ship.lock"

        env = os.environ.copy()
        env["GATE_CMD"] = "sleep 5"
        env["SHIP_LOCK_FILE"] = str(lock_file)
        env["REPO_DIR"] = str(repo)
        env["STATUS_FILE"] = str(status_file)

        # Start first invocation in background (slow gate)
        bg = subprocess.Popen(
            ["bash", str(SCRIPT), "feature", "master"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Give the first process a moment to acquire the lock
        time.sleep(0.5)

        # Second invocation should be rejected quickly
        start = time.time()
        result2 = subprocess.run(
            ["bash", str(SCRIPT), "feature", "master"],
            env=env,
            capture_output=True,
            text=True,
        )
        elapsed = time.time() - start

        # Clean up background process
        bg.terminate()
        try:
            bg.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            bg.kill()
            bg.communicate()

        assert result2.returncode != 0, f"Second invocation should fail, got returncode={result2.returncode}"
        assert elapsed < 3.0, f"Second invocation should fail fast (< 3s), took {elapsed:.1f}s"
