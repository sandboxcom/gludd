"""Worktree monitor — detects abandoned git worktrees with AGENTS.md and creates todos."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# --------------------------------------------------------------------------- #
# git worktree input hardening                                                #
#                                                                             #
# Branch names and worktree paths reach `git worktree` (add/list/remove) from #
# caller input. Without validation a value beginning with '-' is parsed by    #
# git as an OPTION (e.g. --upload-pack=...) rather than a positional, and a    #
# traversal / out-of-base path could plant or reclaim a worktree anywhere on  #
# the filesystem. We fail closed BEFORE building any argv:                     #
#   * validate_branch_name  — reject leading '-', whitespace/control chars and #
#     git ref metacharacters,                                                  #
#   * confine_worktree_path — realpath the path and require it to live under   #
#     an allowed base directory,                                               #
# and every builder emits list-form argv with a `--` end-of-options separator #
# before the path positional so a value can never be reinterpreted as a flag. #
# --------------------------------------------------------------------------- #

# Characters git itself forbids in a ref, plus shell metacharacters and
# whitespace. A legitimate branch name never contains any of these.
_BRANCH_FORBIDDEN = re.compile(r"[\s~^:?*\[\\\x00-\x1f\x7f;&|$`(){}<>'\"!]")


def validate_branch_name(name: str) -> str:
    """Validate a branch name destined for ``git worktree add -b``.

    Rejects (raising ``ValueError``):
      * empty / whitespace-only names,
      * a leading ``-`` (git would parse it as an option, not a ref),
      * whitespace, control chars and shell/ref metacharacters,
      * git's own ref rules: ``..`` sequences, a leading ``/``, and a
        ``.lock`` suffix.

    Returns the (unchanged) name on success so call sites can inline it.
    """
    if not name or not name.strip():
        raise ValueError("refusing empty branch name")
    if name.startswith("-"):
        raise ValueError(
            f"refusing branch name that begins with '-' (would be parsed as a "
            f"git option, not a ref): {name!r}"
        )
    if name.startswith("/"):
        raise ValueError(f"refusing branch name with a leading '/': {name!r}")
    if ".." in name:
        raise ValueError(f"refusing branch name containing '..': {name!r}")
    if name.endswith(".lock"):
        raise ValueError(f"refusing branch name ending in '.lock': {name!r}")
    if _BRANCH_FORBIDDEN.search(name):
        raise ValueError(
            f"refusing branch name with forbidden/metacharacter content: {name!r}"
        )
    return name


def confine_worktree_path(path: str, allowed_base: str) -> str:
    """Confine ``path`` to ``allowed_base`` and return its realpath.

    Rejects (raising ``ValueError``):
      * a value beginning with ``-`` (would be parsed as a git option),
      * a path whose realpath (symlinks resolved) is not the base itself or a
        descendant of it — this catches ``..`` traversal, absolute out-of-base
        paths and symlink escapes alike.

    Returns the resolved absolute path on success; this is the exact string
    placed into the argv after the ``--`` separator.
    """
    if path.startswith("-"):
        raise ValueError(
            f"refusing worktree path that begins with '-' (would be parsed as a "
            f"git option, not a path): {path!r}"
        )
    base_real = os.path.realpath(os.path.expanduser(allowed_base))
    target_real = os.path.realpath(os.path.expanduser(path))
    base_prefix = base_real.rstrip(os.sep) + os.sep
    if target_real != base_real and not target_real.startswith(base_prefix):
        raise ValueError(
            f"refusing worktree path that escapes the allowed base "
            f"{allowed_base!r}: {path!r} -> {target_real!r}"
        )
    return target_real


def build_worktree_add_argv(
    repo_path: str,
    branch_name: str,
    worktree_path: str,
    allowed_base: str,
) -> list[str]:
    """Build the argv for ``git worktree add`` (list-form, hardened).

    Validates the branch name and confines the worktree path before assembling
    a list-form argv with a ``--`` separator before the path positional.
    Raises ``ValueError`` if either input is rejected.
    """
    validate_branch_name(branch_name)
    safe_path = confine_worktree_path(worktree_path, allowed_base)
    # `-b <branch>` then `--` then the path positional, so neither the branch
    # nor the path can be reinterpreted as an option. `repo_path` selects the
    # owning repo via `-C` (an argv element, never shell-interpolated).
    return [
        "git",
        "-C",
        repo_path,
        "worktree",
        "add",
        "-b",
        branch_name,
        "--",
        safe_path,
    ]


def build_worktree_remove_argv(
    worktree_path: str,
    allowed_base: str,
    repo_path: str | None = None,
) -> list[str]:
    """Build the argv for ``git worktree remove --force`` (list-form, hardened).

    Confines the worktree path before assembling a list-form argv with a ``--``
    separator before the path positional. When ``repo_path`` is given it is
    passed via ``-C`` to select the owning repo. Raises ``ValueError`` if the
    path is rejected.
    """
    safe_path = confine_worktree_path(worktree_path, allowed_base)
    argv = ["git"]
    if repo_path is not None:
        argv += ["-C", repo_path]
    argv += ["worktree", "remove", "--force", "--", safe_path]
    return argv


def build_worktree_list_argv(repo_path: str | None = None) -> list[str]:
    """Build the argv for ``git worktree list --porcelain`` (list-form).

    No caller-controlled positional, so nothing to confine; ``repo_path`` (when
    given) is passed via ``-C`` as an argv element.
    """
    argv = ["git"]
    if repo_path is not None:
        argv += ["-C", repo_path]
    argv += ["worktree", "list", "--porcelain"]
    return argv


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

    def scan(self, watch_paths: list[str] | None = None) -> list[TrackedWorktree]:
        """Scan watch paths for git worktrees.

        ``watch_paths`` optionally overrides the configured roots (e.g. a
        dispatcher rescanning a single directory after an AGENTS.md event).
        """
        import os

        roots = watch_paths if watch_paths is not None else self._config.watch_paths
        discovered: list[TrackedWorktree] = []
        for root_path in roots:
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

    def remove_stale(
        self,
        active_paths: set[str],
        restrict_to: list[str] | None = None,
    ) -> list[str]:
        """Drop tracking for worktrees that no longer exist AND reclaim the
        abandoned worktree directory on the filesystem.

        For each tracked path no longer present in ``active_paths`` we both
        forget the in-memory entry (returning its todo id) and invoke
        ``git worktree remove --force`` so the directory is actually reclaimed,
        falling back to ``git worktree prune`` for stale administrative refs.
        Without this the daemon would leak abandoned worktree directories
        forever.

        ``restrict_to``, when given, limits eviction to tracked paths that live
        under one of those roots — so a narrowed event-driven scan does not
        evict worktrees in directories it never rescanned.
        """
        removed: list[str] = []
        restrict_roots = (
            [os.path.abspath(os.path.expanduser(p)) for p in restrict_to]
            if restrict_to is not None
            else None
        )
        for path in list(self._tracked):
            if path in active_paths:
                continue
            if restrict_roots is not None and not self._under_any_root(path, restrict_roots):
                continue
            wt = self._tracked.pop(path)
            self._reclaim_worktree_dir(path)
            if wt.todo_id:
                removed.append(wt.todo_id)
        return removed

    @staticmethod
    def _under_any_root(path: str, roots: list[str]) -> bool:
        abspath = os.path.abspath(os.path.expanduser(path))
        for root in roots:
            root_prefix = root.rstrip(os.sep) + os.sep
            if abspath == root or abspath.startswith(root_prefix):
                return True
        return False

    @staticmethod
    def _reclaim_worktree_dir(path: str) -> None:
        """Actually remove an abandoned git worktree directory.

        Runs ``git worktree remove --force -- <path>`` from the worktree's own
        directory (so git locates the owning repo), then ``git worktree prune``
        to clear stale administrative entries. All failures are swallowed —
        reclamation is best-effort and must never raise into the scan loop.

        Hardened: the ``path`` is validated and realpath-confined to its own
        parent directory BEFORE any subprocess runs. A value beginning with
        ``-`` (which git would parse as an option) or one whose realpath
        escapes its parent is refused and NO subprocess is invoked — reclaim
        fails closed rather than letting an injection-y path reach git.
        """
        import contextlib
        import subprocess

        # Confine the path to its own parent directory (resolves symlinks and
        # rejects a leading-dash / out-of-base value). Fail closed: if the path
        # cannot be validated, reclaim nothing rather than exec git on it.
        try:
            parent = os.path.dirname(os.path.realpath(os.path.expanduser(path))) or os.sep
            safe_path = confine_worktree_path(path, parent)
        except ValueError:
            return

        # `git -C <safe_path>` resolves the linked working tree's main
        # repository. List-form argv with a `--` separator before the path
        # positional (built by build_worktree_remove_argv) so the path can
        # never be reinterpreted as an option.
        for argv in (
            build_worktree_remove_argv(safe_path, parent, repo_path=safe_path),
            ["git", "-C", safe_path, "worktree", "prune"],
        ):
            with contextlib.suppress(Exception):
                subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )


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

    def evaluate(self, watch_paths: list[str] | None = None) -> list[dict[str, object]]:
        """Evaluate worktrees and return list of todos to create.

        ``watch_paths`` optionally narrows the scan to specific directories
        (used by the event dispatcher when a single AGENTS.md change fires);
        when ``None`` the configured ``watch_paths`` are scanned. The parameter
        is accepted (and used) so the dispatcher's
        ``evaluate(watch_paths=[...])`` call site does not raise ``TypeError``.
        """
        if not self._config.enabled:
            return []

        discovered = self._scanner.scan(watch_paths=watch_paths)
        active_paths = {wt.path for wt in discovered}
        # Only reconcile (and prune) tracking for the paths we actually
        # rescanned — a narrowed event-driven scan must not evict worktrees in
        # directories it never looked at.
        self._scanner.remove_stale(active_paths, restrict_to=watch_paths)

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
