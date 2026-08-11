"""Deep unit tests for ansible.file_tracker — FileChangeTracker + helpers."""

from __future__ import annotations

import subprocess

import pytest

from general_ludd.ansible.file_tracker import (
    FILE_MODULES,
    FileChangeTracker,
    _git_diff_name_status_range,
    _git_diff_name_status_working,
    _git_diff_range,
    _git_diff_working,
    _git_rev_parse,
    _task_uses_file_module,
)


class TestTaskUsesFileModule:
    def test_short_names_match(self):
        for name in ("copy", "template", "file", "blockinfile", "lineinfile", "replace", "assemble", "ini_file"):
            assert _task_uses_file_module(f"{name} some action")

    def test_fqcn_names_match(self):
        for name in FILE_MODULES:
            if "ansible.builtin." in name:
                assert _task_uses_file_module(f"{name} some action")

    def test_non_string_rejected(self):
        assert not _task_uses_file_module(None)
        assert not _task_uses_file_module(42)
        assert not _task_uses_file_module(["copy"])

    def test_non_file_modules_rejected(self):
        assert not _task_uses_file_module("command Run something")
        assert not _task_uses_file_module("shell Do stuff")
        assert not _task_uses_file_module("apt Install package")
        assert not _task_uses_file_module("service Restart nginx")

    def test_no_space_gets_whole_module_name(self):
        assert _task_uses_file_module("copy")
        assert not _task_uses_file_module("command")


class TestGitHelpers:
    def test_rev_parse_returns_sha(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        (git_dir / "f.txt").write_text("hi")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        sha = _git_rev_parse(git_dir, "HEAD")
        assert sha is not None
        assert len(sha) == 40

    def test_rev_parse_nonexistent_ref_returns_none(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        assert _git_rev_parse(git_dir, "HEAD") is None

    def test_git_diff_range_returns_text(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        (git_dir / "f.txt").write_text("a")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c1"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        sha1 = _git_rev_parse(git_dir, "HEAD")
        (git_dir / "f.txt").write_text("b")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c2"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        diff = _git_diff_range(git_dir, sha1)
        assert "-a" in diff or "+b" in diff

    def test_git_diff_working_returns_text(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        (git_dir / "f.txt").write_text("a")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c1"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        (git_dir / "f.txt").write_text("b")
        diff = _git_diff_working(git_dir)
        assert diff != ""

    def test_git_diff_name_status_range_returns_text(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        (git_dir / "f.txt").write_text("a")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c1"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        sha1 = _git_rev_parse(git_dir, "HEAD")
        (git_dir / "g.txt").write_text("new")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c2"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        result = _git_diff_name_status_range(git_dir, sha1)
        assert "g.txt" in result

    def test_git_diff_name_status_working_returns_text(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        (git_dir / "f.txt").write_text("a")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "c1"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        (git_dir / "f.txt").write_text("changed")
        result = _git_diff_name_status_working(git_dir)
        assert "f.txt" in result


class TestFileChangeTrackerInit:
    def test_captures_head_sha(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        (git_dir / "f.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        tracker = FileChangeTracker(repo_root=git_dir)
        assert tracker._git_sha_before is not None
        assert len(tracker._git_sha_before) == 40

    def test_empty_repo_head_is_none(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        tracker = FileChangeTracker(repo_root=git_dir)
        assert tracker._git_sha_before is None


class TestFileChangeTrackerEventHandler:
    @pytest.fixture
    def tracker(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        (git_dir / "f.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        return FileChangeTracker(repo_root=git_dir)

    def test_non_dict_event_ignored(self, tracker):
        tracker.event_handler("not a dict")
        tracker.event_handler(42)
        tracker.event_handler(None)
        assert len(tracker._file_events) == 0

    def test_non_ok_event_ignored(self, tracker):
        tracker.event_handler({"event": "runner_on_failed", "event_data": {}})
        tracker.event_handler({"event": "runner_on_start", "event_data": {}})
        assert len(tracker._file_events) == 0

    def test_missing_event_data_ignored(self, tracker):
        tracker.event_handler({"event": "runner_on_ok"})
        assert len(tracker._file_events) == 0

    def test_non_string_task_ignored(self, tracker):
        tracker.event_handler(
            {
                "event": "runner_on_ok",
                "event_data": {"task": None, "host": "localhost"},
            }
        )
        assert len(tracker._file_events) == 0

    def test_non_file_module_task_ignored(self, tracker):
        tracker.event_handler(
            {
                "event": "runner_on_ok",
                "event_data": {
                    "task": "command Run something",
                    "host": "localhost",
                    "res": {"changed": True},
                },
            }
        )
        assert len(tracker._file_events) == 0

    def test_file_module_event_captured(self, tracker):
        tracker.event_handler(
            {
                "event": "runner_on_ok",
                "event_data": {
                    "task": "copy my task name",
                    "host": "localhost",
                    "res": {"dest": "/tmp/x", "changed": True},
                },
            }
        )
        assert len(tracker._file_events) == 1
        ev = tracker._file_events[0]
        assert ev["task"] == "copy my task name"
        assert ev["host"] == "localhost"
        assert ev["dest"] == "/tmp/x"
        assert ev["changed"] is True

    def test_file_module_fqcn_captured(self, tracker):
        tracker.event_handler(
            {
                "event": "runner_on_ok",
                "event_data": {
                    "task": "ansible.builtin.template Deploy config",
                    "host": "web01",
                    "res": {"dest": "/etc/conf", "src": "/tmp/tmpl"},
                },
            }
        )
        assert len(tracker._file_events) == 1
        ev = tracker._file_events[0]
        assert ev["task"] == "ansible.builtin.template Deploy config"
        assert ev["dest"] == "/etc/conf"
        assert ev["src"] == "/tmp/tmpl"

    def test_multiple_file_events_accumulate(self, tracker):
        for i in range(3):
            tracker.event_handler(
                {
                    "event": "runner_on_ok",
                    "event_data": {
                        "task": f"copy Task {i}",
                        "host": "localhost",
                        "res": {"dest": f"/tmp/x{i}", "changed": True},
                    },
                }
            )
        assert len(tracker._file_events) == 3

    def test_non_dict_event_data_ignored(self, tracker):
        tracker.event_handler(
            {
                "event": "runner_on_ok",
                "event_data": "not a dict",
            }
        )
        assert len(tracker._file_events) == 0

    def test_non_dict_res_ignored(self, tracker):
        tracker.event_handler(
            {
                "event": "runner_on_ok",
                "event_data": {
                    "task": "copy Task",
                    "host": "localhost",
                    "res": "not a dict",
                },
            }
        )
        assert len(tracker._file_events) == 0

    def test_irrelevant_result_fields_ignored(self, tracker):
        tracker.event_handler(
            {
                "event": "runner_on_ok",
                "event_data": {
                    "task": "copy Task",
                    "host": "localhost",
                    "res": {"dest": "/tmp/x", "extra_key": "val", "gid": 1000},
                },
            }
        )
        ev = tracker._file_events[0]
        assert "dest" in ev
        assert "extra_key" not in ev
        assert "gid" not in ev


class TestFileChangeTrackerBuildAgentContext:
    @pytest.fixture
    def tracker(self, tmp_path):
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(git_dir), capture_output=True, check=False)
        (git_dir / "f.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=str(git_dir), capture_output=True, check=False)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
            cwd=str(git_dir),
            capture_output=True,
            check=False,
        )
        return FileChangeTracker(repo_root=git_dir)

    def test_context_has_all_keys(self, tracker):
        ctx = tracker.build_agent_context()
        assert "playbook_summary" in ctx
        assert "git_state" in ctx
        assert "file_details" in ctx
        assert "git_diff" in ctx

    def test_playbook_summary_counts_events(self, tracker):
        tracker.event_handler(
            {
                "event": "runner_on_ok",
                "event_data": {
                    "task": "copy Task",
                    "host": "localhost",
                    "res": {"dest": "/tmp/x", "changed": True},
                },
            }
        )
        ctx = tracker.build_agent_context()
        assert ctx["playbook_summary"]["file_events_count"] == 1
        assert len(ctx["playbook_summary"]["events"]) == 1

    def test_git_state_has_before_and_after(self, tracker):
        ctx = tracker.build_agent_context()
        assert ctx["git_state"]["sha_before"] is not None
        assert ctx["git_state"]["sha_after"] is not None
        assert len(ctx["git_state"]["sha_before"]) == 40

    def test_git_state_same_sha_when_no_changes(self, tracker):
        ctx = tracker.build_agent_context()
        assert ctx["git_state"]["sha_before"] == ctx["git_state"]["sha_after"]

    def test_file_details_equals_events(self, tracker):
        tracker.event_handler(
            {
                "event": "runner_on_ok",
                "event_data": {
                    "task": "copy Task",
                    "host": "localhost",
                    "res": {"dest": "/tmp/x", "changed": True},
                },
            }
        )
        ctx = tracker.build_agent_context()
        assert ctx["file_details"] == tracker._file_events

    def test_git_diff_is_string(self, tracker):
        ctx = tracker.build_agent_context()
        assert isinstance(ctx["git_diff"], str)
