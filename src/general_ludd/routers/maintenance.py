"""Maintenance/admin endpoints: code intelligence, dependency, quality gate.

Surfaces three read/check capabilities over the daemon API:
  - GitIntelligence  -> hot files / recent commits for the daemon's repo
  - DependencyManager -> outdated package report (read-only)
  - QualityGateChecker -> evaluate a coverage number against the quality gate
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    repo_root = os.environ.get("GLUDD_REPO_ROOT", ".")

    @app.get("/admin/code-intel/hot-files")
    async def code_intel_hot_files(limit: int = 10) -> dict[str, Any]:
        from general_ludd.code_intelligence.git_intel import GitIntelligence

        gi = GitIntelligence(repo_path=repo_root)
        return {
            "hot_files": gi.hot_files(limit=limit),
            "recent_commits": gi.recent_commits(limit=min(limit, 20)),
        }

    @app.get("/admin/deps/outdated")
    async def deps_outdated() -> dict[str, Any]:
        from general_ludd.dependency.manager import DependencyManager

        dm = DependencyManager(project_root=repo_root)
        outdated = await dm.check_for_updates()
        return {
            "outdated": [
                {
                    "name": p.name,
                    "current_version": p.current_version,
                    "latest_version": p.latest_version,
                }
                for p in outdated
            ],
            "count": len(outdated),
        }

    @app.post("/admin/issues/poll")
    async def issues_poll(payload: dict[str, Any]) -> dict[str, Any]:
        # Poll a GitHub repo's labeled issues and return them as todo specs.
        # Persistence into the queue is the caller's choice; this surfaces the
        # ingestor's output so it can feed the intake queue.
        from general_ludd.git_automation.issue_ingestor import GitHubIssueIngestor

        owner = str(payload.get("owner", ""))
        repo = str(payload.get("repo", ""))
        label = str(payload.get("label", "gludd"))
        # Dedup must survive across requests: a fresh ingestor is built per
        # request, so its per-instance _seen_ids would always be empty and
        # every poll would re-emit all issues. Persist a per-(owner/repo/label)
        # seen-id set in the daemon state and hand it to the ingestor.
        store: dict[str, set[int | str]] = _daemon_state.setdefault(
            "issue_ingestor_seen_ids", {}
        )
        seen = store.setdefault(f"{owner}/{repo}#{label}", set())

        ingestor = GitHubIssueIngestor(
            owner=owner,
            repo=repo,
            label=label,
            seen_ids=seen,
        )
        new_todos = await ingestor.poll_issues()
        return {"new_todos": new_todos, "count": len(new_todos)}

    @app.post("/admin/quality/check")
    async def quality_check(payload: dict[str, Any]) -> dict[str, Any]:
        from general_ludd.quality.gate import QualityGateChecker

        checker = QualityGateChecker()
        return checker.check_python_coverage(
            coverage_percent=float(payload.get("coverage_percent", 0.0)),
            branch_percent=payload.get("branch_percent"),
        )
