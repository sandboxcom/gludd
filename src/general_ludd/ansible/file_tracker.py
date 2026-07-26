"""Ansible file-module change tracking.

Hooks into ansible-runner event callbacks to capture file-level changes
made by known file-management modules, then surfaces a structured agent
context including git diffs and per-file details.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FILE_MODULE_NAMES = frozenset({
    "copy",
    "template",
    "file",
    "blockinfile",
    "lineinfile",
    "replace",
    "assemble",
    "ini_file",
})

FILE_MODULES: frozenset[str] = _FILE_MODULE_NAMES | frozenset({
    f"ansible.builtin.{name}" for name in _FILE_MODULE_NAMES
})

_RUNNER_ON_OK = "runner_on_ok"
_RES_FIELDS = frozenset({"dest", "src", "checksum", "changed", "diff"})


class FileChangeTracker:
    """Tracks file-level changes made during an ansible-runner playbook run.

    Captures the git SHA before the run begins, filters runner events for
    known file-modifying modules, and exposes git-diff state after the run
    completes.  The ``build_agent_context()`` method returns a structured
    dict suitable for passing to an LLM agent as context.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        self._git_sha_before = _git_rev_parse(repo_root, "HEAD")
        self._file_events: list[dict[str, Any]] = []

    # -- event ingestion ---------------------------------------------------

    def event_handler(self, event_data: object) -> None:
        """Ingest an ansible-runner callback event.

        Only ``runner_on_ok`` events whose resolved module name is in
        ``FILE_MODULES`` are retained.  The relevant result keys (dest,
        src, checksum, changed, diff) are extracted from
        ``event_data["event_data"]["res"]``.
        """
        if not isinstance(event_data, dict):
            return
        if event_data.get("event") != _RUNNER_ON_OK:
            return

        ed = event_data.get("event_data", {})
        if not isinstance(ed, dict):
            return
        task_name = ed.get("task", "")
        if not _task_uses_file_module(task_name):
            return

        res = ed.get("res", {})
        if not isinstance(res, dict):
            return

        entry: dict[str, Any] = {
            "task": task_name,
            "host": ed.get("host", ""),
        }
        for field in _RES_FIELDS:
            if field in res:
                entry[field] = res[field]

        self._file_events.append(entry)
        logger.debug(
            "FileChangeTracker captured event: %s on %s (fields: %s)",
            task_name,
            entry.get("host", "?"),
            [k for k in entry if k not in ("task", "host")],
        )

    # -- git queries -------------------------------------------------------

    def get_git_diff(self) -> str:
        """Return the unified diff for the repo root since the captured SHA.

        Falls back to a working-tree diff when ``HEAD`` did not exist at
        capture time (e.g. an empty repository before the first commit).
        """
        if self._git_sha_before is None:
            return _git_diff_working(self._repo_root)
        return _git_diff_range(self._repo_root, self._git_sha_before)

    def get_changed_files(self) -> str:
        """Return ``git diff --name-status`` output since the captured SHA.

        Falls back to a working-tree diff when ``HEAD`` did not exist at
        capture time.
        """
        if self._git_sha_before is None:
            return _git_diff_name_status_working(self._repo_root)
        return _git_diff_name_status_range(
            self._repo_root, self._git_sha_before
        )

    # -- agent context -----------------------------------------------------

    def build_agent_context(self) -> dict[str, Any]:
        """Return a structured dict for consumption by an LLM agent.

        Keys:
        * ``playbook_summary`` — count of file-modifying events + per-event dicts.
        * ``git_state`` — git SHA before the run and git SHA after.
        * ``file_details`` — per-event ``{dest, src, checksum, changed, diff}``.
        * ``git_diff`` — full unified diff text.
        """
        git_sha_after = _git_rev_parse(self._repo_root, "HEAD")
        return {
            "playbook_summary": {
                "file_events_count": len(self._file_events),
                "events": self._file_events,
            },
            "git_state": {
                "sha_before": self._git_sha_before,
                "sha_after": git_sha_after,
            },
            "file_details": self._file_events,
            "git_diff": self.get_git_diff(),
        }


# -- helpers ---------------------------------------------------------------

def _task_uses_file_module(task_name: object) -> bool:
    """Return ``True`` when *task_name* references a file-management module.

    The event's task field is of the form ``module_name [action_name]``
    (e.g. ``ansible.builtin.copy copy``, ``template Deploy config template``).
    We extract the leading module name before the first space and check it
    against ``FILE_MODULES``.
    """
    if not isinstance(task_name, str):
        return False
    module = task_name.split(" ", 1)[0]
    return module in FILE_MODULES


def _git_command(
    repo_root: Path,
    argv: list[str],
) -> str:
    proc = subprocess.run(
        ["git", *argv],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout


def _git_rev_parse(repo_root: Path, ref: str) -> str | None:
    """Return the SHA for *ref* or ``None`` when the ref does not exist."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _git_diff_range(repo_root: Path, sha_before: str) -> str:
    return _git_command(repo_root, ["diff", sha_before, "HEAD"])


def _git_diff_working(repo_root: Path) -> str:
    """Diff of uncommitted working-tree changes (no prior commit)."""
    return _git_command(repo_root, ["diff"])


def _git_diff_name_status_range(repo_root: Path, sha_before: str) -> str:
    return _git_command(
        repo_root, ["diff", "--name-status", sha_before, "HEAD"]
    )


def _git_diff_name_status_working(repo_root: Path) -> str:
    return _git_command(repo_root, ["diff", "--name-status"])
