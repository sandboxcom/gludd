"""Tests for obj02: Worktree monitor with watchdog."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from general_ludd.worktree import (
    AgentsMdResult,
    TrackedWorktree,
    WorktreeEventDispatcher,
    WorktreeMonitor,
    WorktreeMonitorConfig,
    WorktreeScanner,
    is_git_worktree,
    parse_agents_md_markdown,
)


class TestIsGitWorktree:
    def test_worktree_with_git_file(self, tmp_path: Path):
        (tmp_path / ".git").write_text("gitdir: /some/repo/.git/worktrees/wt")
        assert is_git_worktree(str(tmp_path)) is True

    def test_directory_without_git(self, tmp_path: Path):
        assert is_git_worktree(str(tmp_path)) is False

    def test_git_directory_not_worktree(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        assert is_git_worktree(str(tmp_path)) is False

    def test_no_git_at_all(self, tmp_path: Path):
        assert is_git_worktree(str(tmp_path)) is False


class TestParseAgentsMd:
    def test_parse_yaml_frontmatter(self):
        content = """---
title: Fix login timeout
description: Users are being logged out after 5 minutes
work_type: bug_fix
priority: high
queue: core
project: auth-service
---
Some other content here."""
        result = parse_agents_md_markdown(content)
        assert result.title == "Fix login timeout"
        assert result.description == "Users are being logged out after 5 minutes"
        assert result.work_type == "bug_fix"
        assert result.priority == "high"
        assert result.queue == "core"
        assert result.project == "auth-service"

    def test_parse_markdown_headings(self):
        content = """# Task: Fix login timeout bug
## Description
Users are being logged out after 5 minutes of inactivity.

## Work Type: bug_fix
## Priority: high
## Queue: core
## Project: auth-service
"""
        result = parse_agents_md_markdown(content)
        assert result.title == "Task: Fix login timeout bug"
        assert "5 minutes" in result.description
        assert result.work_type == "bug_fix"
        assert result.priority == "high"
        assert result.queue == "core"
        assert result.project == "auth-service"

    def test_parse_minimal_markdown(self):
        content = """# Simple task
Some description here."""
        result = parse_agents_md_markdown(content)
        assert result.title == "Simple task"
        assert "Some description" in result.description
        assert result.work_type == "code"
        assert result.priority == "medium"

    def test_parse_empty_content(self):
        result = parse_agents_md_markdown("")
        assert result.title == ""
        assert result.description == ""

    def test_parse_invalid_yaml_falls_back_to_markdown(self):
        content = """---
invalid: yaml: here
---
# Valid markdown title"""
        result = parse_agents_md_markdown(content)
        assert result.title == "Valid markdown title"

    def test_parse_work_type_without_colon(self):
        content = """## Work Type bug_fix"""
        result = parse_agents_md_markdown(content)
        assert result.work_type == "bug_fix"

    def test_parse_preserves_raw_content(self):
        content = "# Test title"
        result = parse_agents_md_markdown(content)
        assert result.raw_content == content


class TestWorktreeMonitorConfig:
    def test_defaults(self):
        cfg = WorktreeMonitorConfig()
        assert cfg.enabled is True
        assert cfg.watch_paths == []
        assert cfg.abandoned_after_hours == 24
        assert cfg.scan_interval_seconds == 300
        assert cfg.max_todos_per_scan == 10
        assert cfg.default_queue == "intake"
        assert cfg.auto_create_todos is True

    def test_custom_values(self):
        cfg = WorktreeMonitorConfig(
            enabled=False,
            watch_paths=["/projects"],
            abandoned_after_hours=48,
            max_todos_per_scan=5,
            default_queue="core",
        )
        assert cfg.enabled is False
        assert cfg.watch_paths == ["/projects"]
        assert cfg.abandoned_after_hours == 48


class TestWorktreeScanner:
    def test_scanner_scans_watch_paths(self, tmp_path: Path):
        wt_dir = tmp_path / "my-project"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/my-project")
        (wt_dir / "AGENTS.md").write_text("# Test task\n## Description\nDo something")

        cfg = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        scanner = WorktreeScanner(cfg)
        discovered = scanner.scan()
        assert len(discovered) == 1
        assert discovered[0].path == str(wt_dir)
        assert discovered[0].agents_md is not None
        assert discovered[0].agents_md.title == "Test task"

    def test_scanner_excludes_patterns(self, tmp_path: Path):
        wt_dir = tmp_path / "node_modules"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/nodemod")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            exclude_patterns=["*node_modules*"],
        )
        scanner = WorktreeScanner(cfg)
        discovered = scanner.scan()
        assert len(discovered) == 0

    def test_scanner_ignores_non_worktrees(self, tmp_path: Path):
        regular_dir = tmp_path / "regular"
        regular_dir.mkdir()
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/wt")

        cfg = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        scanner = WorktreeScanner(cfg)
        discovered = scanner.scan()
        assert len(discovered) == 1
        assert discovered[0].path == str(wt_dir)

    def test_scanner_updates_existing_tracked(self, tmp_path: Path):
        wt_dir = tmp_path / "existing"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/existing")
        (wt_dir / "AGENTS.md").write_text("# Old title")

        cfg = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        existing = TrackedWorktree(path=str(wt_dir), todo_id="TODO-001")
        tracked = {str(wt_dir): existing}
        scanner = WorktreeScanner(cfg, tracked=tracked)

        discovered = scanner.scan()
        assert len(discovered) == 1
        assert discovered[0].todo_id == "TODO-001"
        assert discovered[0].agents_md is not None
        assert discovered[0].agents_md.title == "Old title"

    def test_scanner_removes_stale_worktrees(self, tmp_path: Path):
        tracked = {
            "/stale/path": TrackedWorktree(path="/stale/path", todo_id="TODO-STALE"),
        }
        cfg = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        scanner = WorktreeScanner(cfg, tracked=tracked)

        removed = scanner.remove_stale(set())
        assert len(removed) == 1
        assert removed[0] == "TODO-STALE"
        assert len(tracked) == 0

    def test_scanner_no_git_activity_returns_none(self, tmp_path: Path):
        wt_dir = tmp_path / "no-commits"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/no-commits")

        activity = WorktreeScanner._get_last_activity(str(wt_dir))
        assert activity is None

    def test_scanner_expands_user_paths(self):
        cfg = WorktreeMonitorConfig(watch_paths=["~/projects"])
        scanner = WorktreeScanner(cfg)
        with patch("os.path.isdir", return_value=False):
            discovered = scanner.scan()
            assert len(discovered) == 0


class TestWorktreeEventDispatcher:
    def test_agents_md_event_triggers_scan(self, tmp_path: Path):

        wt_dir = tmp_path / "event-wt"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/event-wt")
        (wt_dir / "AGENTS.md").write_text("# React to event")

        cfg = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        scanner = WorktreeScanner(cfg)
        dispatcher = WorktreeEventDispatcher(scanner, cfg)

        result = dispatcher.on_agents_md_event(str(wt_dir / "AGENTS.md"))
        assert result == str(wt_dir)
        assert str(wt_dir) in scanner._tracked

    def test_non_agents_md_event_ignored(self, tmp_path: Path):

        cfg = WorktreeMonitorConfig()
        scanner = WorktreeScanner(cfg)
        dispatcher = WorktreeEventDispatcher(scanner, cfg)

        result = dispatcher.on_agents_md_event("/some/path/README.md")
        assert result is None

    def test_agents_md_outside_worktree_ignored(self, tmp_path: Path):

        regular_dir = tmp_path / "regular"
        regular_dir.mkdir()
        (regular_dir / "AGENTS.md").write_text("# Not a worktree")

        cfg = WorktreeMonitorConfig()
        scanner = WorktreeScanner(cfg)
        dispatcher = WorktreeEventDispatcher(scanner, cfg)

        result = dispatcher.on_agents_md_event(str(regular_dir / "AGENTS.md"))
        assert result is None

    def test_excluded_worktree_ignored(self, tmp_path: Path):

        wt_dir = tmp_path / "excluded"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/excluded")
        (wt_dir / "AGENTS.md").write_text("# Skip me")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            exclude_patterns=[f"{wt_dir}*"],
        )
        scanner = WorktreeScanner(cfg)
        dispatcher = WorktreeEventDispatcher(scanner, cfg)

        result = dispatcher.on_agents_md_event(str(wt_dir / "AGENTS.md"))
        assert result is None


class TestWorktreeMonitor:
    def test_monitor_disabled_returns_empty(self):
        cfg = WorktreeMonitorConfig(enabled=False)
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert todos == []

    def test_monitor_detects_abandoned_worktree(self, tmp_path: Path):
        wt_dir = tmp_path / "abandoned"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/abandoned")
        (wt_dir / "AGENTS.md").write_text("# Fix important bug\n## Work Type: code\n## Priority: high")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
        )
        monitor = WorktreeMonitor(cfg)
        # Worktree has no activity → last_activity is None → is_abandoned returns True
        todos = monitor.evaluate()
        assert len(todos) == 1
        assert todos[0]["title"] == "Fix important bug"
        assert todos[0]["work_type"] == "code"

    def test_monitor_ignores_active_worktree(self, tmp_path: Path):

        wt_dir = tmp_path / "active"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/active")
        (wt_dir / "AGENTS.md").write_text("# Active task")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=24,
        )
        scanner = WorktreeScanner(cfg)
        monitor = WorktreeMonitor(cfg, scanner=scanner)

        # Pre-populate with recent activity
        scanner._tracked[str(wt_dir)] = TrackedWorktree(
            path=str(wt_dir),
            last_activity=datetime.now(UTC),
        )

        todos = monitor.evaluate()
        assert len(todos) == 0

    def test_monitor_respects_max_todos_per_scan(self, tmp_path: Path):
        for i in range(5):
            wt_dir = tmp_path / f"wt-{i}"
            wt_dir.mkdir()
            (wt_dir / ".git").write_text(f"gitdir: /main/.git/worktrees/wt-{i}")
            (wt_dir / "AGENTS.md").write_text(f"# Task {i}")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
            max_todos_per_scan=2,
        )
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert len(todos) == 2

    def test_monitor_skips_existing_todo(self, tmp_path: Path):
        wt_dir = tmp_path / "has-todo"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/has-todo")
        (wt_dir / "AGENTS.md").write_text("# Already tracked")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
        )
        scanner = WorktreeScanner(cfg)
        scanner._tracked[str(wt_dir)] = TrackedWorktree(
            path=str(wt_dir),
            todo_id="TODO-EXISTS",
        )
        monitor = WorktreeMonitor(cfg, scanner=scanner)
        todos = monitor.evaluate()
        assert len(todos) == 0

    def test_monitor_skips_no_agents_md(self, tmp_path: Path):
        wt_dir = tmp_path / "no-agents"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/no-agents")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
        )
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert len(todos) == 0

    def test_monitor_skips_empty_agents_md_title(self, tmp_path: Path):
        wt_dir = tmp_path / "empty-title"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/empty-title")
        (wt_dir / "AGENTS.md").write_text("")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
        )
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert len(todos) == 0

    def test_todo_includes_defaults_from_config(self, tmp_path: Path):
        wt_dir = tmp_path / "defaults"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /main/.git/worktrees/defaults")
        (wt_dir / "AGENTS.md").write_text("# Just a title")

        cfg = WorktreeMonitorConfig(
            watch_paths=[str(tmp_path)],
            abandoned_after_hours=0,
            default_queue="core",
        )
        monitor = WorktreeMonitor(cfg)
        todos = monitor.evaluate()
        assert len(todos) == 1
        assert todos[0]["queue"] == "core"
        assert todos[0]["status"] == "queued"
        assert "worktree-monitor" in todos[0]["tags"]

    def test_monitor_provides_tracked_worktrees(self, tmp_path: Path):
        cfg = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        monitor = WorktreeMonitor(cfg)
        assert isinstance(monitor.tracked_worktrees, dict)

    def test_monitor_provides_event_dispatcher(self, tmp_path: Path):
        cfg = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        monitor = WorktreeMonitor(cfg)

        assert isinstance(monitor.event_dispatcher, WorktreeEventDispatcher)


class TestAgentsMdResult:
    def test_defaults(self):
        result = AgentsMdResult()
        assert result.title == ""
        assert result.work_type == "code"
        assert result.priority == "medium"
        assert result.queue is None

    def test_with_values(self):
        result = AgentsMdResult(
            title="Test",
            description="Desc",
            work_type="refactor",
            priority="high",
            queue="core",
            project="proj-1",
        )
        assert result.title == "Test"
        assert result.work_type == "refactor"
        assert result.project == "proj-1"


class TestTrackedWorktree:
    def test_defaults(self):
        wt = TrackedWorktree(path="/some/path")
        assert wt.path == "/some/path"
        assert wt.agents_md_path is None
        assert wt.last_activity is None
        assert wt.todo_id is None

    def test_with_values(self):

        now = datetime.now(UTC)
        wt = TrackedWorktree(
            path="/some/path",
            agents_md_path="/some/path/AGENTS.md",
            last_activity=now,
            todo_id="TODO-001",
        )
        assert wt.todo_id == "TODO-001"
        assert wt.last_activity == now
