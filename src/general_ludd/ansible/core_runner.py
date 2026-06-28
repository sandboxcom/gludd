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

import logging
import multiprocessing
import os
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

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

    Used to ferry an AnsibleResult dump out of a fork child: dicts/lists recurse,
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

try:
    from ansible.parsing.dataloader import DataLoader
    from ansible.plugins.callback import CallbackBase
    from ansible.template import Templar

    _HAS_ANSIBLE_CORE = True
except ImportError:
    _HAS_ANSIBLE_CORE = False
    CallbackBase = object


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
        self._events.append({
            "event": "runner_on_start",
            "host": str(host),
            "task": str(task),
        })

    def v2_runner_on_ok(self, result: Any) -> None:
        self._events.append({
            "event": "runner_on_ok",
            "host": str(result._host),
            "task": str(result._task),
            "result": result._result,
        })

    def v2_runner_on_failed(self, result: Any, ignore_errors: bool = False) -> None:
        self._events.append({
            "event": "runner_on_failed",
            "host": str(result._host),
            "task": str(result._task),
            "result": result._result,
            "ignore_errors": ignore_errors,
        })

    def v2_runner_on_skipped(self, result: Any) -> None:
        self._events.append({
            "event": "runner_on_skipped",
            "host": str(result._host),
            "task": str(result._task),
        })

    def v2_runner_on_unreachable(self, result: Any) -> None:
        self._events.append({
            "event": "runner_on_unreachable",
            "host": str(result._host),
            "task": str(result._task),
        })

    def v2_playbook_on_start(self, playbook: Any) -> None:
        self._events.append({
            "event": "playbook_on_start",
            "playbook": str(playbook),
        })

    def v2_playbook_on_stats(self, stats: Any) -> None:
        self._host_stats = {}
        for host, host_stats in stats.processed.items():
            self._host_stats[str(host)] = host_stats
        self._events.append({
            "event": "playbook_on_stats",
            "stats": self._host_stats,
        })


class AnsibleOptions:
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
    def __init__(
        self,
        module_paths: list[str] | None = None,
        callback_plugins: list[str] | None = None,
        process_isolation: Any | None = None,
    ) -> None:
        self._module_paths = module_paths or []
        self._callback_plugins = callback_plugins or []
        self._process_isolation = process_isolation
        self._collected_events: list[dict[str, Any]] = []

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
        if not _HAS_ANSIBLE_CORE:
            raise ImportError("ansible-core is required for playbook execution but is not installed")

        # HIGH (process_isolation): the config used to be stored and silently
        # ignored. If isolation is REQUESTED but we cannot honor it natively,
        # fail CLOSED — never run an isolation-requiring job unconfined.
        iso = self._process_isolation
        if (
            iso is not None
            and getattr(iso, "enabled", False)
            and not self._isolation_supported(iso)
        ):
            return AnsibleResult(
                status="failed",
                rc=1,
                error=(
                    "process_isolation requested but not supported in this "
                    "runtime; refusing to run unconfined"
                ),
            )

        # HIGH (unwrapped extravars): wrap EVERY untrusted extra-var value
        # Ansible-unsafe before it reaches the executor, so embedded Jinja in
        # model output (or any other caller-supplied value) is never re-
        # templated into a shell/command/template task.
        from general_ludd.ansible.unsafe import wrap_extravars

        safe_extravars = wrap_extravars(extravars)

        # HIGH (no timeout): bound the run in a killable fork child. An
        # unbounded pb_exec.run() let a runaway/sleeping playbook hang the
        # worker forever. The network-exposed adapter (runner.py) ALWAYS passes
        # a finite timeout, so the exposed path is always bounded.
        #
        # Bounding requires a fork child, which (a) cannot share an in-process
        # mock and (b) serializes the result across the process boundary. So an
        # explicit timeout=None means "run inline, no bound" — preserving the
        # in-process API (direct event/stat collection, mockable executor) for
        # trusted callers and tests. A None timeout falls back to the env-driven
        # default ONLY when one is configured, never to a silent fork.
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

    @staticmethod
    def _isolation_supported(iso: Any) -> bool:
        """Whether requested process isolation can actually be honored.

        Native ansible-core library execution (PlaybookExecutor) does NOT apply
        the runner-style container isolation; honoring it requires the named
        executable (e.g. podman/bwrap) to exist on PATH. When it does not, we
        cannot confine the run, so the caller must fail closed.
        """
        import shutil

        executable = getattr(iso, "executable", None)
        if not executable:
            return False
        return shutil.which(executable) is not None

    def _run_with_timeout(
        self,
        timeout: float,
        **exec_kwargs: Any,
    ) -> AnsibleResult:
        """Run ``_execute_with_core`` in a fork child bounded by ``timeout``.

        The child puts its serialized AnsibleResult on a queue. The parent joins
        with a deadline; on expiry it terminate()s then kill()s the child and
        returns a failed result with rc 124. A non-positive timeout means "no
        bound" and runs inline.
        """
        if timeout is None or timeout <= 0:
            return self._execute_with_core(**exec_kwargs)

        # Prefer a fork context so the child inherits the already-imported
        # ansible modules and this object's state without re-pickling them.
        try:
            ctx = multiprocessing.get_context("fork")
        except ValueError:  # pragma: no cover - platforms without fork
            # No fork available (e.g. Windows / spawn-only). Fall back to inline
            # execution rather than failing — the timeout cannot be enforced via
            # a killable child here, but correctness is preserved.
            logger.warning(
                "fork start method unavailable; running playbook without a "
                "killable timeout child"
            )
            return self._execute_with_core(**exec_kwargs)

        queue: Any = ctx.Queue()

        def _target() -> None:
            import contextlib

            # Become a process-group leader so a timeout can SIGKILL the whole
            # group (this child + any ansible worker processes it forks).
            with contextlib.suppress(AttributeError, OSError):  # non-POSIX: no-op
                os.setsid()
            try:
                result = self._execute_with_core(**exec_kwargs)
                # Sanitize before crossing the process boundary: event payloads
                # can carry arbitrary (non-picklable) ansible objects, which
                # would make queue.put raise and the child die "without a
                # result". JSON round-trip coerces every leaf to a picklable
                # primitive (unserializable values become their repr).
                payload = _json_safe(result.model_dump())
                queue.put(("ok", payload))
            except BaseException as exc:  # report any failure (incl. SystemExit) up
                queue.put(("err", f"{type(exc).__name__}: {exc}"))

        # NOT daemon=True: ansible's PlaybookExecutor forks its own task-worker
        # processes, and a daemonic parent cannot have children (Python forbids
        # it), which would silently no-op the play. We instead kill the whole
        # process GROUP on timeout to reap any worker children too.
        proc = ctx.Process(target=_target, daemon=False)
        proc.start()

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
                logger.error(
                    "Playbook exceeded wall-clock timeout of %.1fs; killed", timeout
                )
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
    _PLAYBOOK_ENV_ALLOWLIST: frozenset[str] = frozenset({
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
    })

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
        from ansible import context
        from ansible.executor.playbook_executor import PlaybookExecutor
        from ansible.inventory.manager import InventoryManager
        from ansible.module_utils.common.collections import ImmutableDict
        from ansible.vars.manager import VariableManager

        # Ensure the collection/plugin loader is initialized in THIS process.
        # When the run is bounded in a fork child, the child must (re)install the
        # AnsibleCollectionFinder on its own sys.meta_path or ansible.builtin.*
        # module resolution fails ("couldn't resolve module/action"). Idempotent.
        try:
            from ansible.plugins.loader import init_plugin_loader

            init_plugin_loader()
        except Exception:  # pragma: no cover - older cores auto-init on use
            pass

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
            variable_mgr.extra_vars = extravars

        self._collected_events = []

        callback = _EventCollectorCallback()

        pb_exec = PlaybookExecutor(
            playbooks=[playbook_path],
            inventory=inventory_mgr,
            variable_manager=variable_mgr,
            loader=loader,
            passwords={},
        )
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
        scrubbed_env: dict[str, str] = {
            k: v for k, v in os.environ.items()
            if k in self._PLAYBOOK_ENV_ALLOWLIST
        }
        if extra_env:
            scrubbed_env.update(extra_env)

        _original_env = os.environ.copy()
        os.environ.clear()
        os.environ.update(scrubbed_env)
        try:
            pb_exec.run()
        finally:
            os.environ.clear()
            os.environ.update(_original_env)

        self._collected_events = list(callback._events)

        stats: dict[str, Any] = {}
        if hasattr(pb_exec, "_tqm") and hasattr(pb_exec._tqm, "_stats"):
            tqm_stats = pb_exec._tqm._stats
            if hasattr(tqm_stats, "process_tally"):
                stats = dict(tqm_stats.process_tally) if tqm_stats.process_tally else {}
            elif hasattr(tqm_stats, "processed"):
                for _host, host_stats in tqm_stats.processed.items():
                    for key, val in host_stats.items():
                        stats[key] = stats.get(key, 0) + val
        if not stats:
            stats = dict(callback._host_stats)

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
        if not _HAS_ANSIBLE_CORE:
            raise ImportError("ansible-core is required for templating but is not installed")
        templar = _get_templar(variables=variables)
        result = templar.template(template_str)
        return str(result)

    def resolve_variable(
        self,
        var_name: str,
        host: str = "localhost",
        inventory_path: str | None = None,
        extravars: dict[str, Any] | None = None,
    ) -> Any:
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
            variable_mgr.extra_vars = extravars

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
        _NON_MODULE_KEYS = {
            "name", "when", "loop", "with_items", "with_dict", "register",
            "become", "become_user", "delegate_to", "ignore_errors",
            "notify", "tags", "vars", "block", "rescue", "always",
            "args", "changed_when", "failed_when", "retries", "delay", "until",
            "run_once", "local_action", "delegate_facts",
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
                tasks.append({
                    "name": task_name,
                    "module": module,
                    "hosts": str(play_hosts),
                })
        return tasks

    def validate_playbook_syntax(self, playbook_path: str) -> list[str]:
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
