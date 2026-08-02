"""E2E integration tests for the ``agent_orchestrate`` role.

Tests the role via direct subprocess invocation of ``ansible-playbook`` against
the molecule mock daemon — no adapter layer, no forking interference.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from socket import socket

import pytest

_MOCK_DAEMON = (
    Path(__file__).parent.parent.parent
    / "molecule" / "mock_daemon" / "server.py"
)
_PLAYBOOK = (
    Path(__file__).parent.parent.parent
    / "playbooks" / "agent_orchestrate.yml"
)
_COLLECTIONS = (
    Path(__file__).parent.parent.parent / "collections"
)


def _free_port() -> int:
    with socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class _DaemonProcess:
    def __init__(self):
        self.port = _free_port()
        self.host = "127.0.0.1"
        self._proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        env = os.environ.copy()
        self._proc = subprocess.Popen(
            ["python3", str(_MOCK_DAEMON), "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self._wait_ready()

    def stop(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)

    def _wait_ready(self, timeout: float = 5) -> None:
        import urllib.request
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                req = urllib.request.Request(f"{self.url}/healthz", method="GET")
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                pass
            time.sleep(0.1)
        raise TimeoutError(f"mock daemon did not start on {self.url}")


def _has_ansible() -> bool:
    return shutil.which("ansible-playbook") is not None


pytestmark = pytest.mark.skipif(
    not _has_ansible(), reason="ansible-playbook not installed"
)


@pytest.fixture()
def daemon():
    d = _DaemonProcess()
    d.start()
    yield d
    d.stop()


def _run_playbook(
    daemon_url: str,
    artifact_dir: str,
    work_type: str = "feature",
    min_remaining_usd: float = 0.02,
    enable_db_read: bool = False,
    todo_id: str = "",
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["ANSIBLE_COLLECTIONS_PATH"] = str(_COLLECTIONS)
    return subprocess.run(
        [
            "ansible-playbook",
            str(_PLAYBOOK),
            "--connection", "local",
            "-e", f"daemon_url={daemon_url}",
            "-e", f"work_type={work_type}",
            "-e", "prompt_text=Write a function that returns 42",
            "-e", "skill_body=You are a coding assistant.",
            "-e", f"todo_id={todo_id}",
            "-e", f"min_remaining_usd={min_remaining_usd}",
            "-e", "quality_threshold=0.6",
            "-e", "max_retries=2",
            "-e", "capability_role=agent_task",
            "-e", f"enable_db_read={'true' if enable_db_read else 'false'}",
            "-e", f"artifact_dir={artifact_dir}",
            "-e", "psk=",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


# ---------------------------------------------------------------------------
# 1. Budget floor deferral
# ---------------------------------------------------------------------------

def test_role_defers_when_below_budget_floor(tmp_path: Path, daemon: _DaemonProcess) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    result = _run_playbook(str(daemon.url), str(artifact_dir),
                           work_type="feature", min_remaining_usd=100.0)
    assert result.returncode == 0, f"playbook failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}"
    artifact = artifact_dir / "agent_orchestrate_result.json"
    assert artifact.is_file()
    data = json.loads(artifact.read_text())
    assert data["status"] == "deferred"
    assert data["reason"] == "budget_floor"


# ---------------------------------------------------------------------------
# 2. Single-shot model call
# ---------------------------------------------------------------------------

def test_role_dispatches_via_single_shot_model_call(tmp_path: Path, daemon: _DaemonProcess) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    result = _run_playbook(str(daemon.url), str(artifact_dir), work_type="docs")
    assert result.returncode == 0, f"playbook failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}"
    artifact = artifact_dir / "agent_orchestrate_result.json"
    data = json.loads(artifact.read_text())
    assert data["status"] == "success"
    assert data["path"] == "single_shot"


# ---------------------------------------------------------------------------
# 3. Workflow dispatch
# ---------------------------------------------------------------------------

def test_role_dispatches_via_langgraph_workflow(tmp_path: Path, daemon: _DaemonProcess) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    result = _run_playbook(str(daemon.url), str(artifact_dir), work_type="bugfix")
    assert result.returncode == 0, f"playbook failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}"
    artifact = artifact_dir / "agent_orchestrate_result.json"
    data = json.loads(artifact.read_text())
    assert data["status"] == "success"
    assert data["path"] == "workflow"
    assert data["model_profile"] == "glm-4.6"
    assert data["quality_score"] > 0.5
    assert "content_excerpt" in data
    assert len(data["content_excerpt"]) > 0


# ---------------------------------------------------------------------------
# 4. Workflow + todo lifecycle
# ---------------------------------------------------------------------------

def test_role_workflow_with_todo_lifecycle(tmp_path: Path, daemon: _DaemonProcess) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    result = _run_playbook(str(daemon.url), str(artifact_dir),
                           work_type="feature", enable_db_read=True,
                           todo_id="TODO-ORCH-001")
    assert result.returncode == 0, f"playbook failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}"
    artifact = artifact_dir / "agent_orchestrate_result.json"
    data = json.loads(artifact.read_text())
    assert data["status"] == "success"
    assert data["work_type"] == "feature"
    assert data["path"] == "workflow"


# ---------------------------------------------------------------------------
# 5. Actionable advice output
# ---------------------------------------------------------------------------

def test_role_produces_actionable_advice_output(tmp_path: Path, daemon: _DaemonProcess) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    result = _run_playbook(str(daemon.url), str(artifact_dir),
                           work_type="refactor", todo_id="TODO-ADVICE-1")
    assert result.returncode == 0, f"playbook failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}"
    artifact = artifact_dir / "agent_orchestrate_result.json"
    data = json.loads(artifact.read_text())
    required_keys = ("todo_id", "work_type", "path", "model_profile",
                      "quality_score", "est_cost_usd", "content_excerpt")
    for key in required_keys:
        assert key in data, f"missing actionable output key: {key}"
    assert data["todo_id"] == "TODO-ADVICE-1"
    assert data["work_type"] == "refactor"
    assert data["path"] == "workflow"
    assert data["model_profile"] == "glm-4.6"
    assert data["est_cost_usd"] is not None
    assert data["quality_score"] > 0.0


# ---------------------------------------------------------------------------
# 6. Multi-work-type coordination
# ---------------------------------------------------------------------------

def test_role_coordinates_multiple_work_types(tmp_path: Path, daemon: _DaemonProcess) -> None:
    results = {}
    for work_type in ("feature", "docs", "chat"):
        artifact_dir = tmp_path / work_type
        artifact_dir.mkdir()
        result = _run_playbook(str(daemon.url), str(artifact_dir), work_type=work_type)
        assert result.returncode == 0, f"failed for {work_type}:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}"
        artifact = artifact_dir / "agent_orchestrate_result.json"
        data = json.loads(artifact.read_text())
        results[work_type] = data

    assert results["feature"]["path"] == "workflow"
    assert results["docs"]["path"] == "single_shot"
    assert results["chat"]["path"] == "single_shot"

    for _wt, data in results.items():
        assert data["model_profile"] in ("glm-4.6", "mock-profile")
        assert len(data.get("content_excerpt", "")) > 0 or data["path"] == "workflow"


# ---------------------------------------------------------------------------
# 7. Failure artifact on unreachable daemon
# ---------------------------------------------------------------------------

def test_role_writes_failure_artifact_when_daemon_unreachable(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    result = _run_playbook("http://127.0.0.1:1", str(artifact_dir), work_type="feature")
    artifact = artifact_dir / "agent_orchestrate_result.json"
    if artifact.is_file():
        data = json.loads(artifact.read_text())
        assert data["status"] == "failed"
    else:
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# 8. Budget_ok=true non-deferral
# ---------------------------------------------------------------------------

def test_role_proceeds_when_budget_ok(tmp_path: Path, daemon: _DaemonProcess) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    result = _run_playbook(str(daemon.url), str(artifact_dir),
                           work_type="feature", enable_db_read=False)
    assert result.returncode == 0, f"playbook failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}"
    artifact = artifact_dir / "agent_orchestrate_result.json"
    data = json.loads(artifact.read_text())
    assert data["status"] == "success", (
        f"expected success (budget_ok=true), got {data.get('status')}"
    )


def test_role_escalates_weak_profile_instead_of_bypassing_small_model_policy(
    tmp_path: Path, daemon: _DaemonProcess
) -> None:
    """A weak-profile recommendation cannot enter a side-effecting role."""
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    result = _run_playbook(
        str(daemon.url), str(artifact_dir), work_type="bounded_small_model"
    )

    assert result.returncode == 0, (
        f"playbook failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}"
    )
    data = json.loads(
        (artifact_dir / "agent_orchestrate_result.json").read_text()
    )
    assert data["model_profile"] == "mock-profile"
    assert data["small_model_policy"] == {
        "action": "escalate",
        "reason": "collection_role_has_side_effects",
        "recommended_profile": "mock-weak",
        "selected_profile": "mock-profile",
    }
