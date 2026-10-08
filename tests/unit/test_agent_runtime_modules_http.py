"""HTTP mock tests for collection modules migrated to daemon-owned operations."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ansible_collections.general_ludd.agent.plugins.module_utils.push_rate_guard import (
    ForcePushTracker,
)

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "collections/ansible_collections/general_ludd/agent/plugins/modules"


def test_collection_push_rate_guard_is_atomic_bounded_and_resettable(tmp_path: Path) -> None:
    state_file = tmp_path / "pushes.json"
    tracker = ForcePushTracker(state_file, max_bypasses=2, window_hours=1.0)

    assert tracker.count == 0
    assert tracker.is_bypass_allowed() is True
    tracker.record_bypass()
    tracker.record_bypass()
    assert tracker.count == 2
    assert tracker.is_bypass_allowed() is False
    assert not list(tmp_path.glob(".pushes.json.*"))

    tracker.record_normal_push()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert tracker.count == 0
    assert state["bypass_times"] == []
    assert isinstance(state["last_normal_push"], float)


def test_collection_push_rate_guard_validates_and_prunes_state(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        ForcePushTracker(tmp_path / "bad.json", max_bypasses=0)
    state_file = tmp_path / "pushes.json"
    state_file.write_text(
        json.dumps({"bypass_times": [time.time() - 7200], "last_normal_push": None}),
        encoding="utf-8",
    )
    assert ForcePushTracker(state_file, window_hours=1.0).count == 0
    assert json.loads(state_file.read_text(encoding="utf-8"))["bypass_times"] == []

    state_file.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid force-push state"):
        ForcePushTracker(state_file).record_bypass()


class _FakeAnsibleModule:
    def __init__(self, params: dict[str, Any], *, check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs


class _FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.init: dict[str, Any] = {}

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        return self.response


def _load(name: str) -> ModuleType:
    path = MODULES / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    params: dict[str, Any],
    response: dict[str, Any],
    *,
    check_mode: bool = False,
) -> tuple[_FakeAnsibleModule, _FakeClient]:
    module = _load(name)
    ansible = _FakeAnsibleModule(params, check_mode=check_mode)
    client = _FakeClient(response)

    def _client_factory(**kwargs: Any) -> _FakeClient:
        client.init = kwargs
        return client

    monkeypatch.setattr(module, "AnsibleModule", lambda **_: ansible)
    monkeypatch.setattr(module, "GluddClient", _client_factory)
    module.main()
    return ansible, client


def test_reload_hashes_candidate_and_sends_idempotent_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "leaf.py"
    candidate.write_text("VALUE = 2\n", encoding="utf-8")
    params = {
        "module_name": "general_ludd.example.leaf",
        "candidate_source_path": str(candidate),
        "health_url": "http://127.0.0.1:8000/readyz",
        "health_timeout": 2.0,
        "config_dir": "config",
        "base_source_path": None,
        "role": None,
        "expected_sha256": None,
        "idempotency_key": None,
        "result_path": None,
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
    }
    ansible, client = _run(
        monkeypatch,
        "gludd_reload",
        params,
        {"_status": 200, "success": True, "rolled_back": False, "details": {}},
    )

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["success"] is True
    path, body = client.posts[0]
    assert path == "/admin/reload/code"
    assert len(body["expected_sha256"]) == 64
    assert body["idempotency_key"].startswith("reload:general_ludd.example.leaf:")
    assert "config_dir" not in body


def test_reload_rejects_digest_mismatch_before_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "leaf.py"
    candidate.write_text("VALUE = 2\n", encoding="utf-8")
    params = {
        "module_name": "general_ludd.example.leaf",
        "candidate_source_path": str(candidate),
        "health_url": None,
        "health_timeout": 2.0,
        "config_dir": "config",
        "base_source_path": None,
        "role": None,
        "expected_sha256": "0" * 64,
        "idempotency_key": None,
        "result_path": None,
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
    }
    ansible, client = _run(monkeypatch, "gludd_reload", params, {"_status": 200})

    assert ansible.exited is None
    assert ansible.failed is not None
    assert "digest" in ansible.failed["msg"]
    assert client.posts == []


def test_reload_http_failure_preserves_rollback_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "leaf.py"
    candidate.write_text("VALUE = 2\n", encoding="utf-8")
    params: dict[str, Any] = {
        "module_name": "general_ludd.example.leaf",
        "candidate_source_path": str(candidate),
        "health_url": None,
        "health_timeout": 2.0,
        "config_dir": "config",
        "base_source_path": str(tmp_path / "base.py"),
        "role": "release",
        "expected_sha256": None,
        "idempotency_key": "reload:explicit",
        "result_path": None,
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
    }
    ansible, client = _run(
        monkeypatch,
        "gludd_reload",
        params,
        {"_status": 503, "detail": "reload unavailable", "rolled_back": True},
    )

    assert ansible.exited is None
    assert ansible.failed is not None
    assert ansible.failed["msg"] == "reload unavailable"
    assert ansible.failed["rolled_back"] is True
    assert client.posts[0][1]["base_source_path"] == str(tmp_path / "base.py")
    assert client.posts[0][1]["idempotency_key"] == "reload:explicit"


def test_reload_writes_promotion_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "leaf.py"
    candidate.write_text("VALUE = 2\n", encoding="utf-8")
    artifact = tmp_path / "promotion.json"
    params: dict[str, Any] = {
        "module_name": "general_ludd.example.leaf",
        "candidate_source_path": str(candidate),
        "health_url": None,
        "health_timeout": 2.0,
        "config_dir": "config",
        "base_source_path": None,
        "role": None,
        "expected_sha256": None,
        "idempotency_key": None,
        "result_path": str(artifact),
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
    }
    ansible, _ = _run(
        monkeypatch,
        "gludd_reload",
        params,
        {"_status": 200, "success": True, "rolled_back": False, "details": {}},
    )

    assert ansible.failed is None
    assert ansible.exited is not None
    assert artifact.is_file()
    assert '"success": true' in artifact.read_text(encoding="utf-8")


def test_reload_artifact_write_failure_is_clean(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "leaf.py"
    candidate.write_text("VALUE = 2\n", encoding="utf-8")
    params: dict[str, Any] = {
        "module_name": "general_ludd.example.leaf",
        "candidate_source_path": str(candidate),
        "health_url": None,
        "health_timeout": 2.0,
        "config_dir": "config",
        "base_source_path": None,
        "role": None,
        "expected_sha256": None,
        "idempotency_key": None,
        "result_path": str(tmp_path),
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
    }
    ansible, _ = _run(
        monkeypatch,
        "gludd_reload",
        params,
        {"_status": 200, "success": True, "rolled_back": False, "details": {}},
    )

    assert ansible.exited is None
    assert ansible.failed is not None
    assert "promotion result" in ansible.failed["msg"]


def test_skill_preserves_rendered_result_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    params = {
        "name": "review",
        "trigger": None,
        "variables": {"project": "Gludd"},
        "skills_path": "",
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
        "timeout": 30,
    }
    response = {
        "_status": 200,
        "skill_name": "review",
        "rendered_body": "Hello Gludd!",
        "required_vars": ["project"],
    }
    ansible, client = _run(monkeypatch, "gludd_skill", params, response)

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["rendered_body"] == "Hello Gludd!"
    assert client.posts == [
        (
            "/admin/skills/render",
            {"name": "review", "trigger": None, "variables": {"project": "Gludd"}, "skills_path": None},
        )
    ]


def test_skill_http_failure_is_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    params: dict[str, Any] = {
        "name": "missing",
        "trigger": None,
        "variables": {},
        "skills_path": "",
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
        "timeout": 30,
    }
    ansible, _ = _run(
        monkeypatch,
        "gludd_skill",
        params,
        {"_status": 404, "detail": "skill not found"},
    )
    assert ansible.exited is None
    assert ansible.failed is not None
    assert ansible.failed["msg"] == "skill not found"


def _slurm_params(tmp_path: Path) -> dict[str, Any]:
    return {
        "engine": "vllm",
        "model_id": "org/model",
        "gpu_count": 1,
        "gpu_type": "a100",
        "port": 8000,
        "max_hours": 4,
        "mem_gb": 32,
        "partition": "gpu",
        "max_ctx": 32768,
        "artifact_dir": str(tmp_path),
        "poll_timeout": 30,
        "poll_interval": 1.0,
        "module_loads": [],
        "extra_args": [],
        "idempotency_key": None,
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
    }


def test_slurm_deploy_sends_stable_replay_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    response = {"_status": 200, "job_id": "job-42", "servable_url": "http://gpu:8000"}
    ansible, client = _run(
        monkeypatch,
        "gludd_slurm_deploy",
        _slurm_params(tmp_path),
        response,
    )

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["job_id"] == "job-42"
    assert client.posts[0][0] == "/admin/slurm/deploy"
    assert client.posts[0][1]["idempotency_key"].startswith("slurm-deploy:")


def test_slurm_check_mode_never_submits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ansible, client = _run(
        monkeypatch,
        "gludd_slurm_deploy",
        _slurm_params(tmp_path),
        {"_status": 200},
        check_mode=True,
    )
    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["changed"] is False
    assert client.posts == []


def test_slurm_http_failure_preserves_module_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ansible, client = _run(
        monkeypatch,
        "gludd_slurm_deploy",
        _slurm_params(tmp_path),
        {"_status": 503, "detail": "scheduler offline"},
    )

    assert ansible.exited is None
    assert ansible.failed is not None
    assert ansible.failed["msg"] == "submit failed: scheduler offline"
    assert ansible.failed["changed"] is False
    assert ansible.failed["engine"] == "vllm"
    assert client.posts[0][0] == "/admin/slurm/deploy"


def test_abtest_posts_absolute_roots_and_preserves_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "base"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    params = {
        "candidate_root": str(candidate),
        "baseline_root": str(baseline),
        "repo_root": ".",
        "module": "general_ludd.example.leaf",
        "expect_attr": None,
        "timeout": 2.0,
        "mem_limit_mb": 128,
        "verdict_path": None,
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
    }
    verdict = {"a": {"ok": True}, "b": {"ok": True}, "promote": True, "reason": "ok"}
    ansible, client = _run(
        monkeypatch,
        "gludd_abtest",
        params,
        {"_status": 200, "verdict": verdict, "promote": True},
    )

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["verdict"] == verdict
    assert Path(client.posts[0][1]["baseline_root"]).is_absolute()
    assert Path(client.posts[0][1]["candidate_root"]).is_absolute()


def test_abtest_http_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    params = {
        "candidate_root": str(tmp_path),
        "baseline_root": str(tmp_path),
        "repo_root": ".",
        "module": "general_ludd.example.leaf",
        "expect_attr": None,
        "timeout": 2.0,
        "mem_limit_mb": 128,
        "verdict_path": None,
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
    }
    ansible, _ = _run(
        monkeypatch,
        "gludd_abtest",
        params,
        {"_status": 504, "detail": "A/B comparison timed out"},
    )
    assert ansible.exited is None
    assert ansible.failed is not None
    assert ansible.failed["msg"] == "A/B comparison timed out"


def test_abtest_check_mode_and_artifact_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    params: dict[str, Any] = {
        "candidate_root": str(tmp_path),
        "baseline_root": None,
        "repo_root": str(tmp_path),
        "module": "general_ludd.example.leaf",
        "expect_attr": None,
        "timeout": 2.0,
        "mem_limit_mb": 128,
        "verdict_path": None,
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
    }
    ansible, client = _run(
        monkeypatch,
        "gludd_abtest",
        params,
        {"_status": 200},
        check_mode=True,
    )
    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["promote"] is False
    assert client.posts == []

    artifact = tmp_path / "verdict.json"
    params["verdict_path"] = str(artifact)
    ansible, _ = _run(
        monkeypatch,
        "gludd_abtest",
        params,
        {"_status": 200, "verdict": {"promote": True}, "promote": True},
    )
    assert ansible.failed is None
    assert artifact.is_file()

    params["verdict_path"] = str(tmp_path)
    ansible, _ = _run(
        monkeypatch,
        "gludd_abtest",
        params,
        {"_status": 200, "verdict": {}, "promote": False},
    )
    assert ansible.exited is None
    assert ansible.failed is not None
    assert "verdict artifact" in ansible.failed["msg"]
