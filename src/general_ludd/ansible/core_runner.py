"""Core Ansible runner using ansible-core as a native Python library.

Uses ansible-core's PlaybookExecutor, DataLoader, VariableManager, and
InventoryManager directly instead of the ansible-runner subprocess wrapper.

This provides direct access to:
- Ansible's variable manager and templating engine
- Inventory management
- Callback plugins
- Module execution
- Task-level control
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from general_ludd.ansible.file_tracker import FileChangeTracker

logger = logging.getLogger(__name__)

# Default wall-clock bound (seconds) for a single playbook run when the caller
# does not pass one. The network-exposed worker adapter (runner.py) always
# passes a finite timeout; this default guards any other in-process caller.
_DEFAULT_PLAYBOOK_TIMEOUT = 300.0
# rc returned when a run is killed for exceeding its wall-clock bound (matches
# the shell convention for "command timed out").
_TIMEOUT_RC = 124


def _json_safe(obj: Any) -> Any:
    """Coerce a structure to JSON/pickle-safe primitives.

    Used to ferry an AnsibleResult dump out of a timeout worker: dicts/lists recurse,
    JSON scalars pass through, and anything else (an ansible object embedded in an
    event's result payload) is replaced by its ``repr`` so the queue.put never
    fails on an unpicklable leaf.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    try:
        import json

        json.dumps(obj)
        return obj
    except Exception:
        return repr(obj)


def _env_default_timeout() -> float:
    """Resolve the default playbook timeout from GLUDD_PLAYBOOK_TIMEOUT.

    A non-positive or unparseable value falls back to the 300s default. The env
    var is read per-call so tests can override it without re-importing.
    """
    raw = os.environ.get("GLUDD_PLAYBOOK_TIMEOUT", "")
    if not raw:
        return _DEFAULT_PLAYBOOK_TIMEOUT
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_PLAYBOOK_TIMEOUT
    return val if val > 0 else _DEFAULT_PLAYBOOK_TIMEOUT


@contextlib.contextmanager
def _isolated_ansible_process_state(
    extra_env: dict[str, str] | None,
) -> Iterator[None]:
    """Bound Ansible's process-global loader and CLI state to one execution.

    ``ansible-core`` installs a collection finder in ``sys.meta_path`` and
    stores CLI options on a module global.  Inline playbook execution must not
    leave either mutation behind for unrelated imports in a long-lived worker.
    Newly imported production modules remain cached; only registries and hooks
    owned by this call are restored.
    """
    from ansible import context
    from ansible.plugins.loader import init_plugin_loader
    from ansible.utils.collection_loader import AnsibleCollectionConfig

    ansible_env_keys = (
        "ANSIBLE_COLLECTIONS_PATH",
        "ANSIBLE_ROLES_PATH",
        "ANSIBLE_COLLECTIONS_PATHS",
    )
    original_env = {key: os.environ.get(key) for key in ansible_env_keys}
    original_cliargs = context.CLIARGS
    original_finder = AnsibleCollectionConfig._collection_finder
    original_path = list(sys.path)
    original_meta_path = list(sys.meta_path)
    original_path_hooks = list(sys.path_hooks)
    original_importer_cache = dict(sys.path_importer_cache)

    if extra_env:
        for key in ansible_env_keys:
            if key in extra_env:
                os.environ[key] = extra_env[key]

    try:
        if AnsibleCollectionConfig._collection_finder is None:
            init_plugin_loader()
        yield
    finally:
        context.CLIARGS = original_cliargs
        AnsibleCollectionConfig._collection_finder = original_finder
        sys.path[:] = original_path
        sys.meta_path[:] = original_meta_path
        sys.path_hooks[:] = original_path_hooks
        sys.path_importer_cache.clear()
        sys.path_importer_cache.update(original_importer_cache)
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _timeout_child_entry(
    runner: CoreAnsibleRunner,
    queue: Any,
    exec_kwargs: dict[str, Any],
) -> None:
    """Execute one playbook inside a picklable, process-group-owned worker.

    This target must remain at module scope so ``spawn`` and ``forkserver`` can
    import it without unsafe ``fork()`` inheritance from a threaded Gunicorn or
    pytest parent. The child retains the original seccomp-before-execution and
    process-group ownership guarantees.
    """
    if runner._seccomp_filter is not None:
        try:
            applied = runner._seccomp_filter.apply()
            if not applied:
                logger.warning("seccomp filter not applied (fail-open); playbook child runs without syscall filtering")
        except Exception:
            logger.warning(
                "seccomp filter apply raised; continuing unfiltered",
                exc_info=True,
            )

    # Become a process-group leader so the parent can terminate this worker and
    # every Ansible task process it creates with one scoped signal.
    with contextlib.suppress(AttributeError, OSError):
        os.setsid()
    try:
        result = runner._execute_with_core(**exec_kwargs)
        queue.put(("ok", _json_safe(result.model_dump())))
    except BaseException as exc:  # report SystemExit and executor failures too
        queue.put(("err", f"{type(exc).__name__}: {exc}"))


try:
    from ansible.parsing.dataloader import DataLoader
    from ansible.plugins.callback import CallbackBase
    from ansible.template import Templar

    _HAS_ANSIBLE_CORE = True
except ImportError:
    _HAS_ANSIBLE_CORE = False
    CallbackBase = object

try:
    import ansible_runner

    _HAS_ANSIBLE_RUNNER = True
except ImportError:
    _HAS_ANSIBLE_RUNNER = False
    ansible_runner = None


def _get_templar(loader: Any = None, variables: dict[str, Any] | None = None) -> Any:
    if not _HAS_ANSIBLE_CORE:
        raise ImportError("ansible-core is required for templating but is not installed")
    if loader is None:
        loader = DataLoader()
    return Templar(loader=loader, variables=variables or {})


class _EventCollectorCallback(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "gludd_event_collector"

    def __init__(self) -> None:
        super().__init__()
        self._events: list[dict[str, Any]] = []
        self._host_stats: dict[str, Any] = {}

    def v2_runner_on_start(self, host: Any, task: Any) -> None:
        self._events.append(
            {
                "event": "runner_on_start",
                "host": str(host),
                "task": str(task),
            }
        )

    def v2_runner_on_ok(self, result: Any) -> None:
        self._events.append(
            {
                "event": "runner_on_ok",
                "host": str(result._host),
                "task": str(result._task),
                "result": result._result,
            }
        )

    def v2_runner_on_failed(self, result: Any, ignore_errors: bool = False) -> None:
        self._events.append(
            {
                "event": "runner_on_failed",
                "host": str(result._host),
                "task": str(result._task),
                "result": result._result,
                "ignore_errors": ignore_errors,
            }
        )

    def v2_runner_on_skipped(self, result: Any) -> None:
        self._events.append(
            {
                "event": "runner_on_skipped",
                "host": str(result._host),
                "task": str(result._task),
            }
        )

    def v2_runner_on_unreachable(self, result: Any) -> None:
        self._events.append(
            {
                "event": "runner_on_unreachable",
                "host": str(result._host),
                "task": str(result._task),
            }
        )

    def v2_playbook_on_start(self, playbook: Any) -> None:
        self._events.append(
            {
                "event": "playbook_on_start",
                "playbook": str(playbook),
            }
        )

    def v2_playbook_on_stats(self, stats: Any) -> None:
        self._host_stats = {}
        for host, host_stats in stats.processed.items():
            self._host_stats[str(host)] = host_stats
        self._events.append(
            {
                "event": "playbook_on_stats",
                "stats": self._host_stats,
            }
        )


class AnsibleOptions:
    """Options object mirroring the ansible CLI args a playbook run accepts."""

    def __init__(
        self,
        inventory: list[str] | None = None,
        extravars: dict[str, Any] | None = None,
        verbosity: int = 0,
        check: bool = False,
        diff: bool = False,
        forks: int = 5,
        become: bool = False,
        become_method: str | None = None,
        become_user: str | None = None,
        connection: str = "local",
        module_path: list[str] | None = None,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        start_at_task: str | None = None,
    ) -> None:
        """Build an AnsibleOptions instance with safe defaults."""
        self.inventory = inventory or ["localhost,"]
        self.extravars = extravars
        self.verbosity = verbosity
        self.check = check
        self.diff = diff
        self.forks = forks
        self.become = become
        self.become_method = become_method
        self.become_user = become_user
        self.connection = connection
        self.module_path = module_path or []
        self.tags = tags or ["all"]
        self.skip_tags = skip_tags or []
        self.start_at_task = start_at_task


class AnsibleResult(BaseModel):
    """Result of one playbook execution: status, rc, stats, events, error."""

    status: str = "unknown"
    rc: int = 0
    stats: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    host_results: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _strip(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip()
        return v


class CoreAnsibleRunner:
    """Execute Ansible playbooks in-process via ansible-core's PlaybookExecutor."""

    def __init__(
        self,
        module_paths: list[str] | None = None,
        callback_plugins: list[str] | None = None,
        process_isolation: Any | None = None,
        private_data_dir: str | None = None,
        network_policy: Any | None = None,
        seccomp_filter: Any | None = None,
    ) -> None:
        """Initialize the runner with optional module paths, callbacks, and sandboxing."""
        self._module_paths = module_paths or []
        self._callback_plugins = callback_plugins or []
        self._process_isolation = process_isolation
        self._private_data_dir = private_data_dir
        self._network_policy = network_policy
        # OpenShell P2 transfer: an optional seccomp BPF filter installed in the
        # timeout child (before os.setsid) to block container-escape syscalls
        # (mount/unshare/setns/pivot_root/...). None = no syscall filtering
        # (backward-compatible default). See general_ludd.security.seccomp.
        self._seccomp_filter = seccomp_filter
        self._collected_events: list[dict[str, Any]] = []

    def close(self) -> None:
        """Remove the runner's private data directory if one was created."""
        if self._private_data_dir and os.path.isdir(self._private_data_dir):
            import shutil

            shutil.rmtree(self._private_data_dir, ignore_errors=True)
            self._private_data_dir = ""

    def run_playbook(
        self,
        playbook_path: str,
        inventory: list[str] | None = None,
        extravars: dict[str, Any] | None = None,
        verbosity: int = 0,
        check: bool = False,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        connection: str = "local",
        become: bool = False,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> AnsibleResult:
        """Run one playbook with network-policy scanning, unsafe-wrapping, and a timeout bound."""
        if not _HAS_ANSIBLE_CORE:
            raise ImportError("ansible-core is required for playbook execution but is not installed")

        # L7 network policy (OpenShell P0 transfer): before ANY execution, scan
        # the playbook for ansible.builtin.uri / get_url tasks and validate each
        # outbound HTTP request (method + path + host) against the declarative
        # policy. Blocks a POST to an allowed host under a GET-only rule even
        # when the host matches the sandbox net: allowlist. On any violation the
        # run is fail-closed BEFORE the playbook process starts.
        if self._network_policy is not None:
            from general_ludd.ansible.network_policy import scan_playbook_tasks

            violations = scan_playbook_tasks(playbook_path, self._network_policy)
            if violations:
                logger.warning(
                    "network_policy blocked playbook execution: %s",
                    "; ".join(violations),
                    extra={
                        "event_type": "network_policy_block_playbook",
                        "playbook": playbook_path,
                        "violations": violations,
                    },
                )
                return AnsibleResult(
                    status="failed",
                    rc=1,
                    error=("network policy denied outbound HTTP request(s): " + "; ".join(violations)),
                )

        # HIGH (unwrapped extravars): wrap EVERY untrusted extra-var value
        # Ansible-unsafe before it reaches the executor, so embedded Jinja in
        # model output (or any other caller-supplied value) is never re-
        # templated into a shell/command/template task. Applied to BOTH paths
        # (in-process PlaybookExecutor and subprocess ansible-runner).
        from general_ludd.ansible.unsafe import wrap_extravars

        safe_extravars = wrap_extravars(extravars)

        # HIGH (process_isolation): when isolation is REQUESTED, delegate to the
        # ansible-runner subprocess backend which spawns podman/bwrap for real
        # container confinement. The in-process PlaybookExecutor path cannot
        # honor isolation (no container subprocess is spawned). This is the
        # finding #1 real fix: the Wave 13 fail-closed guard is replaced by
        # actual confinement via ansible-runner.
        iso = self._process_isolation
        if iso is not None and getattr(iso, "enabled", False):
            return self._execute_with_runner(
                playbook_path=playbook_path,
                inventory=inventory,
                extravars=safe_extravars,
                verbosity=verbosity,
                check=check,
                tags=tags,
                skip_tags=skip_tags,
                connection=connection,
                become=become,
                extra_env=extra_env,
            )

        # HIGH (no timeout): bound the run in a killable child process. An
        # unbounded pb_exec.run() let a runaway/sleeping playbook hang the
        # worker forever. The network-exposed adapter (runner.py) ALWAYS passes
        # a finite timeout, so the exposed path is always bounded.
        #
        # Bounding requires a child process, which (a) cannot share an in-process
        # mock and (b) serializes the result across the process boundary. So an
        # explicit timeout=None means "run inline, no bound" — preserving the
        # in-process API (direct event/stat collection, mockable executor) for
        # trusted callers and tests. A None timeout falls back to the env-driven
        # default ONLY when one is configured, never to a silent child process.
        if timeout is None:
            env_to = os.environ.get("GLUDD_PLAYBOOK_TIMEOUT", "")
            if not env_to:
                return self._execute_with_core(
                    playbook_path=playbook_path,
                    inventory=inventory,
                    extravars=safe_extravars,
                    verbosity=verbosity,
                    check=check,
                    tags=tags,
                    skip_tags=skip_tags,
                    connection=connection,
                    become=become,
                    extra_env=extra_env,
                )
            bound = _env_default_timeout()
        else:
            bound = timeout

        return self._run_with_timeout(
            timeout=bound,
            playbook_path=playbook_path,
            inventory=inventory,
            extravars=safe_extravars,
            verbosity=verbosity,
            check=check,
            tags=tags,
            skip_tags=skip_tags,
            connection=connection,
            become=become,
            extra_env=extra_env,
        )

    def _run_with_timeout(
        self,
        timeout: float,
        **exec_kwargs: Any,
    ) -> AnsibleResult:
        """Run ``_execute_with_core`` in a thread-safe child bounded by timeout.

        The child puts its serialized AnsibleResult on a queue. The parent joins
        with a deadline; on expiry it terminate()s then kill()s the child and
        returns a failed result with rc 124. A non-positive timeout means "no
        bound" and runs inline.
        """
        if timeout is None or timeout <= 0:
            return self._execute_with_core(**exec_kwargs)

        # Python 3.14 explicitly warns that fork() from a multithreaded process
        # is unsafe. Prefer forkserver where supported: its single-threaded
        # server retains copy-on-write efficiency without inheriting Gunicorn's
        # thread state. Spawn is the portable safe fallback.
        start_methods = multiprocessing.get_all_start_methods()
        start_method = "forkserver" if "forkserver" in start_methods else "spawn"
        try:
            # Typeshed exposes a common context base that omits the concrete
            # Process factory even though every returned runtime context has it.
            ctx: Any = multiprocessing.get_context(start_method)
        except ValueError as exc:  # pragma: no cover - Python always has spawn
            # Never weaken a requested wall-clock bound by falling back inline.
            return AnsibleResult(
                status="failed",
                rc=1,
                error=(
                    f"no thread-safe multiprocessing start method is available for the playbook timeout worker: {exc}"
                ),
            )

        queue: Any = ctx.Queue()

        # NOT daemon=True: ansible's PlaybookExecutor forks its own task-worker
        # processes, and a daemonic parent cannot have children (Python forbids
        # it), which would silently no-op the play. We instead kill the whole
        # process GROUP on timeout to reap any worker children too.
        try:
            proc = ctx.Process(
                target=_timeout_child_entry,
                args=(self, queue, exec_kwargs),
                daemon=False,
            )
            proc.start()
        except Exception as exc:
            # Release the queue's feeder thread + file descriptors even on a
            # failed child start; a leaked Queue keeps its thread alive.
            try:
                queue.close()
                queue.join_thread()
            except Exception:
                pass
            return AnsibleResult(
                status="failed",
                rc=1,
                error=(f"unable to start {start_method} playbook timeout worker: {type(exc).__name__}: {exc}"),
            )

        # Track this child (and its setsid process group) in the managed-process
        # registry so the admin API can inspect/signal it. Registration must
        # never break job execution, so it is fully guarded.
        try:
            from general_ludd.process.registry import default_registry

            _pid = proc.pid
            if _pid is not None:
                default_registry().register(
                    _pid,
                    command=[
                        "ansible-playbook-runner",
                        str(exec_kwargs.get("playbook_path", "")),
                    ],
                    origin="ansible_runner",
                )
        except Exception:
            logger.debug("managed-process registration failed", exc_info=True)

        try:
            proc.join(timeout)

            if proc.is_alive():
                # Deadline blown — kill the child and (best-effort) its worker
                # tree, then hard-kill if it ignores SIGTERM.
                self._terminate_tree(proc)
                logger.error("Playbook exceeded wall-clock timeout of %.1fs; killed", timeout)
                return AnsibleResult(
                    status="failed",
                    rc=_TIMEOUT_RC,
                    error=f"playbook timed out after {timeout:.1f}s",
                )

            try:
                kind, payload = queue.get_nowait()
            except Exception:
                # Child exited without posting a result (crash/OOM/SIGKILL).
                return AnsibleResult(
                    status="failed",
                    rc=1,
                    error="playbook child exited without a result",
                )
            if kind == "ok":
                return AnsibleResult(**payload)
            return AnsibleResult(status="failed", rc=1, error=str(payload))
        finally:
            # The child has been joined or terminated; drop it from the registry.
            try:
                from general_ludd.process.registry import default_registry

                _pid = proc.pid
                if _pid is not None:
                    default_registry().deregister(_pid)
            except Exception:
                logger.debug("managed-process deregister failed", exc_info=True)
            try:
                queue.close()
                queue.join_thread()
            except Exception:
                logger.debug("playbook timeout queue cleanup failed", exc_info=True)
            with contextlib.suppress(AttributeError, ValueError):
                proc.close()

    @staticmethod
    def _terminate_tree(proc: Any) -> None:
        """Kill a timed-out child and the process group it leads.

        The child called ``os.setsid()``, so its PID is its process-group id and
        every ansible worker it forked shares that group. We SIGTERM the group,
        give it a moment, then SIGKILL the group, and finally reap the child.
        """
        pid = proc.pid
        for sig_name in ("SIGTERM", "SIGKILL"):
            if not proc.is_alive():
                break
            try:
                import signal

                sig = getattr(signal, sig_name)
                if pid is not None:
                    try:
                        os.killpg(pid, sig)
                    except (ProcessLookupError, PermissionError, OSError):
                        # Fall back to signalling just the child.
                        os.kill(pid, sig)
            except (ProcessLookupError, OSError):
                pass
            proc.join(5.0)
        if proc.is_alive():  # pragma: no cover - last-resort
            proc.kill()
            proc.join(5.0)

    # Env vars whose values are NOT secrets and are safe to pass through to the
    # playbook subprocess.  Anything not on this list (ZAI_API_KEY, GLUDD_PSK,
    # AWS_*, OPENAI_*, DATABASE_URL, …) is stripped before pb_exec.run().
    _PLAYBOOK_ENV_ALLOWLIST: frozenset[str] = frozenset(
        {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TMPDIR",
            "TEMP",
            "TMP",
            # Gludd runner configuration (not secrets — callers use these to tune
            # playbook behaviour at the process level; GLUDD_PSK / ZAI_API_KEY /
            # AWS_* are intentionally absent and must stay absent).
            "GLUDD_PLAYBOOK_TIMEOUT",
            # Ansible configuration (not secrets)
            "ANSIBLE_CONFIG",
            "ANSIBLE_ROLES_PATH",
            "ANSIBLE_COLLECTIONS_PATHS",
            "ANSIBLE_COLLECTIONS_PATH",
            "ANSIBLE_LIBRARY",
            "ANSIBLE_MODULE_UTILS",
            "ANSIBLE_FILTER_PLUGINS",
            "ANSIBLE_CALLBACK_PLUGINS",
            "ANSIBLE_LOOKUP_PLUGINS",
            "ANSIBLE_STRATEGY_PLUGINS",
            "ANSIBLE_CACHE_PLUGINS",
            "ANSIBLE_CONNECTION_PLUGINS",
            "ANSIBLE_VARS_PLUGINS",
            "ANSIBLE_HOST_KEY_CHECKING",
            "ANSIBLE_STDOUT_CALLBACK",
            "ANSIBLE_RETRY_FILES_ENABLED",
            "ANSIBLE_FORCE_COLOR",
            "ANSIBLE_NOCOLOR",
            "ANSIBLE_VERBOSITY",
            # Python runtime (needed by ansible-core modules)
            "PYTHONPATH",
            "PYTHONDONTWRITEBYTECODE",
            "VIRTUAL_ENV",
            # SSH (connection metadata only — no keys)
            "SSH_AUTH_SOCK",
            "SSH_AGENT_PID",
            # Display / terminal (needed by some callbacks)
            "TERM",
            "COLUMNS",
            "LINES",
        }
    )

    def _execute_with_runner(
        self,
        playbook_path: str,
        inventory: list[str] | None = None,
        extravars: dict[str, Any] | None = None,
        verbosity: int = 0,
        check: bool = False,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        connection: str = "local",
        become: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> AnsibleResult:
        """Execute playbook via the ansible-runner subprocess backend.

        Invoked when ``process_isolation.enabled=True``. ansible-runner spawns
        the configured isolation executable (podman/bwrap) so the playbook runs
        inside a real container — providing the confinement that the Wave 13
        fail-closed guard stood in for. Falls back to a failed result if the
        ansible-runner package is unavailable.
        """
        if not _HAS_ANSIBLE_RUNNER:
            return AnsibleResult(
                status="failed",
                rc=1,
                error=(
                    "process_isolation is enabled but the ansible-runner "
                    "package is not installed. Install it (it is a declared "
                    "dependency in pyproject.toml) to enable container confinement."
                ),
            )

        iso = self._process_isolation
        if iso is None:
            return AnsibleResult(
                status="failed",
                rc=1,
                error="process_isolation config unexpectedly missing on isolation path",
            )

        import tempfile

        private_data_dir = self._private_data_dir or tempfile.mkdtemp(prefix="gl-runner-iso-")

        runner_kwargs: dict[str, Any] = {
            "private_data_dir": private_data_dir,
            "playbook": playbook_path,
            **iso.to_runner_kwargs(),
        }
        if inventory:
            runner_kwargs["inventory"] = inventory
        if extravars:
            runner_kwargs["extravars"] = extravars
        if verbosity:
            runner_kwargs["verbosity"] = verbosity

        cmdline: list[str] = []
        if check:
            cmdline.append("--check")
        if tags:
            cmdline.extend(["--tags", ",".join(tags)])
        if skip_tags:
            cmdline.extend(["--skip-tags", ",".join(skip_tags)])
        if become:
            cmdline.append("--become")
        if connection and connection != "local":
            cmdline.extend(["--connection", connection])
        if cmdline:
            runner_kwargs["cmdline"] = cmdline

        if extra_env:
            runner_kwargs["envvars"] = dict(extra_env)

        self._collected_events = []

        file_tracker = FileChangeTracker(repo_root=Path.cwd())
        runner_kwargs["event_handler"] = file_tracker.event_handler

        try:
            runner_obj = ansible_runner.run(**runner_kwargs)
            self._file_tracker = file_tracker
        except Exception as exc:
            return AnsibleResult(
                status="failed",
                rc=1,
                error=f"ansible-runner invocation raised: {type(exc).__name__}: {exc}",
            )

        rc = int(getattr(runner_obj, "rc", 1) or 0)
        raw_status = getattr(runner_obj, "status", None)
        status = str(raw_status).strip() if raw_status else "failed"

        stats: dict[str, Any] = {}
        stats_attr = getattr(runner_obj, "stats", None)
        if isinstance(stats_attr, dict):
            stats = dict(stats_attr)

        events: list[dict[str, Any]] = []
        events_attr = getattr(runner_obj, "events", None)
        if events_attr:
            try:
                events = [_json_safe(dict(e)) for e in events_attr]
            except (TypeError, ValueError):
                events = [_json_safe(e) for e in list(events_attr)]

        if status == "successful" and rc == 0:
            return AnsibleResult(
                status="successful",
                rc=0,
                stats=stats,
                events=events,
            )
        return AnsibleResult(
            status="failed",
            rc=rc if rc != 0 else 1,
            stats=stats,
            events=events,
            error=f"ansible-runner subprocess reported status={status} (rc={rc})",
        )

    def _execute_with_core(
        self,
        playbook_path: str,
        inventory: list[str] | None = None,
        extravars: dict[str, Any] | None = None,
        verbosity: int = 0,
        check: bool = False,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        connection: str = "local",
        become: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> AnsibleResult:
        """Execute with every ansible-core process-global mutation bounded."""
        with _isolated_ansible_process_state(extra_env):
            return self._execute_with_core_active_state(
                playbook_path=playbook_path,
                inventory=inventory,
                extravars=extravars,
                verbosity=verbosity,
                check=check,
                tags=tags,
                skip_tags=skip_tags,
                connection=connection,
                become=become,
                extra_env=extra_env,
            )

    def _execute_with_core_active_state(
        self,
        playbook_path: str,
        inventory: list[str] | None = None,
        extravars: dict[str, Any] | None = None,
        verbosity: int = 0,
        check: bool = False,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        connection: str = "local",
        become: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> AnsibleResult:
        from ansible import context
        from ansible.executor.playbook_executor import PlaybookExecutor
        from ansible.inventory.manager import InventoryManager
        from ansible.module_utils.common.collections import ImmutableDict
        from ansible.vars.manager import VariableManager

        loader = DataLoader()

        options = AnsibleOptions(
            inventory=inventory or ["localhost,"],
            extravars=extravars,
            verbosity=verbosity,
            check=check,
            tags=tags,
            skip_tags=skip_tags,
            connection=connection,
            become=become,
        )

        context.CLIARGS = ImmutableDict(
            inventory=options.inventory,
            extravars=options.extravars or {},
            verbosity=options.verbosity,
            check=options.check,
            diff=options.diff,
            forks=options.forks,
            become=options.become,
            become_method=options.become_method or "sudo",
            become_user=options.become_user or "root",
            connection=options.connection,
            module_path=options.module_path,
            tags=options.tags,
            skip_tags=options.skip_tags,
            start_at_task=options.start_at_task or None,
            listhosts=False,
            listtasks=False,
            listtags=False,
            syntax=False,
            subset=None,
            private_key_file=None,
            ssh_common_args=None,
            ssh_extra_args=None,
            sftp_extra_args=None,
            scp_extra_args=None,
            ask_vault_pass=False,
            vault_password_files=None,
            vault_ids=None,
        )

        inventory_mgr = InventoryManager(loader=loader, sources=options.inventory)
        variable_mgr = VariableManager(loader=loader, inventory=inventory_mgr)
        if extravars:
            # In ansible-core >= 2.14, VariableManager.extra_vars is a read-only
            # property (getter returns self._extra_vars; no setter). Assigning to
            # it raises AttributeError. Write the backing field directly — this
            # is what the property getter reads and what PlaybookExecutor
            # consumes via variable_manager._extra_vars.
            variable_mgr._extra_vars = extravars

        self._collected_events = []

        callback = _EventCollectorCallback()

        pb_exec = PlaybookExecutor(
            playbooks=[playbook_path],
            inventory=inventory_mgr,
            variable_manager=variable_mgr,
            loader=loader,
            passwords={},
        )
        # ansible-core 2.19+ dispatches through the callback-method map built by
        # ``_init_callback_methods``.  Callback loader normally performs this
        # initialization, but this collector is registered programmatically.
        # Without it, successful and failed task events are silently discarded
        # and callers receive only an opaque executor return code.
        init_callback_methods = getattr(callback, "_init_callback_methods", None)
        if callable(init_callback_methods):
            init_callback_methods()
        pb_exec._tqm._callback_plugins.append(callback)

        # HIGH (env leak): build a minimal allowlisted env so playbook tasks
        # never inherit ZAI_API_KEY / GLUDD_PSK / AWS_* / DATABASE_URL or any
        # other secret that happens to be set in the parent process.  We swap
        # os.environ in-place (Ansible reads it at run time via os.environ
        # directly, not via a captured snapshot), then restore unconditionally
        # in the finally block.  extra_env holds non-secret caller overrides
        # (e.g. ansible-specific vars passed from runner.py) and is merged in
        # AFTER the allowlist so it cannot re-introduce stripped secrets unless
        # the caller explicitly opts in.
        scrubbed_env: dict[str, str] = {k: v for k, v in os.environ.items() if k in self._PLAYBOOK_ENV_ALLOWLIST}
        if extra_env:
            scrubbed_env.update(extra_env)

        _original_env = os.environ.copy()
        os.environ.clear()
        os.environ.update(scrubbed_env)
        pb_rc = 0
        try:
            # PlaybookExecutor.run() returns a return code: 0 on success,
            # non-zero on any task failure (including ansible.builtin.fail).
            # The previous code discarded this and always reported success,
            # masking role failures. Capture and propagate it.
            pb_rc = int(pb_exec.run() or 0)
        finally:
            os.environ.clear()
            os.environ.update(_original_env)
            # ansible-core closes its worker queue but currently leaves the
            # TaskQueueManager connection lock's TemporaryFile open.  Bound
            # that descriptor to this execution so repeated inline runs do not
            # leak pytest capture resources or exhaust a long-lived worker.
            tqm = getattr(pb_exec, "_tqm", None)
            connection_lock = getattr(tqm, "_connection_lockfile", None)
            if connection_lock is not None:
                connection_lock.close()

        self._collected_events = list(callback._events)

        stats: dict[str, Any] = {}
        if hasattr(pb_exec, "_tqm") and hasattr(pb_exec._tqm, "_stats"):
            tqm_stats = pb_exec._tqm._stats
            if hasattr(tqm_stats, "process_tally"):
                stats = dict(tqm_stats.process_tally) if tqm_stats.process_tally else {}
            elif hasattr(tqm_stats, "processed"):
                for _host, host_stats in tqm_stats.processed.items():
                    # In modern ansible-core, AggregateStats.processed[host] may
                    # be either a dict {counter: int} or an int (sum of changes).
                    # Defensive: only iterate when it is actually a mapping.
                    if isinstance(host_stats, dict):
                        for key, val in host_stats.items():
                            stats[key] = stats.get(key, 0) + val
                    elif isinstance(host_stats, int):
                        stats[_host] = stats.get(_host, 0) + host_stats
        if not stats:
            stats = dict(callback._host_stats)

        # Determine success from the executor return code; a non-zero pb_rc
        # means at least one task failed (fail/assert/unreachable). Fall back
        # to scanning the host_stats for failures/unreachable as a second
        # signal (covers executors that return 0 while reporting failures).
        failed_count = (
            stats.get("failures", 0) + stats.get("failed", 0) + stats.get("unreachable", 0)
            if isinstance(stats, dict)
            else 0
        )
        if pb_rc != 0 or failed_count:
            if pb_rc != 0:
                return AnsibleResult(
                    status="failed",
                    rc=pb_rc,
                    stats=stats,
                    events=list(self._collected_events),
                    error=f"ansible playbook execution failed with rc={pb_rc}",
                )
            return AnsibleResult(
                status="failed",
                rc=1,
                stats=stats,
                events=list(self._collected_events),
                error="ansible playbook reported task failures with rc=0",
            )

        return AnsibleResult(
            status="successful",
            rc=0,
            stats=stats,
            events=list(self._collected_events),
        )

    def render_template(
        self,
        template_str: str,
        variables: dict[str, Any] | None = None,
    ) -> str:
        """Render a Jinja template string with optional variables via ansible Templar."""
        if not _HAS_ANSIBLE_CORE:
            raise ImportError("ansible-core is required for templating but is not installed")
        templar = _get_templar(variables=variables)
        result = templar.template(template_str)
        rendered = str(result)
        if rendered == template_str and "{{" in template_str:
            # Some ansible-core Templar configurations return the input
            # unchanged for simple expressions. The trusted path may fall
            # back to a plain Jinja2 render (variables are already validated
            # by the caller) so basic templates always render.
            from jinja2 import Environment, StrictUndefined

            env = Environment(undefined=StrictUndefined)
            rendered = env.from_string(template_str).render(**(variables or {}))
        return rendered

    def resolve_variable(
        self,
        var_name: str,
        host: str = "localhost",
        inventory_path: str | None = None,
        extravars: dict[str, Any] | None = None,
    ) -> Any:
        """Resolve a single host variable using ansible's variable manager."""
        if not _HAS_ANSIBLE_CORE:
            raise ImportError("ansible-core is required for variable resolution but is not installed")
        return self._resolve_with_variable_manager(var_name, host, inventory_path, extravars)

    def _resolve_with_variable_manager(
        self,
        var_name: str,
        host: str = "localhost",
        inventory_path: str | None = None,
        extravars: dict[str, Any] | None = None,
    ) -> Any:
        from ansible.inventory.manager import InventoryManager
        from ansible.vars.manager import VariableManager

        loader = DataLoader()
        sources = [inventory_path] if inventory_path else ["localhost,"]
        inventory_mgr = InventoryManager(loader=loader, sources=sources)
        variable_mgr = VariableManager(loader=loader, inventory=inventory_mgr)
        if extravars:
            # See note in _execute_playbook: extra_vars is read-only in
            # ansible-core >= 2.14; write the backing field directly.
            variable_mgr._extra_vars = extravars

        hosts = inventory_mgr.get_hosts(pattern=host)
        if not hosts:
            return None
        host_vars = variable_mgr.get_vars(host=hosts[0])
        return host_vars.get(var_name)

    def list_tasks(
        self,
        playbook_path: str,
        extravars: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Return a flat list of {name, module, hosts} for every task in a playbook."""
        _NON_MODULE_KEYS = {
            "name",
            "when",
            "loop",
            "with_items",
            "with_dict",
            "register",
            "become",
            "become_user",
            "delegate_to",
            "ignore_errors",
            "notify",
            "tags",
            "vars",
            "block",
            "rescue",
            "always",
            "args",
            "changed_when",
            "failed_when",
            "retries",
            "delay",
            "until",
            "run_once",
            "local_action",
            "delegate_facts",
        }

        try:
            with open(playbook_path) as f:
                plays = yaml.safe_load(f) or []
        except Exception:
            return []

        tasks: list[dict[str, str]] = []
        for play in plays:
            if not isinstance(play, dict):
                continue
            play_hosts = play.get("hosts", "all")
            for task in play.get("tasks", []):
                if not isinstance(task, dict):
                    continue
                task_name = task.get("name", "")
                module = ""
                for key in task:
                    if key not in _NON_MODULE_KEYS:
                        module = key
                        break
                tasks.append(
                    {
                        "name": task_name,
                        "module": module,
                        "hosts": str(play_hosts),
                    }
                )
        return tasks

    def validate_playbook_syntax(self, playbook_path: str) -> list[str]:
        """Validate a playbook parses as YAML and is a list of plays with hosts keys."""
        errors: list[str] = []

        if not os.path.isfile(playbook_path):
            errors.append(f"Playbook file not found: {playbook_path}")
            return errors

        try:
            with open(playbook_path) as f:
                plays = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            errors.append(f"YAML syntax error: {exc}")
            return errors

        if not isinstance(plays, list):
            errors.append("Playbook must be a list of plays")
            return errors

        for i, play in enumerate(plays):
            if not isinstance(play, dict):
                errors.append(f"Play {i} is not a mapping")
                continue
            if "hosts" not in play:
                errors.append(f"Play {i} ({play.get('name', 'unnamed')}) is missing 'hosts' key")

        return errors
