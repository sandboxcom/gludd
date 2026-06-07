from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from general_ludd.events.bus import EventBus
from general_ludd.events.types import CustomEvent

logger = logging.getLogger(__name__)


@dataclass
class LocalServerConfig:
    engine: str = "vllm"
    model_path: str = ""
    model_name: str = ""
    host: str = "localhost"
    port: int = 8000
    gpu_layers: int = -1
    context_size: int = 4096
    extra_args: list[str] = field(default_factory=list)


@dataclass
class LocalServer:
    server_id: str
    config: LocalServerConfig
    process: Any | None = None
    status: str = "stopped"
    started_at: float = 0.0
    endpoint_url: str = ""
    pid: int | None = None

    @property
    def uptime_seconds(self) -> float:
        if self.status != "running":
            return 0.0
        return time.time() - self.started_at

    @property
    def is_running(self) -> bool:
        return self.status == "running" and self.process is not None


class LocalInferenceManager:
    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._servers: dict[str, LocalServer] = {}
        self._event_bus = event_bus
        self._next_id = 0

    def create_server(self, config: LocalServerConfig) -> LocalServer:
        server_id = f"local-{self._next_id}"
        self._next_id += 1
        endpoint_url = f"http://{config.host}:{config.port}/v1"
        server = LocalServer(
            server_id=server_id,
            config=config,
            endpoint_url=endpoint_url,
        )
        self._servers[server_id] = server
        logger.info(
            "Created local inference server config %s (%s, model=%s)",
            server_id,
            config.engine,
            config.model_path or config.model_name,
        )
        return server

    async def start_server(self, server_id: str) -> LocalServer:
        server = self._servers.get(server_id)
        if server is None:
            raise ValueError(f"Server '{server_id}' not found")
        if server.is_running:
            return server
        cmd = self._build_command(server.config)
        logger.info("Starting local inference server %s: %s", server_id, " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        server.process = process
        server.status = "running"
        server.started_at = time.time()
        server.pid = process.pid
        if self._event_bus:
            self._event_bus.publish(
                CustomEvent(
                    name="local_server_started",
                    payload={
                        "server_id": server_id,
                        "engine": server.config.engine,
                        "url": server.endpoint_url,
                    },
                )
            )
        return server

    async def stop_server(self, server_id: str) -> None:
        server = self._servers.get(server_id)
        if server is None or not server.is_running:
            return
        if server.process and server.process.returncode is None:
            server.process.terminate()
            try:
                await asyncio.wait_for(server.process.wait(), timeout=10.0)
            except TimeoutError:
                server.process.kill()
        server.status = "stopped"
        server.process = None
        server.pid = None
        logger.info("Stopped local inference server %s", server_id)

    async def stop_all(self) -> None:
        for sid in list(self._servers.keys()):
            await self.stop_server(sid)

    def list_servers(self, status: str | None = None) -> list[LocalServer]:
        servers = list(self._servers.values())
        if status:
            servers = [s for s in servers if s.status == status]
        return servers

    def get_server(self, server_id: str) -> LocalServer | None:
        return self._servers.get(server_id)

    def remove_server(self, server_id: str) -> None:
        server = self._servers.get(server_id)
        if server and server.is_running:
            raise RuntimeError(f"Cannot remove running server '{server_id}'. Stop it first.")
        self._servers.pop(server_id, None)

    def get_endpoints(self) -> dict[str, str]:
        return {
            sid: s.endpoint_url
            for sid, s in self._servers.items()
            if s.is_running
        }

    def _build_command(self, config: LocalServerConfig) -> list[str]:
        if config.engine == "vllm":
            model = config.model_name or config.model_path
            cmd: list[str] = ["vllm", "serve", model, "--host", config.host, "--port", str(config.port)]
            cmd.extend(config.extra_args)
            return cmd
        elif config.engine == "llamacpp":
            cmd = ["python3", "-m", "llama_cpp.server"]
            cmd.extend(["--model", config.model_path])
            cmd.extend(["--host", config.host])
            cmd.extend(["--port", str(config.port)])
            cmd.extend(["--n_gpu_layers", str(config.gpu_layers)])
            cmd.extend(["--n_ctx", str(config.context_size)])
            cmd.extend(config.extra_args)
            return cmd
        elif config.engine == "slurm":
            model = config.model_name or config.model_path
            cmd = ["sbatch"]
            cmd.extend(config.extra_args)
            cmd.extend([
                "--wrap",
                f"python3 -m llama_cpp.server "
                f"--model {model} "
                f"--host {config.host} "
                f"--port {config.port} "
                f"--n_gpu_layers {config.gpu_layers} "
                f"--n_ctx {config.context_size}",
            ])
            return cmd
        else:
            raise ValueError(f"Unsupported engine: {config.engine}")
