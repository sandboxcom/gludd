"""Apply results: SelfApply (hot-reload gludd) vs ExternalApply (commit to external project)."""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class _Reloader(Protocol):
    """Structural type for HotReloader's reload_changed_modules."""

    def reload_changed_modules(
        self,
        repo_dir: str,
        changed_paths: list[str],
        health_check: Any = None,
        role: str | None = None,
    ) -> Any: ...


@runtime_checkable
class _EventBus(Protocol):
    """Structural type for the daemon event bus."""

    def publish(self, event: Any) -> None: ...


class SelfApply:
    """Commit a self-improvement change AND hot-reload the running gludd daemon.

    This is the path for gludd improving ITSELF: after a self-improve todo
    generates a code change in the gludd workspace, call this to commit it
    and swap the live modules without a restart.
    """

    def apply(
        self,
        workspace_repo_dir: str,
        message: str,
        reloader: _Reloader,
        health_check: Any = None,
        role: str | None = None,
        event_bus: _EventBus | None = None,
    ) -> dict[str, Any]:
        """Commit a self-change, hot-reload it, and publish bounded evidence."""
        from general_ludd.events.types import SelfUpdateAppliedEvent
        from general_ludd.git_automation.repo import GitAutomation

        git = GitAutomation(repo_path=workspace_repo_dir)
        changed = git.changed_files()
        commit_sha = git.commit_and_push(message)

        reload_result = reloader.reload_changed_modules(
            repo_dir=workspace_repo_dir,
            changed_paths=changed,
            health_check=health_check,
            role=role,
        )
        reloaded: list[str] = []
        if hasattr(reload_result, "details"):
            details = reload_result.details
            reloaded = list(
                details.get("reloaded_modules", [])
            ) + list(details.get("added_modules", []))
        elif isinstance(reload_result, dict):
            reloaded = list(
                reload_result.get("reloaded_modules", [])
            ) + list(reload_result.get("added_modules", []))
        else:
            reloaded = []

        if event_bus is not None:
            event_bus.publish(
                SelfUpdateAppliedEvent(
                    commit_sha=commit_sha,
                    reloaded_modules=list(reloaded),
                )
            )

        return {
            "commit_sha": commit_sha,
            "changed_files": changed,
            "reload_success": getattr(reload_result, "success", True),
            "reloaded_modules": reloaded,
        }


class ExternalApply:
    """Commit changes to an EXTERNAL project workspace — no hot-reload.

    This is the path for gludd improving another project: after a gap-analysis
    generates code changes in the external project's workspace clone, call this
    to commit and push them. The external project is NOT gludd itself, so there
    is no hot-reload path — just a standard git commit+push.
    """

    def apply(
        self,
        workspace_repo_dir: str,
        message: str,
        event_bus: _EventBus | None = None,
    ) -> dict[str, Any]:
        """Commit an external-project change without reloading Gludd."""
        from general_ludd.git_automation.repo import GitAutomation

        git = GitAutomation(repo_path=workspace_repo_dir)
        changed = git.changed_files()
        commit_sha = git.commit_and_push(message)

        return {
            "commit_sha": commit_sha,
            "changed_files": changed,
        }
