#!/usr/bin/env python3
"""Run game E2E against a hermetic, managed, or explicit local endpoint."""

from __future__ import annotations

import asyncio
import os
import shlex
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from tests.e2e._local_model_endpoint import EndpointLifecycle

from general_ludd.infra.local_inference import (
    LocalInferenceManager,
    LocalServerConfig,
)

_HERMETIC_MODEL_ID = "gludd-hermetic-game-e2e"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SNAKE_MODULE = "\n".join(
    [
        "import random",
        "",
        "class Snake:",
        "    def __init__(self, grid_w=20, grid_h=20):",
        "        self.grid_w = grid_w",
        "        self.grid_h = grid_h",
        "        self.restart()",
        "",
        "    def restart(self):",
        "        self.snake = [[self.grid_w // 2, self.grid_h // 2]]",
        "        self.direction = 'right'",
        "        self.score = 0",
        "        self.game_over = False",
        "        self.state = 'ready'",
        "        self.spawn_food()",
        "",
        "    def start(self):",
        "        self.state = 'playing'",
        "",
        "    def input(self, action):",
        "        if action in {'up', 'down', 'left', 'right'}:",
        "            self.direction = action",
        "",
        "    def tick(self):",
        "        return self.state == 'playing' and not self.game_over",
        "",
        "    def spawn_food(self):",
        "        self.food = [[0, 0]]",
        "",
        "    def render_state(self):",
        "        return {'grid_w': self.grid_w, 'grid_h': self.grid_h,",
        "                'snake': self.snake, 'food': self.food, 'score': self.score,",
        "                'game_over': self.game_over, 'length': len(self.snake)}",
    ]
)
_HERMETIC_RESPONSE = f"```python\n{_SNAKE_MODULE}\n```"


def _validated_external_url(value: str) -> str:
    """Return an explicit OpenAI loopback URL or reject it fail-closed."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in _LOOPBACK_HOSTS
        or parsed.path.rstrip("/") != "/v1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("external local-model endpoint must be an explicit loopback /v1 URL")
    return value


def _run_pytest(environment: dict[str, str]) -> int:
    """Run pytest in a clean child interpreter with an isolated base temp."""
    extra_args = shlex.split(os.environ.get("PYTEST_ARGS", ""))
    with tempfile.TemporaryDirectory(prefix=f"gludd-local-model-game-e2e-{os.getpid()}-") as base_temp:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/e2e/test_game_building_local.py",
                "-v",
                f"--basetemp={base_temp}",
                *extra_args,
            ],
            env=environment,
            check=False,
            timeout=180,
        )
    return result.returncode


def _find_free_loopback_port() -> int:
    """Reserve and release an ephemeral IPv4 loopback port for one owned server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    """Resolve the requested mode, run the game suite, and always clean up."""
    mode = os.environ.get("LOCAL_MODEL_E2E_MODE", "hermetic").strip().lower()
    game = os.environ.get("LOCAL_MODEL_GAME", "snake").strip().lower()
    endpoint: EndpointLifecycle | None = None
    manager: LocalInferenceManager | None = None
    manager_loop: asyncio.AbstractEventLoop | None = None
    try:
        if mode == "hermetic":
            if game != "snake":
                raise ValueError("hermetic local-model E2E supports LOCAL_MODEL_GAME=snake")
            model_id = (
                os.environ.get("LOCAL_MODEL_NAME", _HERMETIC_MODEL_ID).strip()
                or _HERMETIC_MODEL_ID
            )
            endpoint = EndpointLifecycle(
                model_id=model_id,
                chat_content=_HERMETIC_RESPONSE,
                namespace=f"game-{os.getpid()}",
            )
            endpoint.start()
            base_url = endpoint.base_url
            api_key = os.environ.get("LOCAL_MODEL_KEY", "").strip() or "local-hermetic"
            print(f"LOCAL_MODEL_E2E_READY mode=hermetic endpoint={base_url}", flush=True)
        elif mode == "managed":
            model_path = Path(os.environ.get("LOCAL_MODEL_PATH", ""))
            if not model_path.is_file() or not os.access(model_path, os.R_OK):
                raise FileNotFoundError(
                    "managed local-model E2E requires a readable LOCAL_MODEL_PATH"
                )
            model_id = os.environ.get("LOCAL_MODEL_NAME", "").strip() or model_path.stem
            manager = LocalInferenceManager()
            manager_loop = asyncio.new_event_loop()
            server = manager.create_server(
                LocalServerConfig(
                    engine="llamacpp",
                    model_path=str(model_path),
                    model_name=model_id,
                    host="127.0.0.1",
                    port=_find_free_loopback_port(),
                    gpu_layers=0,
                    context_size=2048,
                    startup_timeout=120,
                )
            )
            manager_loop.run_until_complete(manager.start_server(server.server_id))
            base_url = server.endpoint_url
            api_key = os.environ.get("LOCAL_MODEL_KEY", "").strip() or "local-managed"
            print(f"LOCAL_MODEL_E2E_READY mode=managed endpoint={base_url}", flush=True)
        elif mode == "external":
            base_url = _validated_external_url(os.environ.get("LOCAL_MODEL_BASE_URL", ""))
            model_id = os.environ.get("LOCAL_MODEL_NAME", "").strip()
            if not model_id:
                raise ValueError("external local-model E2E requires LOCAL_MODEL_NAME")
            api_key = os.environ.get("LOCAL_MODEL_KEY", "")
            print(f"LOCAL_MODEL_E2E_READY mode=external endpoint={base_url}", flush=True)
        else:
            raise ValueError(
                "LOCAL_MODEL_E2E_MODE must be hermetic, managed, or external"
            )

        environment = os.environ.copy()
        environment["LOCAL_MODEL_BASE_URL"] = base_url
        environment["LOCAL_MODEL_NAME"] = model_id
        environment["LOCAL_MODEL_KEY"] = api_key
        environment["LOCAL_MODEL_GAME"] = game
        return _run_pytest(environment)
    finally:
        if manager_loop is not None:
            try:
                if manager is not None:
                    manager_loop.run_until_complete(manager.stop_all())
                    print("LOCAL_MODEL_E2E_STOPPED mode=managed", flush=True)
            finally:
                manager_loop.close()
        if endpoint is not None:
            endpoint.stop()
            print("LOCAL_MODEL_E2E_STOPPED mode=hermetic", flush=True)


if __name__ == "__main__":
    sys.exit(main())
