from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

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
    def test_file_dot_git_returns_true(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: /some/path\n")
        assert is_git_worktree(str(tmp_path)) is True

    def test_directory_dot_git_returns_false(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert is_git_worktree(str(tmp_path)) is False

    def test_missing_dot_git_returns_false(self, tmp_path):
        assert is_git_worktree(str(tmp_path)) is False

    def test_nonexistent_path_returns_false(self, tmp_path):
        assert is_git_worktree(str(tmp_path / "nope")) is False


class TestParseAgentsMdMarkdown:
    def test_yaml_frontmatter(self):
        content = (
            "---\ntitle: My Task\ndescription: Do the thing"
            "\nwork_type: infra\npriority: high"
            "\nqueue: critical\nproject: alpha\n---\nSome body"
        )
        result = parse_agents_md_markdown(content)
        assert result.title == "My Task"
        assert result.description == "Do the thing"
        assert result.work_type == "infra"
        assert result.priority == "high"
        assert result.queue == "critical"
        assert result.project == "alpha"
        assert result.raw_content == content

    def test_yaml_frontmatter_partial(self):
        content = "---\ntitle: Partial\n---\n"
        result = parse_agents_md_markdown(content)
        assert result.title == "Partial"
        assert result.description == ""
        assert result.work_type == "code"

    def test_yaml_frontmatter_invalid_yaml(self):
        content = "---\n: [invalid yaml {{{\n---\n"
        result = parse_agents_md_markdown(content)
        assert result.raw_content == content
        assert result.title == ""

    def test_markdown_headings_with_colons(self):
        content = (
            "# My Title\n\n## Description: A fine task"
            "\n\n## Work Type: infra\n\n## Priority: critical"
            "\n\n## Queue: urgent\n\n## Project: beta\n"
        )
        result = parse_agents_md_markdown(content)
        assert result.title == "My Title"
        assert result.description == "A fine task"
        assert result.work_type == "infra"
        assert result.priority == "critical"
        assert result.queue == "urgent"
        assert result.project == "beta"

    def test_markdown_headings_without_colons(self):
        content = "# My Title\n\n## Description\nA fine task here\n\n## Work Type infra build\n\n## Priority high\n"
        result = parse_agents_md_markdown(content)
        assert result.title == "My Title"
        assert result.work_type == "infra build"
        assert result.priority == "high"

    def test_empty_content(self):
        result = parse_agents_md_markdown("")
        assert result.title == ""
        assert result.description == ""
        assert result.work_type == "code"
        assert result.priority == "medium"
        assert result.raw_content == ""

    def test_yaml_frontmatter_non_dict(self):
        content = "---\n- just\n- a\n- list\n---\n"
        result = parse_agents_md_markdown(content)
        assert result.title == ""

    def test_markdown_title_only(self):
        content = "# Only Title\n"
        result = parse_agents_md_markdown(content)
        assert result.title == "Only Title"
        assert result.description == ""

    def test_description_after_title_before_other_headings(self):
        content = "# Title\nThis is the description.\nIt has two lines.\n## Priority: low\n"
        result = parse_agents_md_markdown(content)
        assert result.title == "Title"
        assert "This is the description." in result.description
        assert "It has two lines." in result.description
        assert result.priority == "low"

    def test_description_inline_with_heading(self):
        content = "# Title\n## Description: inline desc\n## Queue: myq\n"
        result = parse_agents_md_markdown(content)
        assert result.description == "inline desc"
        assert result.queue == "myq"

    def test_yaml_queue_none(self):
        content = "---\ntitle: T\n---\n"
        result = parse_agents_md_markdown(content)
        assert result.queue == "None"


class TestWorktreeScannerIsExcluded:
    def test_matching_full_path(self):
        config = WorktreeMonitorConfig(exclude_patterns=["*/excluded/*"])
        scanner = WorktreeScanner(config)
        assert scanner._is_excluded("/some/excluded/dir") is True

    def test_matching_basename(self):
        config = WorktreeMonitorConfig(exclude_patterns=["temp-*"])
        scanner = WorktreeScanner(config)
        assert scanner._is_excluded("/some/path/temp-123") is True

    def test_no_match(self):
        config = WorktreeMonitorConfig(exclude_patterns=["hidden-*"])
        scanner = WorktreeScanner(config)
        assert scanner._is_excluded("/some/path/visible-dir") is False

    def test_empty_patterns(self):
        config = WorktreeMonitorConfig(exclude_patterns=[])
        scanner = WorktreeScanner(config)
        assert scanner._is_excluded("/any/path") is False

    def test_multiple_patterns_one_matches(self):
        config = WorktreeMonitorConfig(exclude_patterns=["*.bak", "tmp-*", "*.swp"])
        scanner = WorktreeScanner(config)
        assert scanner._is_excluded("/path/tmp-work") is True


class TestWorktreeScannerScan:
    def test_scan_finds_worktree(self, tmp_path):
        wt_dir = tmp_path / "my-worktree"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /some/path\n")
        config = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        scanner = WorktreeScanner(config)
        with patch.object(scanner, "_process_worktree", return_value=TrackedWorktree(path=str(wt_dir))):
            results = scanner.scan()
        assert len(results) == 1

    def test_scan_skips_non_dir_entries(self, tmp_path):
        wt_dir = tmp_path / "my-worktree"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /some/path\n")
        (tmp_path / "a_file.txt").write_text("not a dir")
        config = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        scanner = WorktreeScanner(config)
        with patch.object(scanner, "_process_worktree", return_value=TrackedWorktree(path=str(wt_dir))):
            results = scanner.scan()
        assert len(results) == 1

    def test_scan_skips_excluded(self, tmp_path):
        wt_dir = tmp_path / "temp-123"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /some/path\n")
        config = WorktreeMonitorConfig(watch_paths=[str(tmp_path)], exclude_patterns=["temp-*"])
        scanner = WorktreeScanner(config)
        results = scanner.scan()
        assert len(results) == 0

    def test_scan_skips_non_worktree(self, tmp_path):
        regular = tmp_path / "regular-dir"
        regular.mkdir()
        (regular / ".git").mkdir()
        config = WorktreeMonitorConfig(watch_paths=[str(tmp_path)])
        scanner = WorktreeScanner(config)
        results = scanner.scan()
        assert len(results) == 0

    def test_scan_skips_nonexistent_watch_path(self):
        config = WorktreeMonitorConfig(watch_paths=["/nonexistent/path/xyz"])
        scanner = WorktreeScanner(config)
        results = scanner.scan()
        assert len(results) == 0

    def test_scan_multiple_watch_paths(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        wt_a = dir_a / "wt1"
        wt_a.mkdir()
        (wt_a / ".git").write_text("gitdir: x\n")
        wt_b = dir_b / "wt2"
        wt_b.mkdir()
        (wt_b / ".git").write_text("gitdir: y\n")
        config = WorktreeMonitorConfig(watch_paths=[str(dir_a), str(dir_b)])
        scanner = WorktreeScanner(config)
        with patch.object(scanner, "_process_worktree", return_value=TrackedWorktree(path="/fake")):
            results = scanner.scan()
        assert len(results) == 2


class TestWorktreeScannerProcessWorktree:
    def test_with_agents_md(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: /x\n")
        agents = tmp_path / "AGENTS.md"
        agents.write_text("# Test Title\n## Description: testing\n")
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        with patch.object(WorktreeScanner, "_get_last_activity", return_value=datetime.now(UTC)):
            wt = scanner._process_worktree(str(tmp_path))
        assert wt.agents_md is not None
        assert wt.agents_md.title == "Test Title"
        assert wt.agents_md_path == str(agents)
        assert wt.last_scanned is not None
        assert wt.last_activity is not None

    def test_without_agents_md(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: /x\n")
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        with patch.object(WorktreeScanner, "_get_last_activity", return_value=datetime.now(UTC)):
            wt = scanner._process_worktree(str(tmp_path))
        assert wt.agents_md is None
        assert wt.agents_md_path is None

    def test_unreadable_agents_md(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: /x\n")
        agents = tmp_path / "AGENTS.md"
        agents.write_text("# Title\n")
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        with patch.object(WorktreeScanner, "_get_last_activity", return_value=None), \
             patch("builtins.open", side_effect=PermissionError("nope")):
            wt = scanner._process_worktree(str(tmp_path))
        assert wt.agents_md is None

    def test_existing_tracked_preserves_activity(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: /x\n")
        old_time = datetime(2020, 1, 1, tzinfo=UTC)
        existing = TrackedWorktree(path=str(tmp_path), last_activity=old_time, todo_id="WT-1")
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config, tracked={str(tmp_path): existing})
        with patch.object(WorktreeScanner, "_get_last_activity", return_value=datetime.now(UTC)):
            wt = scanner._process_worktree(str(tmp_path))
        assert wt.last_activity == old_time
        assert wt.todo_id == "WT-1"


class TestWorktreeScannerGetLastActivity:
    def test_successful_git_log(self, tmp_path):
        ts = int(datetime.now(UTC).timestamp())
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = str(ts) + "\n"
        with patch("subprocess.run", return_value=mock_result):
            result = WorktreeScanner._get_last_activity(str(tmp_path))
        assert result is not None

    def test_failed_git_log(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = WorktreeScanner._get_last_activity(str(tmp_path))
        assert result is not None

    def test_git_exception_falls_back_to_head(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        now = datetime.now(UTC)
        os.utime(str(tmp_path / ".git" / "HEAD"), (now.timestamp(), now.timestamp()))
        with patch("subprocess.run", side_effect=Exception("no git")):
            result = WorktreeScanner._get_last_activity(str(tmp_path))
        assert result is not None

    def test_no_activity_at_all(self, tmp_path):
        with patch("subprocess.run", side_effect=Exception("no git")):
            result = WorktreeScanner._get_last_activity(str(tmp_path))
        assert result is None

    def test_empty_stdout(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "\n"
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        with patch("subprocess.run", return_value=mock_result):
            result = WorktreeScanner._get_last_activity(str(tmp_path))
        assert result is not None

    def test_head_mtime_exception(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.getmtime", side_effect=PermissionError("no")):
            result = WorktreeScanner._get_last_activity(str(tmp_path))
        assert result is None


class TestWorktreeScannerRemoveStale:
    def test_removes_stale_with_todo_id(self):
        config = WorktreeMonitorConfig()
        tracked = {
            "/a": TrackedWorktree(path="/a", todo_id="WT-1"),
            "/b": TrackedWorktree(path="/b", todo_id="WT-2"),
            "/c": TrackedWorktree(path="/c"),
        }
        scanner = WorktreeScanner(config, tracked=tracked)
        removed = scanner.remove_stale({"/a"})
        assert "/a" in scanner._tracked
        assert len(scanner._tracked) == 1
        assert "WT-2" in removed
        assert "WT-1" not in removed

    def test_nothing_removed_when_all_active(self):
        config = WorktreeMonitorConfig()
        tracked = {"/a": TrackedWorktree(path="/a")}
        scanner = WorktreeScanner(config, tracked=tracked)
        removed = scanner.remove_stale({"/a"})
        assert removed == []
        assert len(scanner._tracked) == 1

    def test_removes_all_when_none_active(self):
        config = WorktreeMonitorConfig()
        tracked = {"/a": TrackedWorktree(path="/a", todo_id="WT-1")}
        scanner = WorktreeScanner(config, tracked=tracked)
        removed = scanner.remove_stale(set())
        assert len(scanner._tracked) == 0
        assert removed == ["WT-1"]


class TestWorktreeEventDispatcherOnAgentsMdEvent:
    def test_valid_event(self, tmp_path):
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /x\n")
        (wt_dir / "AGENTS.md").write_text("# Title\n")
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config)
        event = MagicMock()
        event.src_path = str(wt_dir / "AGENTS.md")
        event.event_type = "modified"
        with patch.object(scanner, "_process_worktree", return_value=TrackedWorktree(path=str(wt_dir))):
            result = dispatcher.on_agents_md_event(event)
        assert result == str(wt_dir)

    def test_non_agents_md_returns_none(self, tmp_path):
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config)
        event = MagicMock()
        event.src_path = str(tmp_path / "README.md")
        result = dispatcher.on_agents_md_event(event)
        assert result is None

    def test_not_worktree_returns_none(self, tmp_path):
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()
        (wt_dir / ".git").mkdir()
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config)
        event = MagicMock()
        event.src_path = str(wt_dir / "AGENTS.md")
        result = dispatcher.on_agents_md_event(event)
        assert result is None

    def test_excluded_path_returns_none(self, tmp_path):
        wt_dir = tmp_path / "temp-x"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /x\n")
        config = WorktreeMonitorConfig(exclude_patterns=["temp-*"])
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config)
        event = MagicMock()
        event.src_path = str(wt_dir / "AGENTS.md")
        result = dispatcher.on_agents_md_event(event)
        assert result is None

    def test_string_event_path(self, tmp_path):
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /x\n")
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config)
        with patch.object(scanner, "_process_worktree", return_value=TrackedWorktree(path=str(wt_dir))):
            result = dispatcher.on_agents_md_event(str(wt_dir / "AGENTS.md"))
        assert result == str(wt_dir)

    def test_calls_monitor_evaluate(self, tmp_path):
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /x\n")
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        monitor = MagicMock()
        dispatcher = WorktreeEventDispatcher(scanner, config, monitor=monitor)
        event = MagicMock()
        event.src_path = str(wt_dir / "AGENTS.md")
        with patch.object(scanner, "_process_worktree", return_value=TrackedWorktree(path=str(wt_dir))):
            dispatcher.on_agents_md_event(event)
        monitor.evaluate.assert_called_once_with(watch_paths=[str(wt_dir)])

    def test_no_monitor_does_not_error(self, tmp_path):
        wt_dir = tmp_path / "wt"
        wt_dir.mkdir()
        (wt_dir / ".git").write_text("gitdir: /x\n")
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config, monitor=None)
        event = MagicMock()
        event.src_path = str(wt_dir / "AGENTS.md")
        with patch.object(scanner, "_process_worktree", return_value=TrackedWorktree(path=str(wt_dir))):
            result = dispatcher.on_agents_md_event(event)
        assert result == str(wt_dir)


class TestWorktreeEventDispatcherStartStopWatching:
    def test_start_watching_without_watchdog(self):
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config, watch_paths=["/tmp"])
        with patch.dict("sys.modules", {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}):
            result = dispatcher.start_watching()
        assert result is None

    def test_start_watching_with_watchdog(self, tmp_path):
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        watch_dir = tmp_path / "watchme"
        watch_dir.mkdir()
        dispatcher = WorktreeEventDispatcher(scanner, config, watch_paths=[str(watch_dir)])
        mock_observer = MagicMock()
        mock_observer_cls = MagicMock(return_value=mock_observer)
        with patch.dict("sys.modules", {
            "watchdog": MagicMock(),
            "watchdog.observers": MagicMock(Observer=mock_observer_cls),
            "watchdog.events": MagicMock(),
        }):
            result = dispatcher.start_watching()
        assert result is mock_observer
        mock_observer.start.assert_called_once()

    def test_start_watching_skips_nonexistent_paths(self):
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config, watch_paths=["/nonexistent/path/xyz"])
        mock_observer = MagicMock()
        mock_observer_cls = MagicMock(return_value=mock_observer)
        with patch.dict("sys.modules", {
            "watchdog": MagicMock(),
            "watchdog.observers": MagicMock(Observer=mock_observer_cls),
            "watchdog.events": MagicMock(),
        }):
            result = dispatcher.start_watching()
        assert result is mock_observer
        mock_observer.schedule.assert_not_called()

    def test_stop_watching(self):
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config)
        mock_obs = MagicMock()
        dispatcher._observer = mock_obs
        dispatcher.stop_watching()
        mock_obs.stop.assert_called_once()
        mock_obs.join.assert_called_once_with(timeout=5)

    def test_stop_watching_no_observer(self):
        config = WorktreeMonitorConfig()
        scanner = WorktreeScanner(config)
        dispatcher = WorktreeEventDispatcher(scanner, config)
        dispatcher.stop_watching()


class TestWorktreeEventDispatcherIsWorktree:
    def test_delegates_to_is_git_worktree(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: /x\n")
        assert WorktreeEventDispatcher._is_worktree(str(tmp_path)) is True

    def test_returns_false_for_non_worktree(self, tmp_path):
        assert WorktreeEventDispatcher._is_worktree(str(tmp_path)) is False


class TestWorktreeMonitorEvaluate:
    def test_disabled_returns_empty(self):
        config = WorktreeMonitorConfig(enabled=False)
        scanner = MagicMock()
        monitor = WorktreeMonitor(config, scanner=scanner)
        result = monitor.evaluate()
        assert result == []

    def test_abandoned_worktree_creates_todo(self, tmp_path):
        config = WorktreeMonitorConfig(abandoned_after_hours=1, max_todos_per_scan=10)
        old_time = datetime.now(UTC) - timedelta(hours=48)
        agents_md = AgentsMdResult(title="Fix bug", description="broken", work_type="code", priority="high")
        wt = TrackedWorktree(path=str(tmp_path), last_activity=old_time, agents_md=agents_md)
        scanner = MagicMock()
        scanner.scan.return_value = [wt]
        scanner._tracked = {}
        monitor = WorktreeMonitor(config, scanner=scanner)
        todos = monitor.evaluate()
        assert len(todos) == 1
        assert todos[0]["title"] == "Fix bug"
        assert todos[0]["status"] == "queued"
        assert "todo_id" in todos[0]

    def test_not_abandoned_skipped(self, tmp_path):
        config = WorktreeMonitorConfig()
        recent_time = datetime.now(UTC)
        agents_md = AgentsMdResult(title="Fix bug")
        wt = TrackedWorktree(path=str(tmp_path), last_activity=recent_time, agents_md=agents_md)
        scanner = MagicMock()
        scanner.scan.return_value = [wt]
        scanner._tracked = {}
        monitor = WorktreeMonitor(config, scanner=scanner)
        todos = monitor.evaluate()
        assert len(todos) == 0

    def test_no_agents_md_skipped(self, tmp_path):
        config = WorktreeMonitorConfig(abandoned_after_hours=1)
        old_time = datetime.now(UTC) - timedelta(hours=48)
        wt = TrackedWorktree(path=str(tmp_path), last_activity=old_time, agents_md=None)
        scanner = MagicMock()
        scanner.scan.return_value = [wt]
        scanner._tracked = {}
        monitor = WorktreeMonitor(config, scanner=scanner)
        todos = monitor.evaluate()
        assert len(todos) == 0

    def test_agents_md_no_title_skipped(self, tmp_path):
        config = WorktreeMonitorConfig(abandoned_after_hours=1)
        old_time = datetime.now(UTC) - timedelta(hours=48)
        agents_md = AgentsMdResult(title="")
        wt = TrackedWorktree(path=str(tmp_path), last_activity=old_time, agents_md=agents_md)
        scanner = MagicMock()
        scanner.scan.return_value = [wt]
        scanner._tracked = {}
        monitor = WorktreeMonitor(config, scanner=scanner)
        todos = monitor.evaluate()
        assert len(todos) == 0

    def test_existing_todo_id_skipped(self, tmp_path):
        config = WorktreeMonitorConfig(abandoned_after_hours=1)
        old_time = datetime.now(UTC) - timedelta(hours=48)
        agents_md = AgentsMdResult(title="Fix bug")
        wt = TrackedWorktree(path=str(tmp_path), last_activity=old_time, agents_md=agents_md, todo_id="WT-1")
        scanner = MagicMock()
        scanner.scan.return_value = [wt]
        scanner._tracked = {str(tmp_path): wt}
        monitor = WorktreeMonitor(config, scanner=scanner)
        todos = monitor.evaluate()
        assert len(todos) == 0

    def test_max_todos_per_scan_limit(self):
        config = WorktreeMonitorConfig(abandoned_after_hours=1, max_todos_per_scan=2)
        old_time = datetime.now(UTC) - timedelta(hours=48)
        worktrees = []
        for i in range(5):
            md = AgentsMdResult(title=f"Task {i}")
            wt = TrackedWorktree(path=f"/wt{i}", last_activity=old_time, agents_md=md)
            worktrees.append(wt)
        scanner = MagicMock()
        scanner.scan.return_value = worktrees
        scanner._tracked = {}
        monitor = WorktreeMonitor(config, scanner=scanner)
        todos = monitor.evaluate()
        assert len(todos) == 2

    def test_removes_stale_during_evaluate(self, tmp_path):
        config = WorktreeMonitorConfig(abandoned_after_hours=1)
        old_wt = TrackedWorktree(path="/gone", todo_id="WT-old")
        agents_md = AgentsMdResult(title="Active")
        active_wt = TrackedWorktree(
            path=str(tmp_path),
            last_activity=datetime.now(UTC) - timedelta(hours=48),
            agents_md=agents_md,
        )
        scanner = MagicMock()
        scanner.scan.return_value = [active_wt]
        scanner._tracked = {"/gone": old_wt}
        scanner.remove_stale = MagicMock()
        monitor = WorktreeMonitor(config, scanner=scanner)
        monitor.evaluate()
        scanner.remove_stale.assert_called_once_with({str(tmp_path)})


class TestWorktreeMonitorIsAbandoned:
    def test_none_activity_is_abandoned(self):
        config = WorktreeMonitorConfig(abandoned_after_hours=24)
        scanner = MagicMock()
        monitor = WorktreeMonitor(config, scanner=scanner)
        wt = TrackedWorktree(path="/x", last_activity=None)
        assert monitor._is_abandoned(wt) is True

    def test_old_activity_is_abandoned(self):
        config = WorktreeMonitorConfig(abandoned_after_hours=24)
        scanner = MagicMock()
        monitor = WorktreeMonitor(config, scanner=scanner)
        old = datetime.now(UTC) - timedelta(hours=48)
        wt = TrackedWorktree(path="/x", last_activity=old)
        assert monitor._is_abandoned(wt) is True

    def test_recent_activity_not_abandoned(self):
        config = WorktreeMonitorConfig(abandoned_after_hours=24)
        scanner = MagicMock()
        monitor = WorktreeMonitor(config, scanner=scanner)
        recent = datetime.now(UTC) - timedelta(hours=1)
        wt = TrackedWorktree(path="/x", last_activity=recent)
        assert monitor._is_abandoned(wt) is False


class TestWorktreeMonitorCreateTodoFromWorktree:
    def test_full_agents_md(self):
        config = WorktreeMonitorConfig(default_queue="intake")
        scanner = MagicMock()
        monitor = WorktreeMonitor(config, scanner=scanner)
        md = AgentsMdResult(
            title="My Task",
            description="Do things",
            work_type="infra",
            priority="high",
            queue="critical",
            project="proj-1",
        )
        wt = TrackedWorktree(path="/my/wt", agents_md=md)
        todo = monitor._create_todo_from_worktree(wt)
        assert todo["title"] == "My Task"
        assert todo["description"] == "Do things"
        assert todo["work_type"] == "infra"
        assert todo["priority"] == "high"
        assert todo["queue"] == "critical"
        assert todo["project_id"] == "proj-1"
        assert todo["status"] == "queued"
        assert "worktree-monitor" in todo["tags"]

    def test_minimal_agents_md_uses_defaults(self):
        config = WorktreeMonitorConfig(default_queue="fallback")
        scanner = MagicMock()
        monitor = WorktreeMonitor(config, scanner=scanner)
        md = AgentsMdResult(title="T", description="D")
        wt = TrackedWorktree(path="/wt", agents_md=md)
        todo = monitor._create_todo_from_worktree(wt)
        assert todo["queue"] == "fallback"

    def test_none_agents_md_returns_empty(self):
        config = WorktreeMonitorConfig()
        scanner = MagicMock()
        monitor = WorktreeMonitor(config, scanner=scanner)
        wt = TrackedWorktree(path="/wt", agents_md=None)
        todo = monitor._create_todo_from_worktree(wt)
        assert todo == {}

    def test_empty_title_uses_path(self):
        config = WorktreeMonitorConfig()
        scanner = MagicMock()
        monitor = WorktreeMonitor(config, scanner=scanner)
        md = AgentsMdResult(title="", description="")
        wt = TrackedWorktree(path="/my/worktree", agents_md=md)
        todo = monitor._create_todo_from_worktree(wt)
        assert todo["title"] == "Worktree: /my/worktree"
        assert todo["description"] == "Abandoned worktree at /my/worktree"


class TestWorktreeMonitorProperties:
    def test_tracked_worktrees(self):
        config = WorktreeMonitorConfig()
        tracked = {"/a": TrackedWorktree(path="/a")}
        scanner = WorktreeScanner(config, tracked=tracked)
        monitor = WorktreeMonitor(config, scanner=scanner)
        assert monitor.tracked_worktrees is tracked

    def test_event_dispatcher(self):
        config = WorktreeMonitorConfig()
        monitor = WorktreeMonitor(config)
        dispatcher = monitor.event_dispatcher
        assert isinstance(dispatcher, WorktreeEventDispatcher)


class TestDataclassDefaults:
    def test_worktree_monitor_config_defaults(self):
        config = WorktreeMonitorConfig()
        assert config.enabled is True
        assert config.watch_paths == []
        assert config.abandoned_after_hours == 24
        assert config.scan_interval_seconds == 300
        assert config.max_todos_per_scan == 10
        assert config.exclude_patterns == []
        assert config.default_queue == "intake"
        assert config.auto_create_todos is True

    def test_agents_md_result_defaults(self):
        r = AgentsMdResult()
        assert r.title == ""
        assert r.description == ""
        assert r.work_type == "code"
        assert r.priority == "medium"
        assert r.queue is None
        assert r.project is None
        assert r.raw_content == ""

    def test_tracked_worktree_defaults(self):
        wt = TrackedWorktree(path="/x")
        assert wt.path == "/x"
        assert wt.agents_md_path is None
        assert wt.last_activity is None
        assert wt.last_scanned is None
        assert wt.todo_id is None
        assert wt.agents_md is None
