"""Local inference server lifecycle manager (start/stop/list/health)."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import re
import signal
import tempfile
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from general_ludd.events.bus import EventBus
from general_ludd.events.types import (
    CustomEvent,
    ModelDeployStartedEvent,
    ModelErrorEvent,
    ModelReadyEvent,
)
from general_ludd.infra.slurm import SlurmAdapter

if TYPE_CHECKING:
    from general_ludd.ansible.runner import AnsibleRunnerAdapter

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


def _readiness_path(engine: str) -> str:
    """Return the stable readiness endpoint exposed by an inference engine."""
    if engine == "llamacpp":
        return "/v1/models"
    return "/health"


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
    if _has_shell_metachars(host) or (not _HOST_RE.match(host) and host not in _LOOPBACK_HOSTS):
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
    """Configuration for one local inference server instance."""

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
    """Runtime state for a local inference server managed by the daemon."""

    server_id: str
    config: LocalServerConfig
    process: Any | None = None
    status: str = "stopped"
    started_at: float = 0.0
    endpoint_url: str = ""
    pid: int | None = None
    stderr_path: str | None = None

    @property
    def uptime_seconds(self) -> float:
        """Return seconds since the server started (0 when stopped)."""
        if self.status != "running":
            return 0.0
        return time.time() - self.started_at

    @property
    def is_running(self) -> bool:
        """Return whether the server process is live."""
        return self.status == "running" and self.process is not None


class LocalInferenceManager:
    """Owns the local inference server lifecycle (create/start/stop/list)."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        ansible_adapter: AnsibleRunnerAdapter | None = None,
    ) -> None:
        """Initialize the manager with an optional event bus and adapter."""
        self._servers: dict[str, LocalServer] = {}
        self._event_bus = event_bus
        self._ansible_adapter = ansible_adapter
        self._next_id = 0

    @property
    def ansible_adapter(self) -> AnsibleRunnerAdapter | None:
        """Return the wired ansible adapter, if any."""
        return self._ansible_adapter

    @ansible_adapter.setter
    def ansible_adapter(self, adapter: AnsibleRunnerAdapter | None) -> None:
        self._ansible_adapter = adapter

    def create_server(self, config: LocalServerConfig) -> LocalServer:
        """Register a new local server and return its runtime record."""
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
        """Start a registered server (no-op when already running)."""
        server = self._servers.get(server_id)
        if server is None:
            raise ValueError(f"Server '{server_id}' not found")
        if server.is_running:
            return server

        if server.config.engine == "slurm":
            return await self._start_slurm_server(server)

        config = server.config
        model_path = _validate_model(config.model_name or config.model_path)
        engine = config.engine
        host = _validate_host(config.host, allow_nonloopback=config.allow_nonloopback)
        port = _validate_port(config.port)

        self._emit(
            ModelDeployStartedEvent(
                server_id=server_id,
                engine=engine,
                model_path=model_path,
                host=host,
                port=port,
            )
        )

        adapter = self._ansible_adapter
        if adapter is not None and hasattr(adapter, "run_playbook"):
            return await self._start_via_ansible(server, model_path, host, port, engine, config, adapter)

        return await self._start_via_subprocess(server, config)

    async def _start_via_ansible(
        self,
        server: LocalServer,
        model_path: str,
        host: str,
        port: int,
        engine: str,
        config: LocalServerConfig,
        adapter: Any,
    ) -> LocalServer:
        extravars: dict[str, Any] = {
            "engine": engine,
            "model_path": model_path,
            "host": host,
            "port": port,
            "gpu_layers": config.gpu_layers,
            "context_size": config.context_size,
            "extra_args": list(config.extra_args),
            "startup_timeout": config.startup_timeout,
            "server_id": server.server_id,
        }
        result = await asyncio.to_thread(
            adapter.run_playbook,
            playbook_name="local_model_serve.yml",
            extravars=extravars,
            timeout=config.startup_timeout + 30,
        )
        if result.get("status") != "success" and result.get("rc") != 0:
            error_msg = result.get("error") or result.get("msg") or str(result)
            server.status = "error"
            self._emit(
                ModelErrorEvent(
                    server_id=server.server_id,
                    engine=engine,
                    error=f"ansible playbook failed: {error_msg}",
                )
            )
            raise RuntimeError(f"Local inference server {server.server_id!r} ansible playbook failed: {error_msg}")

        facts = result.get("facts") or result.get("ansible_facts") or {}
        gludd_server = facts.get("gludd_local_server", {})
        if gludd_server.get("status") == "running" and gludd_server.get("pid"):
            server.status = "running"
            server.started_at = time.time()
            try:
                server.pid = int(gludd_server["pid"])
            except (ValueError, TypeError):
                server.pid = None
            self._emit(
                ModelReadyEvent(
                    server_id=server.server_id,
                    engine=engine,
                    endpoint_url=server.endpoint_url,
                    pid=server.pid,
                )
            )
            logger.info("Ansible-deployed local inference server %s (pid=%s)", server.server_id, server.pid)
        else:
            server.status = "error"
            self._emit(
                ModelErrorEvent(
                    server_id=server.server_id,
                    engine=engine,
                    error=f"playbook completed but server not running: {gludd_server}",
                )
            )
            raise RuntimeError(
                f"Local inference server {server.server_id!r} playbook completed but server not reported as running"
            )
        return server

    async def _start_via_subprocess(
        self,
        server: LocalServer,
        config: LocalServerConfig,
    ) -> LocalServer:
        cmd = self._build_command(config)
        logger.info("Starting local inference server %s: %s", server.server_id, " ".join(cmd))
        with tempfile.NamedTemporaryFile(mode="w+b", delete=False, prefix="gludd-llama-stderr-") as stderr_file:
            server.stderr_path = stderr_file.name
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                # Direct file redirection keeps diagnostics without a bounded
                # PIPE that can fill and deadlock long-running inference after
                # readiness polling has completed. The child owns a duplicate
                # descriptor for its full lifetime.
                stderr=stderr_file,
                start_new_session=True,
            )
            server.process = process
            server.started_at = time.time()
            server.pid = process.pid
            try:
                await self._wait_for_ready(server)
            except BaseException:
                # The manager owns a subprocess as soon as creation succeeds.
                # Readiness errors and cancellation must therefore traverse the
                # same reaping and stderr-removal path as an explicit shutdown.
                await self.stop_server(server.server_id)
                # Preserve the failed-start outcome for callers retaining the
                # returned record, even though the owned resources are retired.
                server.status = "error"
                raise

        server.status = "running"
        self._emit(
            ModelReadyEvent(
                server_id=server.server_id,
                engine=config.engine,
                endpoint_url=server.endpoint_url,
                pid=server.pid,
            )
        )
        return server

    def _emit(self, event: Any) -> None:
        if self._event_bus:
            self._event_bus.publish(event)

    async def _wait_for_ready(self, server: LocalServer) -> None:
        """Poll the /health endpoint until the server is ready or times out.

        Sets ``server.status = "error"`` and raises ``RuntimeError`` if:
        - the subprocess exits before becoming healthy (crash / bad model path),
        - the startup_timeout expires before /health returns 200.

        On any failure, captures and includes the process stderr in the error
        message so the caller can diagnose the root cause without guessing.

        If ``startup_timeout <= 0`` the probe is skipped (test/dev shortcut).
        """
        if server.config.startup_timeout <= 0:
            return

        readiness_path = _readiness_path(server.config.engine)
        health_url = f"http://{server.config.host}:{server.config.port}{readiness_path}"
        deadline = time.time() + server.config.startup_timeout
        poll_interval = 2.0

        async def _read_stderr(stderr_path: str | None) -> str:
            if stderr_path is None:
                return "(stderr not captured)"
            try:

                def _read_file(path: str) -> bytes:
                    with open(path, "rb") as f:
                        return f.read()

                loop = asyncio.get_running_loop()
                raw = await loop.run_in_executor(None, _read_file, stderr_path)
                return raw.decode(errors="replace")[-4000:]
            except Exception:
                return "(could not read stderr)"

        while time.time() < deadline:
            if server.process is not None and server.process.returncode is not None:
                server.status = "error"
                stderr_tail = await _read_stderr(server.stderr_path)
                logger.error(
                    "Server %s crashed (exit=%d). stderr tail:\n%s",
                    server.server_id,
                    server.process.returncode,
                    stderr_tail,
                )
                raise RuntimeError(
                    f"Local inference server {server.server_id!r} exited "
                    f"(returncode={server.process.returncode}) before becoming ready.\n"
                    f"stderr tail:\n{stderr_tail}"
                )

            try:
                async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                    resp = await client.get(health_url)
                if resp.status_code == 200:
                    return
            except (httpx.TransportError, httpx.TimeoutException):
                pass

            await asyncio.sleep(poll_interval)

        server.status = "error"
        stderr_tail = ""
        if server.stderr_path is not None:
            stderr_tail = await _read_stderr(server.stderr_path)
        msg = (
            f"Local inference server {server.server_id!r} did not become ready "
            f"within {server.config.startup_timeout}s (health URL: {health_url})."
        )
        if stderr_tail:
            msg += f"\nstderr tail:\n{stderr_tail}"
        raise RuntimeError(msg)

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
        """Stop a registered server and retire it (no-op when unknown).

        The shutdown ROUTE checks existence first and maps unknown IDs to
        404; the manager itself stays a no-op so callers that stop idempotently
        (unit-pinned contract) never raise.
        """
        server = self._servers.get(server_id)
        if server is None:
            return

        adapter = self._ansible_adapter
        process_wait_attempted = False

        if server.process and server.process.returncode is None:
            pid = server.process.pid
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            try:
                process_wait_attempted = True
                await asyncio.wait_for(server.process.wait(), timeout=10.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(server.process.wait(), timeout=5.0)
        elif adapter is not None and hasattr(adapter, "run_playbook"):
            extravars: dict[str, object] = {
                "server_id": server_id,
                "server_pid": server.pid,
            }
            try:
                await asyncio.to_thread(
                    adapter.run_playbook,
                    playbook_name="local_model_stop.yml",
                    extravars=extravars,
                    timeout=30,
                )
            except Exception as exc:
                logger.warning(
                    "ansible stop playbook failed for %s: %s; falling back to subprocess",
                    server_id,
                    exc,
                )
                if server.pid is not None:
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(server.pid, signal.SIGKILL)
        elif server.pid is not None and server.status != "stopped":
            with contextlib.suppress(ProcessLookupError):
                os.kill(server.pid, signal.SIGTERM)

        # Reap an owned subprocess after every signal path, including the
        # adapter fallback's SIGKILL, without double-waiting a process that
        # already completed during the graceful shutdown attempt.
        if server.process is not None and not process_wait_attempted:
            with contextlib.suppress(TimeoutError, ProcessLookupError):
                await asyncio.wait_for(server.process.wait(), timeout=5.0)

        server.status = "stopped"
        server.process = None
        server.pid = None
        if server.stderr_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(server.stderr_path)
            server.stderr_path = None
        logger.info("Stopped local inference server %s", server_id)
        # A stopped server is fully retired: drop the entry so a second
        # shutdown raises KeyError (route maps it to 404 — pinned contract).
        self._servers.pop(server_id, None)

    async def stop_all(self) -> None:
        """Stop every registered server."""
        for sid in list(self._servers.keys()):
            await self.stop_server(sid)

    def list_servers(self, status: str | None = None) -> list[LocalServer]:
        """List registered servers, optionally filtered by status."""
        servers = list(self._servers.values())
        if status:
            servers = [s for s in servers if s.status == status]
        return servers

    def get_server(self, server_id: str) -> LocalServer | None:
        """Return a registered server by id, or None."""
        return self._servers.get(server_id)

    def remove_server(self, server_id: str) -> None:
        """Remove a stopped server's record (raises while running)."""
        server = self._servers.get(server_id)
        if server and server.is_running:
            raise RuntimeError(f"Cannot remove running server '{server_id}'. Stop it first.")
        self._servers.pop(server_id, None)

    def get_endpoints(self) -> dict[str, str]:
        """Return {server_id: endpoint_url} for running servers."""
        return {sid: s.endpoint_url for sid, s in self._servers.items() if s.is_running}

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


def install_local_inference_lifespan(app: Any) -> None:
    """Compose managed-server cleanup into an application's lifespan.

    The route layer may create local inference subprocesses after application
    startup, so cleanup must resolve the manager from application state at
    shutdown rather than capturing one eagerly.  Composing the existing
    lifespan preserves every daemon startup/shutdown hook while guaranteeing
    manager cleanup after normal, failed-startup, and exceptional-body exits.
    """
    if getattr(app.state, "_local_inference_lifespan_registered", False):
        return

    previous_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _managed_local_inference_lifespan(owner: Any) -> Any:
        try:
            async with previous_lifespan(owner) as state:
                yield state
        finally:
            manager = getattr(owner.state, "_local_inference_manager", None)
            if manager is not None:
                shutdown = manager.stop_all()
                if inspect.isawaitable(shutdown):
                    await shutdown

    app.router.lifespan_context = _managed_local_inference_lifespan
    app.state._local_inference_lifespan_registered = True
