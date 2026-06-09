"""Worktree monitor — detects abandoned git worktrees with AGENTS.md and creates todos."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class WorktreeMonitorConfig:
    """Configuration for the worktree monitor."""

    enabled: bool = True
    watch_paths: list[str] = field(default_factory=list)
    abandoned_after_hours: int = 24
    scan_interval_seconds: int = 300
    max_todos_per_scan: int = 10
    exclude_patterns: list[str] = field(default_factory=list)
    default_queue: str = "intake"
    auto_create_todos: bool = True


@dataclass
class AgentsMdResult:
    """Parsed AGENTS.md content."""

    title: str = ""
    description: str = ""
    work_type: str = "code"
    priority: str = "medium"
    queue: str | None = None
    project: str | None = None
    raw_content: str = ""


@dataclass
class TrackedWorktree:
    """A known worktree being monitored."""

    path: str
    agents_md_path: str | None = None
    last_activity: datetime | None = None
    last_scanned: datetime | None = None
    todo_id: str | None = None
    agents_md: AgentsMdResult | None = None


def is_git_worktree(path: str) -> bool:
    """Check if a directory is a git worktree (has .git file, not directory)."""
    import os

    git_path = os.path.join(path, ".git")
    return os.path.isfile(git_path)


def parse_agents_md_markdown(content: str) -> AgentsMdResult:
    """Parse AGENTS.md markdown format into structured directives.

    Supported formats:
    - Markdown headings: # Title, ## Description, ## Work Type, etc.
    - YAML frontmatter between --- fences.
    """
    import re

    result = AgentsMdResult(raw_content=content)

    fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        import yaml

        try:
            fm = yaml.safe_load(fm_match.group(1))
            if isinstance(fm, dict):
                result.title = str(fm.get("title", result.title))
                result.description = str(fm.get("description", result.description))
                result.work_type = str(fm.get("work_type", result.work_type))
                result.priority = str(fm.get("priority", result.priority))
                result.queue = str(fm.get("queue", result.queue))
                result.project = fm.get("project")
                return result
        except Exception:
            pass

    in_description_section = True
    desc_lines: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            result.title = stripped[2:].strip()
            desc_lines = []
            in_description_section = True
        elif stripped.lower().startswith("## description"):
            in_description_section = True
            desc_lines = []
            val = stripped.split(":", 1)[-1].strip() if ":" in stripped else ""
            if val:
                desc_lines.append(val)
            continue
        elif stripped.startswith("##"):
            in_description_section = False
            tag = stripped[2:].strip()
            if ":" in tag:
                key, val = tag.split(":", 1)
                key = key.strip().lower()
                val = val.strip()
            else:
                parts = tag.split(None, 1)
                first_word = parts[0].lower() if parts else ""
                second_word = parts[1].split(None, 1)[0].lower() if len(parts) > 1 else ""
                val = parts[1].split(None, 1)[1] if len(parts) > 1 and len(parts[1].split(None, 1)) > 1 else ""
                if first_word == "work" and second_word in ("type", "type"):
                    key = "work_type"
                else:
                    key = first_word
                    val = parts[1] if len(parts) > 1 else ""
            if key in ("work type", "work_type"):
                result.work_type = val or tag.split(None, 2)[-1] if len(tag.split(None, 2)) > 2 else "code"
            elif key == "priority":
                result.priority = val
            elif key == "queue":
                result.queue = val
            elif key == "project":
                result.project = val
        elif in_description_section and stripped:
            desc_lines.append(stripped)
        elif in_description_section and not stripped and desc_lines:
            in_description_section = False

    if desc_lines:
        result.description = " ".join(desc_lines)

    return result


class WorktreeScanner:
    """Periodic full scan of configured directories for git worktrees."""

    def __init__(
        self,
        config: WorktreeMonitorConfig,
        tracked: dict[str, TrackedWorktree] | None = None,
    ) -> None:
        self._config = config
        self._tracked: dict[str, TrackedWorktree] = tracked or {}

    def scan(self) -> list[TrackedWorktree]:
        """Scan all configured watch paths for git worktrees."""
        import os

        discovered: list[TrackedWorktree] = []
        for root_path in self._config.watch_paths:
            expanded = os.path.expanduser(root_path)
            if not os.path.isdir(expanded):
                continue
            for entry in os.listdir(expanded):
                full_path = os.path.join(expanded, entry)
                if not os.path.isdir(full_path):
                    continue
                if self._is_excluded(full_path):
                    continue
                if is_git_worktree(full_path):
                    wt = self._process_worktree(full_path)
                    discovered.append(wt)

        return discovered

    def _is_excluded(self, path: str) -> bool:
        import fnmatch
        import os

        for pattern in self._config.exclude_patterns:
            if fnmatch.fnmatch(path, pattern):
                return True
            if fnmatch.fnmatch(os.path.basename(path), pattern):
                return True
        return False

    def _process_worktree(self, path: str) -> TrackedWorktree:
        import os

        wt = self._tracked.get(path, TrackedWorktree(path=path))
        wt.path = path

        agents_md_path = os.path.join(path, "AGENTS.md")
        if os.path.isfile(agents_md_path):
            wt.agents_md_path = agents_md_path
            try:
                with open(agents_md_path) as f:
                    content = f.read()
                wt.agents_md = parse_agents_md_markdown(content)
            except Exception:
                wt.agents_md = None
        else:
            wt.agents_md_path = None
            wt.agents_md = None

        wt.last_scanned = datetime.now(UTC)
        if wt.last_activity is None:
            wt.last_activity = self._get_last_activity(path)

        self._tracked[path] = wt
        return wt

    @staticmethod
    def _get_last_activity(path: str) -> datetime | None:
        """Get the last git commit time for a worktree."""
        import os
        import subprocess

        try:
            result = subprocess.run(
                ["git", "-C", path, "log", "-1", "--format=%ct", "--all"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                ts = int(result.stdout.strip())
                return datetime.fromtimestamp(ts, UTC)
        except Exception:
            pass

        head_path = os.path.join(path, ".git", "HEAD")
        if os.path.exists(head_path):
            try:
                mtime = os.path.getmtime(head_path)
                return datetime.fromtimestamp(mtime, UTC)
            except Exception:
                pass

        return None

    def remove_stale(self, active_paths: set[str]) -> list[str]:
        """Remove tracked worktrees that no longer exist. Returns removed todo IDs."""
        removed: list[str] = []
        for path in list(self._tracked):
            if path not in active_paths:
                wt = self._tracked.pop(path)
                if wt.todo_id:
                    removed.append(wt.todo_id)
        return removed


class WorktreeEventDispatcher:
    """Dispatches watchdog filesystem events to the worktree monitor."""

    def __init__(
        self,
        scanner: WorktreeScanner,
        config: WorktreeMonitorConfig,
        monitor: Any | None = None,
        watch_paths: list[str] | None = None,
    ) -> None:
        self._scanner = scanner
        self._config = config
        self._monitor = monitor
        self._watch_paths = watch_paths or []
        self._observer: Any = None

    def on_agents_md_event(self, event: Any) -> str | None:
        event_path = getattr(event, "src_path", event) if not isinstance(event, str) else event
        getattr(event, "event_type", "modified") if not isinstance(event, str) else "modified"

        if not event_path.endswith("AGENTS.md"):
            return None
        worktree_path = os.path.dirname(event_path)
        if not is_git_worktree(worktree_path):
            return None
        if self._scanner._is_excluded(worktree_path):
            return None
        if self._monitor is not None and hasattr(self._monitor, "evaluate"):
            self._monitor.evaluate(watch_paths=[worktree_path])
        self._scanner._process_worktree(worktree_path)
        return worktree_path

    @staticmethod
    def _is_worktree(path: str) -> bool:
        return is_git_worktree(path)

    def start_watching(self) -> Any | None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return None

        class Handler(FileSystemEventHandler):
            def __init__(self, dispatcher: WorktreeEventDispatcher):
                self._dispatcher = dispatcher

            def on_created(self, event: Any) -> None:
                self._dispatcher.on_agents_md_event(event)

            def on_modified(self, event: Any) -> None:
                self._dispatcher.on_agents_md_event(event)

        self._observer = Observer()
        for path in self._watch_paths:
            if os.path.isdir(path):
                self._observer.schedule(Handler(self), path, recursive=True)
        self._observer.start()
        return self._observer

    def stop_watching(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)


class WorktreeMonitor:
    """Monitors git worktrees for abandonment and creates todos from AGENTS.md directives."""

    def __init__(
        self,
        config: WorktreeMonitorConfig,
        scanner: WorktreeScanner | None = None,
        todo_creator: object | None = None,
    ) -> None:
        self._config = config
        self._scanner = scanner or WorktreeScanner(config)
        self._todo_creator = todo_creator
        self._event_dispatcher = WorktreeEventDispatcher(self._scanner, config)

    def evaluate(self) -> list[dict[str, object]]:
        """Evaluate worktrees and return list of todos to create."""
        if not self._config.enabled:
            return []

        discovered = self._scanner.scan()
        active_paths = {wt.path for wt in discovered}
        self._scanner.remove_stale(active_paths)

        todos: list[dict[str, object]] = []
        todos_created = 0

        for wt in discovered:
            if todos_created >= self._config.max_todos_per_scan:
                break
            if wt.todo_id is not None:
                continue
            if not self._is_abandoned(wt):
                continue
            if wt.agents_md is None or not wt.agents_md.title:
                continue

            todo = self._create_todo_from_worktree(wt)
            if todo:
                todo_id = f"WT-{wt.path.replace('/', '-')}"
                todo["todo_id"] = todo_id
                wt.todo_id = todo_id
                self._scanner._tracked[wt.path] = wt
                todos.append(todo)
                todos_created += 1

        return todos

    def _is_abandoned(self, wt: TrackedWorktree) -> bool:
        if wt.last_activity is None:
            return True
        threshold = datetime.now(UTC) - timedelta(hours=self._config.abandoned_after_hours)
        return wt.last_activity < threshold

    def _create_todo_from_worktree(self, wt: TrackedWorktree) -> dict[str, object]:
        if wt.agents_md is None:
            return {}
        md = wt.agents_md
        return {
            "title": md.title or f"Worktree: {wt.path}",
            "description": md.description or f"Abandoned worktree at {wt.path}",
            "work_type": md.work_type,
            "priority": md.priority,
            "queue": md.queue or self._config.default_queue,
            "project_id": md.project,
            "status": "queued",
            "tags": ["worktree-monitor", "abandoned"],
        }

    @property
    def tracked_worktrees(self) -> dict[str, TrackedWorktree]:
        return self._scanner._tracked

    @property
    def event_dispatcher(self) -> WorktreeEventDispatcher:
        return self._event_dispatcher
