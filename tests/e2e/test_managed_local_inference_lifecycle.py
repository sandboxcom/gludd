"""Opt-in acceptance for Gludd-owned llama.cpp game-E2E lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from scripts import run_local_model_game_e2e as runner

from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServer,
    LocalServerConfig,
)

_LIVE = os.environ.get("GLUDD_MANAGED_LOCAL_MODEL_E2E") == "1"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not _LIVE,
        reason="set GLUDD_MANAGED_LOCAL_MODEL_E2E=1 with LOCAL_MODEL_PATH",
    ),
]


class _TestBodyFailure(RuntimeError):
    """Controlled failure raised after the real server becomes ready."""


@pytest.mark.parametrize("body_fails", [False, True], ids=["success", "test-failure"])
def test_managed_mode_starts_real_llamacpp_and_reaps_it(
    monkeypatch: pytest.MonkeyPatch,
    body_fails: bool,
) -> None:
    """Managed mode owns a random-port server and cleans every test exit path."""
    model_path = Path(os.environ.get("LOCAL_MODEL_PATH", ""))
    assert model_path.is_file(), f"LOCAL_MODEL_PATH is not a file: {model_path}"
    managers: list[LocalInferenceManager] = []
    servers: list[LocalServer] = []
    owned_processes: list[object] = []
    stderr_paths: list[Path] = []
    child_urls: list[str] = []

    class RecordingManager(LocalInferenceManager):
        def __init__(self) -> None:
            super().__init__()
            managers.append(self)

        def create_server(self, config: LocalServerConfig) -> LocalServer:
            server = super().create_server(config)
            servers.append(server)
            return server

        async def stop_server(self, server_id: str) -> None:
            server = self.get_server(server_id)
            if server is not None and server.process is not None:
                owned_processes.append(server.process)
            if server is not None and server.stderr_path is not None:
                stderr_paths.append(Path(server.stderr_path))
            await super().stop_server(server_id)

    def probe_real_server(environment: dict[str, str]) -> int:
        base_url = environment["LOCAL_MODEL_BASE_URL"]
        child_urls.append(base_url)
        response = httpx.get(
            f"{base_url}/models",
            timeout=10,
            trust_env=False,
        )
        assert response.status_code == 200
        if body_fails:
            raise _TestBodyFailure("controlled managed-E2E body failure")
        return 0

    monkeypatch.setenv("LOCAL_MODEL_E2E_MODE", "managed")
    monkeypatch.setenv("LOCAL_MODEL_PATH", str(model_path))
    monkeypatch.setenv("LOCAL_MODEL_NAME", "gludd-managed-lifecycle")
    monkeypatch.setenv("LOCAL_MODEL_GAME", "snake")
    monkeypatch.setattr(runner, "LocalInferenceManager", RecordingManager)
    monkeypatch.setattr(runner, "_run_pytest", probe_real_server)

    if body_fails:
        with pytest.raises(_TestBodyFailure, match="controlled managed-E2E"):
            runner.main()
    else:
        assert runner.main() == 0

    assert len(managers) == len(servers) == len(owned_processes) == 1
    parsed = urlsplit(child_urls[0])
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port not in {None, 9999}
    assert owned_processes[0].returncode is not None
    assert managers[0].list_servers() == []
    assert stderr_paths and not stderr_paths[0].exists()


def test_managed_mode_reaps_real_llamacpp_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A real llama.cpp parse failure must leave no child or stderr artifact."""
    invalid_model = tmp_path / "invalid-model.gguf"
    invalid_model.write_bytes(b"not a gguf model")
    managers: list[LocalInferenceManager] = []
    owned_processes: list[object] = []
    stderr_paths: list[Path] = []

    class RecordingManager(LocalInferenceManager):
        def __init__(self) -> None:
            super().__init__()
            managers.append(self)

        async def stop_server(self, server_id: str) -> None:
            server = self.get_server(server_id)
            if server is not None and server.process is not None:
                owned_processes.append(server.process)
            if server is not None and server.stderr_path is not None:
                stderr_paths.append(Path(server.stderr_path))
            await super().stop_server(server_id)

    monkeypatch.setenv("LOCAL_MODEL_E2E_MODE", "managed")
    monkeypatch.setenv("LOCAL_MODEL_PATH", str(invalid_model))
    monkeypatch.setenv("LOCAL_MODEL_NAME", "gludd-invalid-lifecycle")
    monkeypatch.setenv("LOCAL_MODEL_GAME", "snake")
    monkeypatch.setattr(runner, "LocalInferenceManager", RecordingManager)
    monkeypatch.setattr(
        runner,
        "_run_pytest",
        lambda _environment: pytest.fail("tests must not run after startup failure"),
    )

    with pytest.raises(RuntimeError, match="before becoming ready"):
        runner.main()

    assert len(managers) == len(owned_processes) == 1
    assert owned_processes[0].returncode is not None
    assert managers[0].list_servers() == []
    assert stderr_paths and not stderr_paths[0].exists()
