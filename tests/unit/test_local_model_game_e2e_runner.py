"""Behavioral tests for the isolated local-model game E2E runner."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from scripts import run_local_model_game_e2e as runner

from general_ludd.infra.local_inference import LocalServerConfig
from tests.e2e._local_model_endpoint import EndpointLifecycle


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9999/v1",
        "http://localhost:11434/v1",
        "http://[::1]:8080/v1",
    ],
)
def test_external_mode_accepts_only_explicit_loopback_urls(url: str) -> None:
    assert runner._validated_external_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://models.example.com/v1",
        "file:///tmp/model",
        "http://0.0.0.0:9999/v1",
        "http://127.0.0.1:9999/not-v1",
    ],
)
def test_external_mode_rejects_ssrf_and_ambiguous_urls(url: str) -> None:
    with pytest.raises(ValueError):
        runner._validated_external_url(url)


def test_hermetic_endpoint_owns_random_port_and_cleanup() -> None:
    endpoint = EndpointLifecycle(
        model_id="gludd-hermetic-game-e2e",
        chat_content="class Snake:\n    pass\n",
        namespace=f"unit-{os.getpid()}",
    )
    endpoint.start()
    try:
        assert endpoint.base_url.startswith("http://127.0.0.1:")
        assert ":9999/" not in endpoint.base_url
        response = httpx.post(
            f"{endpoint.base_url}/chat/completions",
            json={
                "model": "gludd-hermetic-game-e2e",
                "messages": [{"role": "user", "content": "build snake"}],
            },
            timeout=1.0,
        )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"].startswith("class Snake")
        assert endpoint.thread.name.startswith("gludd-local-model-e2e-unit-")
    finally:
        endpoint.stop()
    endpoint.stop()
    assert not endpoint.thread.is_alive()


def test_hermetic_main_owns_endpoint_and_passes_child_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    child_environment: dict[str, str] = {}

    class FakeEndpoint:
        base_url = "http://127.0.0.1:43210/v1"

        def __init__(self, **options: object) -> None:
            assert options["model_id"] == "hermetic-model"
            assert str(options["namespace"]).startswith("game-")

        def start(self) -> None:
            events.append("started")

        def stop(self) -> None:
            events.append("stopped")

    def fake_run(environment: dict[str, str]) -> int:
        child_environment.update(environment)
        return 0

    monkeypatch.setenv("LOCAL_MODEL_E2E_MODE", "hermetic")
    monkeypatch.setenv("LOCAL_MODEL_NAME", "hermetic-model")
    monkeypatch.setenv("LOCAL_MODEL_GAME", "snake")
    monkeypatch.delenv("LOCAL_MODEL_KEY", raising=False)
    monkeypatch.setattr(runner, "EndpointLifecycle", FakeEndpoint)
    monkeypatch.setattr(runner, "_run_pytest", fake_run)

    assert runner.main() == 0
    assert events == ["started", "stopped"]
    assert child_environment["LOCAL_MODEL_BASE_URL"] == FakeEndpoint.base_url
    assert child_environment["LOCAL_MODEL_KEY"] == "local-hermetic"


def test_external_main_uses_explicit_endpoint_without_owning_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_environment: dict[str, str] = {}

    def fake_run(environment: dict[str, str]) -> int:
        child_environment.update(environment)
        return 7

    monkeypatch.setenv("LOCAL_MODEL_E2E_MODE", "external")
    monkeypatch.setenv("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("LOCAL_MODEL_NAME", "qwen2.5:0.5b")
    monkeypatch.setenv("LOCAL_MODEL_KEY", "local-only")
    monkeypatch.setenv("LOCAL_MODEL_GAME", "snake")
    monkeypatch.setattr(runner, "_run_pytest", fake_run)
    monkeypatch.setattr(
        runner,
        "LocalInferenceManager",
        lambda: pytest.fail("external mode must not construct an owned manager"),
        raising=False,
    )

    assert runner.main() == 7
    assert child_environment["LOCAL_MODEL_BASE_URL"] == "http://127.0.0.1:9999/v1"
    assert child_environment["LOCAL_MODEL_NAME"] == "qwen2.5:0.5b"


def _configure_managed_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    events: list[str],
    *,
    startup_fails: bool = False,
) -> None:
    """Install a deterministic manager double for managed runner tests."""
    model_path = tmp_path / "managed-model.gguf"
    model_path.write_bytes(b"gguf")

    class FakeManager:
        def __init__(self) -> None:
            self.server = SimpleNamespace(
                server_id="local-0",
                endpoint_url="http://127.0.0.1:43211/v1",
            )

        def create_server(self, config: LocalServerConfig) -> object:
            assert config.host == "127.0.0.1"
            assert config.port == 43211
            assert config.model_path == str(model_path)
            events.append("created")
            return self.server

        async def start_server(self, server_id: str) -> object:
            assert server_id == self.server.server_id
            events.append("started")
            if startup_fails:
                raise RuntimeError("managed startup failed")
            return self.server

        async def stop_all(self) -> None:
            events.append("stopped")

    monkeypatch.setenv("LOCAL_MODEL_E2E_MODE", "managed")
    monkeypatch.setenv("LOCAL_MODEL_PATH", str(model_path))
    monkeypatch.setenv("LOCAL_MODEL_NAME", "managed-model")
    monkeypatch.setenv("LOCAL_MODEL_GAME", "snake")
    monkeypatch.setattr(runner, "LocalInferenceManager", FakeManager, raising=False)
    monkeypatch.setattr(runner, "_find_free_loopback_port", lambda: 43211, raising=False)


@pytest.mark.parametrize("test_fails", [False, True], ids=["success", "test-failure"])
def test_managed_main_owns_manager_and_always_reaps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    test_fails: bool,
) -> None:
    events: list[str] = []
    environment: dict[str, str] = {}
    _configure_managed_mode(monkeypatch, tmp_path, events)

    def fake_run(child_environment: dict[str, str]) -> int:
        environment.update(child_environment)
        if test_fails:
            raise RuntimeError("managed test failed")
        return 0

    monkeypatch.setattr(runner, "_run_pytest", fake_run)

    if test_fails:
        with pytest.raises(RuntimeError, match="managed test failed"):
            runner.main()
    else:
        assert runner.main() == 0

    assert events == ["created", "started", "stopped"]
    assert environment["LOCAL_MODEL_BASE_URL"] == "http://127.0.0.1:43211/v1"


def test_managed_main_reaps_after_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    _configure_managed_mode(monkeypatch, tmp_path, events, startup_fails=True)
    monkeypatch.setattr(
        runner,
        "_run_pytest",
        lambda _environment: pytest.fail("tests must not run after startup failure"),
    )

    with pytest.raises(RuntimeError, match="managed startup failed"):
        runner.main()

    assert events == ["created", "started", "stopped"]


def test_runner_rejects_unknown_mode_and_non_snake_hermetic_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_MODEL_E2E_MODE", "unknown")
    with pytest.raises(ValueError, match="must be hermetic, managed, or external"):
        runner.main()

    monkeypatch.setenv("LOCAL_MODEL_E2E_MODE", "hermetic")
    monkeypatch.setenv("LOCAL_MODEL_GAME", "tetris")
    with pytest.raises(ValueError, match="supports LOCAL_MODEL_GAME=snake"):
        runner.main()
