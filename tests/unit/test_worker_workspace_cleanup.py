"""Regression test: worker cleans up workspace on both success AND failure paths.

Bug: successful jobs never called shutil.rmtree on their workspace dir,
allowing prompt/model-output artifacts to accumulate (disk DoS + data retention).
The failure path already cleaned up; this file ensures the success path does too.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from general_ludd.worker.app import create_app


def _make_dirs(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "job-workspace"
    root.mkdir()
    return {"root": str(root)}


def _stub_runner(dirs: dict[str, str]):
    runner = MagicMock()
    # The worker's /jobs/execute guards against unknown playbooks via
    # get_playbook_registry() == set(get_runner().list_playbooks()).  Declare the
    # synthetic playbook so the allowlist guard permits execution and the
    # workspace-cleanup path under test actually runs.
    runner.list_playbooks.return_value = ["test_playbook"]
    runner.prepare_job_dirs.return_value = dirs
    runner.write_vars.return_value = None
    runner.run_playbook.return_value = {
        "rc": 0,
        "output": "ok",
        "artifacts": [],
        "events": [],
    }
    return runner


class TestWorkerWorkspaceCleanup:
    """Workspace dir must be removed after job execution regardless of outcome."""

    def test_workspace_removed_on_success(self, tmp_path):
        """Successful job execution must rmtree the workspace."""
        dirs = _make_dirs(tmp_path)
        workspace = Path(dirs["root"])
        assert workspace.exists(), "pre-condition: workspace must exist before call"

        runner = _stub_runner(dirs)

        app = create_app()
        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch(
                "general_ludd.worker.app.invoke_model_for_generation",
                new_callable=AsyncMock,
                return_value=None,
            ),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/jobs/execute",
                json={
                    "job_id": "JOB-CLEANUP-001",
                    "playbook": "test_playbook",
                    "queue": "core",
                },
            )
        assert resp.status_code == 200, resp.text
        assert not workspace.exists(), (
            "workspace must be removed after successful job execution"
        )

    def test_workspace_removed_on_failure(self, tmp_path):
        """Failed job execution must also rmtree the workspace (pre-existing behaviour)."""
        dirs = _make_dirs(tmp_path)
        workspace = Path(dirs["root"])
        assert workspace.exists(), "pre-condition: workspace must exist before call"

        runner = _stub_runner(dirs)
        runner.run_playbook.side_effect = RuntimeError("playbook exploded")

        app = create_app()
        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch(
                "general_ludd.worker.app.invoke_model_for_generation",
                new_callable=AsyncMock,
                return_value=None,
            ),
            # raise_server_exceptions=False so the endpoint's re-raised runner
            # error surfaces as a 500 response (the worker has no exception
            # handler that converts it) rather than propagating out of the test
            # client.  The cleanup we assert below runs in the endpoint's
            # ``except`` branch before the re-raise regardless.
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            resp = client.post(
                "/jobs/execute",
                json={
                    "job_id": "JOB-CLEANUP-002",
                    "playbook": "test_playbook",
                    "queue": "core",
                },
            )
        assert resp.status_code == 500
        assert not workspace.exists(), (
            "workspace must be removed after a failed job execution"
        )


class TestArtifactsAndEventsAreInlineAfterCleanup:
    """Regression guard: artifacts and events in the response are inline data
    (dicts/strings), NOT file paths under the workspace dir.  They must survive
    in the response even after the workspace is removed, because rmtree cannot
    affect in-memory data already captured into response_payload.

    This test pins the contract: if a future runner ever puts real file paths
    into runner_result["artifacts"], the runner must copy those files out to
    a persistent store before returning, so this cleanup remains safe.
    """

    def test_artifacts_and_events_survive_workspace_cleanup(self, tmp_path):
        """artifacts and events in the response are inline dicts, not file paths.

        The workspace is deleted on success; the returned artifacts/events must
        still be present and match what the runner returned — proving they were
        captured into memory before cleanup, not re-read from disk.
        """
        dirs = _make_dirs(tmp_path)
        workspace = Path(dirs["root"])

        inline_event = {
            "event": "runner_on_ok",
            "host": "localhost",
            "task": "gather facts",
            "result": {"changed": False},
        }

        runner = MagicMock()
        # Declare the synthetic playbook so the worker's allowlist guard
        # (get_playbook_registry()) permits execution to the cleanup path.
        runner.list_playbooks.return_value = ["test_playbook"]
        runner.prepare_job_dirs.return_value = dirs
        runner.write_vars.return_value = None
        runner.run_playbook.return_value = {
            "rc": 0,
            "output": "done",
            # artifacts is an inline list of dicts (never file paths under workspace)
            "artifacts": [{"name": "summary", "content": "all clear"}],
            "events": [inline_event],
        }

        app = create_app()
        with (
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch(
                "general_ludd.worker.app.invoke_model_for_generation",
                new_callable=AsyncMock,
                return_value=None,
            ),
            TestClient(app) as client,
        ):
            resp = client.post(
                "/jobs/execute",
                json={
                    "job_id": "JOB-INLINE-001",
                    "playbook": "test_playbook",
                    "queue": "core",
                },
            )

        assert resp.status_code == 200, resp.text

        # Workspace must be gone — cleanup ran.
        assert not workspace.exists(), "workspace must be removed after successful job"

        body = resp.json()

        # Inline artifacts survived the workspace deletion.
        assert body["artifacts"] == [{"name": "summary", "content": "all clear"}], (
            "artifacts must be inline dicts returned verbatim in the response; "
            "if this fails a future runner may have put file paths here"
        )

        # Inline events survived the workspace deletion.
        assert body["events"] == [inline_event], (
            "events must be inline dicts returned verbatim in the response"
        )
