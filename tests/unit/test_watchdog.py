"""Tests for watchdog detection functions: CI red after tag, release
completeness, secrets committed, stale release.

All tests mock external APIs (git tags, gh release view, make secrets-scan,
subprocess.run) using monkeypatch and temporary filesystem paths so they
run fully offline and never touch the real repo or network.

Design evidence: detect-secrets documents repository snapshots separately from
lower-overhead hooks (https://github.com/Yelp/detect-secrets), while pytest's
long-running dependency-injection discussion recommends argument passing when
the real dependency must not execute accidentally
(https://github.com/pytest-dev/pytest/issues/4576). Accordingly, cycle tests
inject the scan dependency and this module retains the exact adapter contract.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "agent_watchdog.py"


def _load_module() -> ModuleType:
    module_name = "agent_watchdog_test_watchdog"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


aw = _load_module()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _setup_aw_env(monkeypatch, tmp_path: Path):
    """Redirect all watchdog file paths into tmp_path for isolation."""
    monkeypatch.setattr(aw, "STREAK_FILE", str(tmp_path / "streak.json"))
    monkeypatch.setattr(aw, "MULTITASK_STATE_FILE", str(tmp_path / "multitask-state.json"))
    monkeypatch.setattr(aw, "WATCHDOG_ACTIVITY_FILE", str(tmp_path / "watchdog-activity.json"))
    monkeypatch.setattr(aw, "TODOWRITE_STATE", str(tmp_path / "todos.json"))
    monkeypatch.setattr(aw, "STOP_STATE", str(tmp_path / "stop-state.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_BLOCKS", str(tmp_path / "false-done-blocks.json"))
    monkeypatch.setattr(aw, "FALSE_DONE_MAXOUT", str(tmp_path / "false-done-maxout.json"))
    monkeypatch.setattr(aw, "CONTINUE_DIRECTIVE", str(tmp_path / "continue-directive.json"))
    monkeypatch.setattr(aw, "RESET_LOG", str(tmp_path / "reset.log"))
    monkeypatch.setattr(aw, "STOP_COUNT_FILE", str(tmp_path / "stop-count.json"))
    monkeypatch.setattr(aw, "LAST_FLAG_FILE", str(tmp_path / "last-flag.json"))
    monkeypatch.setattr(aw, "PURE_IDLE_DIRECTIVE", str(tmp_path / "pure-idle.txt"))
    monkeypatch.setattr(aw, "TASK_DEADLINES_FILE", str(tmp_path / "deadlines.json"))
    monkeypatch.setattr(aw, "ANOMALY_COUNT_FILE", str(tmp_path / "anomaly-count.json"))
    monkeypatch.setattr(aw, "STALLED_TASKS_FILE", str(tmp_path / "stalled-tasks.txt"))
    monkeypatch.setattr(aw, "EX_STALLED_TASKS_FILE", str(tmp_path / "ex-stalled.json"))
    monkeypatch.setattr(aw, "EX_ANOMALIES_FILE", str(tmp_path / "ex-anomalies.json"))
    monkeypatch.setattr(aw, "TASK_ANOMALIES_FILE", str(tmp_path / "task-anomalies.json"))
    monkeypatch.setattr(aw, "TASK_TIMING_FILE", str(tmp_path / "task-timing.json"))
    monkeypatch.setattr(aw, "TASK_HISTORY_FILE", str(tmp_path / "task-history.json"))
    monkeypatch.setattr(aw, "TASK_STATE_FILE", str(tmp_path / "task-state.json"))
    monkeypatch.setattr(aw, "TASK_STATE_SNAPSHOT", str(tmp_path / "task-state-snapshot.json"))
    monkeypatch.setattr(aw, "TIMING_DATA_FILE", str(tmp_path / "timing-data.json"))
    monkeypatch.setattr(aw, "PUSH_FLAG", str(tmp_path / "push-flag-nonexistent"))
    monkeypatch.setattr(aw, "EX_TASKS_DIR", str(tmp_path / "tasks-dir-nonexistent"))
    monkeypatch.setattr(aw, "CI_CACHE_FILE", str(tmp_path / "ci-cache.json"))
    monkeypatch.setattr(aw, "DURATIONS_FILE", str(tmp_path / "durations.json"))
    monkeypatch.setattr(aw, "GATE_PID_FILE", tmp_path / "gate-pid-nonexistent")
    monkeypatch.setattr(aw, "_TASKS_MD", tmp_path / "TASKS.md")
    monkeypatch.setattr(aw, "_RATCHET_YML", tmp_path / "ratchet.yml")
    monkeypatch.setattr(aw, "_GATE_STATUS", tmp_path / ".gate-status")
    monkeypatch.setattr(aw, "_CHECK_COOLDOWN_FILE", str(tmp_path / "check-cooldowns.json"))
    monkeypatch.setattr(aw, "HEARTBEAT_FILE", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr(aw, "ORCHESTRATOR_STATE_FILE", str(tmp_path / "orchestrator.json"))
    monkeypatch.setattr(aw, "HEALTH_SCORE_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(aw, "PUSH_LOOP_FILE", str(tmp_path / "push-ts.json"))
    monkeypatch.setattr(aw, "DISENGAGE_FILE", str(tmp_path / "disengage.json"))
    monkeypatch.setattr(aw, "RELEASE_COMPLETENESS_FILE", str(tmp_path / "release-completeness.json"))
    monkeypatch.setattr(aw, "SECRETS_VIOLATION_FILE", str(tmp_path / "secrets-violation.json"))
    monkeypatch.setattr(aw, "STALE_RELEASE_FILE", str(tmp_path / "stale-release.json"))
    monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: True)
    monkeypatch.setattr(aw, "_mark_check_run", lambda name: None)

    (tmp_path / "TASKS.md").write_text("- [x] all done\n")
    (tmp_path / "ratchet.yml").write_text("# empty\n")
    (tmp_path / "todos.json").write_text("[]")
    (tmp_path / "stop-count.json").write_text('{"count":0}')
    (tmp_path / "push-flag-nonexistent").write_text("")
    (tmp_path / "tasks-dir-nonexistent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "check-cooldowns.json").write_text("{}")


def _mock_subprocess_output(monkeypatch, stdout="", stderr="", returncode=0):
    """Mock subprocess.run to return controlled output."""
    class MockResult:
        def __init__(self, stdout, stderr, returncode):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    mock_result = MockResult(stdout, stderr, returncode)

    def _mock_run(cmd, **kwargs):
        return mock_result

    monkeypatch.setattr(aw.subprocess, "run", _mock_run)


# ──────────────────────────────────────────────────────────────────────────────
# Test: _check_ci_red_after_tag_push
# ──────────────────────────────────────────────────────────────────────────────


class TestCiRedAfterTagPush:
    def test_no_tags_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [])
        assert aw._check_ci_red_after_tag_push() is None

    def test_ci_failure_on_tag_returns_dict(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v1.0.0", "abc1234567890abcdef1234567890abcdef12")])
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": (
                '{"status":"completed","conclusion":"failure",'
                '"databaseId":42,"createdAt":"2026-01-01T00:00:00Z"}'
            ),
            "stderr": "",
            "returncode": 0,
        })())
        result = aw._check_ci_red_after_tag_push()
        assert result is not None
        assert result["ci_red_after_tag"] is True
        assert result["tag"] == "v1.0.0"
        assert result["conclusion"] == "failure"
        assert result["run_id"] == 42

    def test_ci_success_on_tag_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v2.0.0", "def5678")])
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": '{"status":"completed","conclusion":"success","databaseId":99}',
            "stderr": "",
            "returncode": 0,
        })())
        assert aw._check_ci_red_after_tag_push() is None

    def test_no_gh_run_for_tag_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v3.0.0", "fff9999")])
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": "",
            "stderr": "",
            "returncode": 0,
        })())
        assert aw._check_ci_red_after_tag_push() is None

    def test_gh_timeout_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v4.0.0", "aaa1111")])
        import subprocess as sp

        def _raise_timeout(cmd, **kwargs):
            raise sp.TimeoutExpired(cmd, 15)

        monkeypatch.setattr(aw.subprocess, "run", _raise_timeout)
        assert aw._check_ci_red_after_tag_push() is None


# ──────────────────────────────────────────────────────────────────────────────
# Test: _check_release_completeness
# ──────────────────────────────────────────────────────────────────────────────


class TestReleaseCompleteness:
    def test_no_tags_writes_clean_state(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: [])
        result = aw._check_release_completeness()
        assert result is None
        data = json.loads((tmp_path / "release-completeness.json").read_text())
        assert data["incomplete"] is False
        assert data["reason"] == "no tags found"

    def test_tag_with_no_release(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: ["v1.0.0-beta.1"])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (False, {}))
        result = aw._check_release_completeness()
        assert result is not None
        assert result["incomplete"] is True
        assert result["reason"] == "no release created"
        assert result["assetCount"] == 0

    def test_draft_release_with_zero_assets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: ["v2.0.0"])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (True, {
            "isDraft": True, "isPrerelease": True, "assetCount": 0,
            "publishedAt": "", "url": "",
        }))
        result = aw._check_release_completeness()
        assert result is not None
        assert result["incomplete"] is True
        assert "draft" in result["reason"].lower()

    def test_published_release_with_assets_is_complete(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: ["v3.0.0"])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (True, {
            "isDraft": False, "isPrerelease": False, "assetCount": 5,
            "publishedAt": "2026-01-01T00:00:00Z", "url": "https://example.com/release",
        }))
        result = aw._check_release_completeness()
        assert result is None
        data = json.loads((tmp_path / "release-completeness.json").read_text())
        assert data["incomplete"] is False
        assert data["assetCount"] == 5

    def test_published_release_with_zero_assets_is_incomplete(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: ["v4.0.0"])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (True, {
            "isDraft": False, "isPrerelease": False, "assetCount": 0,
            "publishedAt": "2026-01-01T00:00:00Z", "url": "https://example.com/release",
        }))
        result = aw._check_release_completeness()
        assert result is not None
        assert result["incomplete"] is True
        assert result["reason"] == "zero artifacts"

    def test_gh_api_error_skips_check(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: ["v5.0.0"])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (False, {"_error": "timeout"}))
        result = aw._check_release_completeness()
        assert result is None

    def test_completeness_state_file_written(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: ["v6.0.0"])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (True, {
            "isDraft": False, "isPrerelease": False, "assetCount": 3,
            "publishedAt": "2026-01-01T00:00:00Z", "url": "https://example.com/release",
        }))
        aw._check_release_completeness()
        state_file = tmp_path / "release-completeness.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert "ts" in data
        assert data["tag"] == "v6.0.0"
        assert data["incomplete"] is False


# ──────────────────────────────────────────────────────────────────────────────
# Test: _check_secrets_committed
# ──────────────────────────────────────────────────────────────────────────────


class TestSecretsCommitted:
    def test_clean_scan_writes_no_violation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        calls = []

        def _run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return type("R", (), {
                "stdout": "No secrets found\n",
                "stderr": "",
                "returncode": 0,
            })()

        monkeypatch.setattr(aw.subprocess, "run", _run)
        result = aw._check_secrets_committed()
        assert result is None
        assert calls == [
            (["make", "secrets-scan"], {
                "capture_output": True,
                "text": True,
                "timeout": 60,
                "cwd": str(aw._WORKSPACE),
            })
        ]
        data = json.loads((tmp_path / "secrets-violation.json").read_text())
        assert data["violation"] is False

    def test_secrets_found_writes_violation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": "SECRET FOUND: AWS key in src/leak.py\n",
            "stderr": "",
            "returncode": 1,
        })())
        result = aw._check_secrets_committed()
        assert result is not None
        assert result["violation"] is True
        assert result["exit_code"] == 1
        data = json.loads((tmp_path / "secrets-violation.json").read_text())
        assert data["violation"] is True

    def test_scan_timeout_records_reason(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        import subprocess as sp

        def _raise_timeout(cmd, **kwargs):
            raise sp.TimeoutExpired(cmd, 60)

        monkeypatch.setattr(aw.subprocess, "run", _raise_timeout)
        result = aw._check_secrets_committed()
        assert result is not None
        assert result["violation"] is None
        assert result["reason"] == "timeout"

    def test_cooldown_respected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: False)
        assert aw._check_secrets_committed() is None


# ──────────────────────────────────────────────────────────────────────────────
# Test: _check_stale_release
# ──────────────────────────────────────────────────────────────────────────────


class TestStaleRelease:
    def test_no_tags_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [])
        assert aw._check_stale_release() is None

    def test_recent_tag_not_stale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        now_ts = int(time.time())
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v1.0.0", "abc12345")])
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": str(now_ts),
            "stderr": "",
            "returncode": 0,
        })())
        result = aw._check_stale_release()
        assert result is None
        data = json.loads((tmp_path / "stale-release.json").read_text())
        assert data.get("stale") is False

    def test_old_tag_without_release_is_stale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        old_ts = int(time.time()) - 3600
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v0.9.0", "deadbeef")])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (False, {}))

        call_count = [0]

        def _mock_run(cmd, **kwargs):
            call_count[0] += 1
            if "git log" in " ".join(cmd):
                return type("R", (), {"stdout": str(old_ts), "stderr": "", "returncode": 0})()
            return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

        monkeypatch.setattr(aw.subprocess, "run", _mock_run)
        result = aw._check_stale_release()
        assert result is not None
        assert result["stale"] is True
        assert result["findings"][0]["tag"] == "v0.9.0"
        assert result["findings"][0]["reason"] == "no release created within timeout"

    def test_old_tag_with_existing_release_not_stale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        old_ts = int(time.time()) - 3600
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v0.8.0", "cafebabe")])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (True, {
            "isDraft": False, "isPrerelease": False, "assetCount": 1,
            "publishedAt": "2026-01-01T00:00:00Z", "url": "https://example.com/release",
        }))

        def _mock_run(cmd, **kwargs):
            if "git log" in " ".join(cmd):
                return type("R", (), {"stdout": str(old_ts), "stderr": "", "returncode": 0})()
            return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

        monkeypatch.setattr(aw.subprocess, "run", _mock_run)
        result = aw._check_stale_release()
        assert result is None
        data = json.loads((tmp_path / "stale-release.json").read_text())
        assert data.get("stale") is False

    def test_multiple_stale_tags_detect_only_latest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        old_ts = int(time.time()) - 3600
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [
            ("v0.9.0", "deadbeef"),
            ("v0.8.0", "cafebabe"),
        ])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (False, {}))

        def _mock_run(cmd, **kwargs):
            if "git log" in " ".join(cmd):
                return type("R", (), {"stdout": str(old_ts), "stderr": "", "returncode": 0})()
            return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

        monkeypatch.setattr(aw.subprocess, "run", _mock_run)
        result = aw._check_stale_release()
        assert result is not None
        assert len(result["findings"]) == 1
        assert result["findings"][0]["tag"] == "v0.9.0"

    def test_gh_timeout_does_not_flag_stale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        old_ts = int(time.time()) - 3600
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v0.7.0", "bbbbbbbb")])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (False, {"_error": "timeout"}))

        def _mock_run(cmd, **kwargs):
            if "git log" in " ".join(cmd):
                return type("R", (), {"stdout": str(old_ts), "stderr": "", "returncode": 0})()
            return type("R", (), {"stdout": "", "stderr": "", "returncode": 0})()

        monkeypatch.setattr(aw.subprocess, "run", _mock_run)
        result = aw._check_stale_release()
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# Test: Helper functions
# ──────────────────────────────────────────────────────────────────────────────


class TestHelperFunctions:
    def test_get_tags_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": "", "stderr": "", "returncode": 0,
        })())
        assert aw._get_tags() == []

    def test_get_tags_returns_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": "v1.0.0\nv0.9.0\nv0.8.0\n",
            "stderr": "", "returncode": 0,
        })())
        tags = aw._get_tags()
        assert len(tags) == 3
        assert tags[0] == "v1.0.0"
        assert tags[-1] == "v0.8.0"

    def test_get_tags_with_commits(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": "v1.0.0 a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0\n"
                       "v0.9.0 deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n",
            "stderr": "", "returncode": 0,
        })())
        pairs = aw._get_tags_with_commits()
        assert len(pairs) == 2
        assert pairs[0] == ("v1.0.0", "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0")
        assert pairs[1] == ("v0.9.0", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

    def test_gh_release_exists_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        release_json = json.dumps({
            "isDraft": False,
            "isPrerelease": False,
            "assets": [{"name": "binary.tar.gz"}, {"name": "checksums.txt"}],
            "publishedAt": "2026-01-01T00:00:00Z",
            "url": "https://github.com/x/y/releases/tag/v1.0.0",
        })
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": release_json, "stderr": "", "returncode": 0,
        })())
        exists, data = aw._gh_release_exists("v1.0.0")
        assert exists is True
        assert data["assetCount"] == 2
        assert data["isDraft"] is False
        assert data["isPrerelease"] is False

    def test_gh_release_exists_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": "release not found", "stderr": "", "returncode": 1,
        })())
        exists, data = aw._gh_release_exists("v99.0.0")
        assert exists is False
        assert data == {}

    def test_release_completeness_cooldown_respected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: False)
        assert aw._check_release_completeness() is None

    def test_stale_release_cooldown_respected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_should_run_check", lambda name, cooldown_secs=aw._CHECK_COOLDOWN_SECS: False)
        assert aw._check_stale_release() is None


# ──────────────────────────────────────────────────────────────────────────────
# Test: Integration with check_and_reset
# ──────────────────────────────────────────────────────────────────────────────


class TestCheckAndResetIntegration:
    def test_result_includes_release_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: ["v1.0.0"])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (True, {
            "isDraft": False, "isPrerelease": False, "assetCount": 0,
            "publishedAt": "2026-01-01T00:00:00Z", "url": "",
        }))
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v1.0.0", "abc12345")])
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)

        (tmp_path / "streak.json").write_text('{"count": 0, "last_tool": "write"}')
        (tmp_path / "streak.json").touch()

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert "release_incomplete" in result
        assert result["release_incomplete"] is not None
        assert result["release_incomplete"]["incomplete"] is True

    def test_result_includes_secrets_violation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: [])
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [])
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": "SECRET FOUND in secrets.py\n",
            "stderr": "",
            "returncode": 1,
        })())

        (tmp_path / "streak.json").write_text('{"count": 0, "last_tool": "write"}')
        (tmp_path / "streak.json").touch()

        result = aw.check_and_reset()
        assert "secrets_violation" in result
        assert result["secrets_violation"] is not None
        assert result["secrets_violation"]["violation"] is True

    def test_result_includes_ci_red_after_tag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: [])
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [("v1.0.0", "abc1234567890")])
        monkeypatch.setattr(aw, "_ci_is_pending_or_red", lambda: (False, None))
        monkeypatch.setattr(aw, "check_task_anomalies", lambda: {"tasks": [], "anomalies": [], "stalled": [], "ts": ""})
        monkeypatch.setattr(aw, "_check_push_stalled", lambda: None)
        monkeypatch.setattr(aw, "_check_task_anomaly_300s", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_pending_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_ci_stall", lambda: None)
        monkeypatch.setattr(aw, "_check_push_health", lambda: None)
        monkeypatch.setattr(aw, "check_task_timings", lambda: None)
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": '{"status":"completed","conclusion":"failure","databaseId":7}',
            "stderr": "",
            "returncode": 0,
        })())

        (tmp_path / "streak.json").write_text('{"count": 0, "last_tool": "write"}')
        (tmp_path / "streak.json").touch()

        result = aw.check_and_reset(secrets_check=lambda: None)
        assert "ci_red_after_tag" in result
        assert result["ci_red_after_tag"]["tag"] == "v1.0.0"
        assert result["ci_red_after_tag"]["conclusion"] == "failure"


# ──────────────────────────────────────────────────────────────────────────────
# Test: State file exports for enforce-stop.ts consumption
# ──────────────────────────────────────────────────────────────────────────────


class TestStateFilesForEnforceStop:
    def test_release_completeness_file_schema(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """enforce-stop.ts reads /tmp/gludd-release-completeness.json and checks
        the `incomplete` boolean field. The file must have the correct schema."""
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags", lambda: ["v1.0.0"])
        monkeypatch.setattr(aw, "_gh_release_exists", lambda tag: (False, {}))

        aw._check_release_completeness()
        data = json.loads((tmp_path / "release-completeness.json").read_text())
        assert "ts" in data
        assert "incomplete" in data
        assert "reason" in data
        assert isinstance(data["incomplete"], bool)
        assert isinstance(data["ts"], (int, float))

    def test_secrets_violation_file_schema(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw.subprocess, "run", lambda cmd, **kwargs: type("R", (), {
            "stdout": "", "stderr": "", "returncode": 0,
        })())

        aw._check_secrets_committed()
        data = json.loads((tmp_path / "secrets-violation.json").read_text())
        assert "ts" in data
        assert "violation" in data
        assert isinstance(data["violation"], bool)

    def test_stale_release_file_schema(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _setup_aw_env(monkeypatch, tmp_path)
        monkeypatch.setattr(aw, "_get_tags_with_commits", lambda: [])

        aw._check_stale_release()
        data = json.loads((tmp_path / "stale-release.json").read_text())
        assert "ts" in data
        assert "stale" in data
        assert isinstance(data["stale"], bool)
