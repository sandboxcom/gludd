from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from general_ludd.events.bus import EventBus
from general_ludd.events.types import CustomEvent
from general_ludd.infra.slurm import SlurmAdapter

logger = logging.getLogger(__name__)

# Characters that the shell treats specially. Any of these in a value that
# is interpolated into an argv (or, worse, into the slurm ``--wrap`` shell
# string) is rejected. This is deliberately a denylist of metacharacters
# plus whitespace/control chars, applied on top of structural checks.
_SHELL_METACHARS = set(";&|<>$`\\\"'()[]{}*?!#~\n\r\t ")

# A hostname/IP: dotted IPv4, or a DNS label sequence. We keep this strict
# so a host can never carry shell metacharacters or whitespace.
_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9_]([A-Za-z0-9_.-]*[A-Za-z0-9_])?$")


def _has_shell_metachars(value: str) -> bool:
    return any(ch in _SHELL_METACHARS for ch in value)


def _validate_model(model: str) -> str:
    """Validate a model identifier/path destined for an argv position.

    Rejects empties, shell metacharacters, and leading-dash values (which a
    downstream CLI would parse as a flag rather than a positional argument —
    classic argv injection).
    """
    if not isinstance(model, str) or not model:
        raise ValueError("model must be a non-empty string")
    if model.startswith("-"):
        raise ValueError(f"model may not start with '-' (argv flag injection): {model!r}")
    if _has_shell_metachars(model):
        raise ValueError(f"model contains forbidden characters: {model!r}")
    return model


# Hosts that are safe to bind to without network exposure.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _validate_host(host: str, *, allow_nonloopback: bool = False) -> str:
    """Validate a host value destined for an argv position.

    By default only loopback addresses are accepted (localhost / 127.0.0.1 /
    ::1) because the inference API has no authentication layer and binding to
    0.0.0.0 / :: would expose it on every NIC.  Pass ``allow_nonloopback=True``
    for Slurm/cluster paths where the server intentionally binds on a cluster
    interface.
    """
    if not isinstance(host, str) or not host:
        raise ValueError("host must be a non-empty string")
    if _has_shell_metachars(host) or not _HOST_RE.match(host):
        raise ValueError(f"invalid host: {host!r}")
    if not allow_nonloopback and host not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"host {host!r} is not a loopback address; binding the unauthenticated "
            "inference API on a non-loopback interface is rejected by default. "
            "Use allow_nonloopback=True (Slurm/cluster mode) to override."
        )
    return host


def _validate_port(port: Any) -> int:
    # Reject bool explicitly (bool is a subclass of int) and any non-int so a
    # string like "8000; rm -rf" can never flow into the argv.
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError(f"port must be an int, got {type(port).__name__}")
    if not (1 <= port <= 65535):
        raise ValueError(f"port out of range (1-65535): {port}")
    return port


def _validate_extra_args(extra_args: list[str]) -> list[str]:
    validated: list[str] = []
    for arg in extra_args:
        if not isinstance(arg, str):
            raise ValueError(f"extra_args entries must be strings, got {type(arg).__name__}")
        if _has_shell_metachars(arg):
            raise ValueError(f"extra_args entry contains forbidden characters: {arg!r}")
        validated.append(arg)
    return validated


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
    # Seconds to wait for the /health endpoint to return 200 after launch.
    # Set to 0 to skip the readiness probe (not recommended in production).
    startup_timeout: float = 120.0
    # Allow binding to non-loopback interfaces (e.g. cluster nodes via Slurm).
    # Defaults to False; must be explicitly set True for Slurm/cluster engines.
    allow_nonloopback: bool = False


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

        if server.config.engine == "slurm":
            return await self._start_slurm_server(server)

        cmd = self._build_command(server.config)
        logger.info("Starting local inference server %s: %s", server_id, " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Place the child in its own process group so that when we stop the
            # server we can send SIGTERM/SIGKILL to the *entire* process group,
            # catching vllm/llama.cpp worker grandchildren that would otherwise
            # be orphaned if we only signal the direct child.
            start_new_session=True,
        )
        server.process = process
        server.started_at = time.time()
        server.pid = process.pid

        # Wait for the server to become ready before advertising it as "running".
        # This prevents callers from sending requests to a not-yet-initialised
        # model, and surfaces launch failures (OOM, bad model path, etc.) early.
        await self._wait_for_ready(server)

        server.status = "running"
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

    async def _wait_for_ready(self, server: LocalServer) -> None:
        """Poll the /health endpoint until the server is ready or times out.

        Sets ``server.status = "error"`` and raises ``RuntimeError`` if:
        - the subprocess exits before becoming healthy (crash / bad model path),
        - the startup_timeout expires before /health returns 200.

        If ``startup_timeout <= 0`` the probe is skipped (test/dev shortcut).
        """
        if server.config.startup_timeout <= 0:
            return

        health_url = f"http://{server.config.host}:{server.config.port}/health"
        deadline = time.time() + server.config.startup_timeout
        poll_interval = 2.0

        while time.time() < deadline:
            # Check whether the process exited prematurely.
            if server.process is not None and server.process.returncode is not None:
                server.status = "error"
                raise RuntimeError(
                    f"Local inference server {server.server_id!r} exited "
                    f"(returncode={server.process.returncode}) before becoming ready."
                )

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(health_url)
                if resp.status_code == 200:
                    return  # ready
            except (httpx.TransportError, httpx.TimeoutException):
                pass  # server not yet accepting connections

            await asyncio.sleep(poll_interval)

        # Timed out — mark error and raise.
        server.status = "error"
        raise RuntimeError(
            f"Local inference server {server.server_id!r} did not become ready "
            f"within {server.config.startup_timeout}s (health URL: {health_url})."
        )

    async def _start_slurm_server(self, server: LocalServer) -> LocalServer:
        adapter = SlurmAdapter()
        # Validate before interpolating into the shell command string.
        model = _validate_model(server.config.model_name or server.config.model_path)
        # Slurm jobs run on cluster compute nodes that may legitimately bind on
        # non-loopback interfaces; allow_nonloopback reflects the config flag.
        host = _validate_host(server.config.host, allow_nonloopback=server.config.allow_nonloopback)
        port = _validate_port(server.config.port)
        extra_args = _validate_extra_args(list(server.config.extra_args))
        if isinstance(server.config.gpu_layers, bool) or not isinstance(server.config.gpu_layers, int):
            raise ValueError("gpu_layers must be an int")
        if isinstance(server.config.context_size, bool) or not isinstance(server.config.context_size, int):
            raise ValueError("context_size must be an int")
        command = (
            f"python3 -m llama_cpp.server "
            f"--model {model} "
            f"--host {host} "
            f"--port {port} "
            f"--n_gpu_layers {server.config.gpu_layers} "
            f"--n_ctx {server.config.context_size}"
        )
        loop = asyncio.get_running_loop()
        job_id = await loop.run_in_executor(
            None,
            lambda: adapter.submit(
                command=command,
                job_name=f"gludd-{server.server_id}",
                extra_args=extra_args if extra_args else None,
            ),
        )
        server.status = "submitted"
        server.started_at = time.time()
        logger.info(
            "Submitted Slurm job %s for server %s",
            job_id,
            server.server_id,
        )
        if self._event_bus:
            self._event_bus.publish(
                CustomEvent(
                    name="local_server_submitted_slurm",
                    payload={
                        "server_id": server.server_id,
                        "engine": "slurm",
                        "slurm_job_id": job_id,
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
            pid = server.process.pid
            # Signal the entire process group so that vllm/llama.cpp worker
            # grandchildren (which were placed in their own session by
            # start_new_session=True) receive SIGTERM and don't become orphans.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            try:
                await asyncio.wait_for(server.process.wait(), timeout=10.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
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
        # Validate every config value that is interpolated into the argv (or,
        # for slurm, into the --wrap shell string) before building the command.
        host = _validate_host(config.host, allow_nonloopback=config.allow_nonloopback)
        port = _validate_port(config.port)
        extra_args = _validate_extra_args(list(config.extra_args))
        # gpu_layers / context_size are typed ints; guard against tampering.
        if isinstance(config.gpu_layers, bool) or not isinstance(config.gpu_layers, int):
            raise ValueError("gpu_layers must be an int")
        if isinstance(config.context_size, bool) or not isinstance(config.context_size, int):
            raise ValueError("context_size must be an int")
        gpu_layers = config.gpu_layers
        context_size = config.context_size

        if config.engine == "vllm":
            model = _validate_model(config.model_name or config.model_path)
            cmd: list[str] = ["vllm", "serve", model, "--host", host, "--port", str(port)]
            cmd.extend(extra_args)
            return cmd
        elif config.engine == "llamacpp":
            model = _validate_model(config.model_path)
            cmd = ["python3", "-m", "llama_cpp.server"]
            cmd.extend(["--model", model])
            cmd.extend(["--host", host])
            cmd.extend(["--port", str(port)])
            cmd.extend(["--n_gpu_layers", str(gpu_layers)])
            cmd.extend(["--n_ctx", str(context_size)])
            cmd.extend(extra_args)
            return cmd
        elif config.engine == "slurm":
            # All values below are interpolated into a SHELL string for the
            # sbatch --wrap argument, so they MUST be validated first.
            model = _validate_model(config.model_name or config.model_path)
            command = (
                f"python3 -m llama_cpp.server "
                f"--model {model} "
                f"--host {host} "
                f"--port {port} "
                f"--n_gpu_layers {gpu_layers} "
                f"--n_ctx {context_size}"
            )
            return ["sbatch", *extra_args, "--wrap", command]
        else:
            raise ValueError(f"Unsupported engine: {config.engine}")
