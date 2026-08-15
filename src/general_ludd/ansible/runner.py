"""Ansible runner adapter module.

Delegates playbook execution to CoreAnsibleRunner which uses ansible-core
as a native Python library for playbook execution, variable resolution,
and Jinja2 templating.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from general_ludd.ansible.core_runner import CoreAnsibleRunner
from general_ludd.ansible.isolation import ProcessIsolationConfig
from general_ludd.ansible.paths import (
    activate_collection_version,
    resolve_collections_paths,
    to_ansible_env,
)
from general_ludd.ansible.unsafe import validate_extravars
from general_ludd.events.types import PlaybookRegisteredEvent
from general_ludd.security.sanitize import sanitize_job_id

logger = logging.getLogger(__name__)


def _resolve_playbooks_root() -> Path:
    candidate = Path(__file__).resolve().parent.parent.parent.parent / "playbooks"
    if candidate.is_dir():
        return candidate
    cwd = Path.cwd() / "playbooks"
    if cwd.is_dir():
        return cwd
    return candidate


_PLAYBOOKS_ROOT = _resolve_playbooks_root()

DEFAULT_REGISTRY: dict[str, str] = {}
if _PLAYBOOKS_ROOT.is_dir():
    for _f in sorted(_PLAYBOOKS_ROOT.glob("*.yml")):
        DEFAULT_REGISTRY[_f.name] = str(_f)
if not DEFAULT_REGISTRY:
    DEFAULT_REGISTRY["noop.yml"] = str(_PLAYBOOKS_ROOT / "noop.yml")


_LANGUAGE_ROLES_ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "language"
    / "roles"
)


def _build_registry(extra: dict[str, str] | None = None) -> dict[str, str]:
    reg = dict(DEFAULT_REGISTRY)
    if extra:
        reg.update(extra)
    return reg


def _convert_role_args(role: str, extra: dict[str, Any]) -> list[str]:
    if role in ("bom_detect", "encoding_detect"):
        if "file_path" in extra:
            return ["--input-file", str(extra["file_path"])]
    elif role == "font_analyze":
        if "file_path" in extra:
            return ["--input", str(extra["file_path"])]
    elif role in ("homoglyph_scan", "unicode_analyze"):
        if "text" in extra:
            return ["--input", str(extra["text"])]
    elif role == "i18n_extract":
        if "directory" in extra:
            source_dir = str(extra["directory"])
            if "output_dir" in extra:
                output_dir = str(extra["output_dir"])
            else:
                import tempfile
                import uuid

                output_dir = str(Path(tempfile.gettempdir()) / f"gludd-i18n-extract-{uuid.uuid4().hex[:12]}")
            return [
                "--source-dir",
                source_dir,
                "--output-dir",
                output_dir,
            ]
    elif role == "locale_format":
        if "locale" in extra:
            return ["--locale", str(extra["locale"])]
    elif role == "phonetic_transcribe":
        args: list[str] = []
        if "text" in extra:
            args.extend(["--input", str(extra["text"])])
        args.extend(["--method", "ipa"])
        return args
    return []


def _normalize_role_output(role: str, raw: dict[str, Any]) -> dict[str, Any]:
    if role == "bom_detect":
        raw["has_bom"] = raw.get("bom_detected", False)
        enc = raw.get("encoding")
        if enc:
            raw["encoding"] = str(enc).lower()
    elif role == "encoding_detect":
        if "detected_encoding" in raw:
            raw.setdefault("encoding", raw["detected_encoding"])
    elif role == "font_analyze":
        fmt = str(raw.get("format", "unknown"))
        if fmt in ("ttf", "otf", "ttc", "woff", "woff2"):
            fname = Path(str(raw.get("file", ""))).stem
            raw.setdefault("font_name", fname or fmt.upper())
        else:
            raw.setdefault("error", f"Unrecognized font format: {fmt}")
    elif role == "homoglyph_scan":
        findings = raw.get("findings", [])
        if not isinstance(findings, list):
            findings = []
        confusables = [f for f in findings if isinstance(f, dict) and f.get("type") == "confusable"]
        raw["confusable_count"] = len(confusables)
        raw["confusables"] = confusables
    elif role == "locale_format":
        locale_str = str(raw.get("locale", ""))
        codeset = ""
        locale_part = locale_str
        if "." in locale_str:
            locale_part, codeset = locale_str.rsplit(".", 1)
        parts = locale_part.replace("-", "_").split("_")
        raw["language"] = parts[0] if parts and parts[0] else ""
        raw["territory"] = parts[1] if len(parts) > 1 else ""
        if codeset:
            raw["codeset"] = codeset
    elif role == "phonetic_transcribe":
        words = raw.get("words", [])
        if not isinstance(words, list):
            words = []
        ipa_parts = [str(w.get("transcription", "")) for w in words if isinstance(w, dict)]
        raw["ipa"] = " ".join(ipa_parts)
    elif role == "unicode_analyze":
        raw["character_count"] = raw.get("input_length", 0)
        codepoints = raw.get("codepoints", [])
        if isinstance(codepoints, list) and codepoints and isinstance(codepoints[0], dict):
            first = codepoints[0]
            raw.setdefault("codepoint", first.get("codepoint"))
            raw.setdefault("name", first.get("name"))
            raw.setdefault("category", first.get("category"))
    return raw


class AnsibleRunnerAdapter:
    """Adapter wiring ansible-core into the daemon with registry + isolation."""

    def __init__(
        self,
        private_data_dir: str | None = None,
        registry: dict[str, str] | None = None,
        isolation_config: ProcessIsolationConfig | None = None,
        playbooks_dir: str | None = None,
        event_bus: Any | None = None,
        default_env: dict[str, str] | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        """Initialize the adapter with a private data dir and playbook registry."""
        self.private_data_dir = private_data_dir or tempfile.mkdtemp(prefix="gl-runner-")
        self.registry = _build_registry(registry)
        self.isolation_config = isolation_config
        self._playbooks_dir = playbooks_dir
        self._event_bus = event_bus
        self._default_env: dict[str, str] = default_env or {}
        self._project_root: Path | None = Path(project_root) if project_root else None
        self._collections_env: dict[str, str] = {}
        self._version_activation_roots: list[Path] = []
        self._version_cleanup_dirs: list[Path] = []
        self._refresh_collections_env()
        self._core_runner = CoreAnsibleRunner(
            process_isolation=isolation_config,
            private_data_dir=self.private_data_dir,
        )
        if playbooks_dir:
            self._scan_playbook_dir(playbooks_dir)

    def close(self) -> None:
        """Remove the private-data temp directory created by this adapter.

        Safe to call multiple times; subsequent calls are no-ops.
        After close(), the adapter should be discarded.
        """
        if self.private_data_dir and os.path.isdir(self.private_data_dir):
            shutil.rmtree(self.private_data_dir, ignore_errors=True)
            self.private_data_dir = ""

    def set_project_root(self, project_root: str | Path | None) -> None:
        """Update the active project root and re-resolve the collections env.

        Called by the daemon when the active project changes so that
        ``run_playbook`` invocations pick up the project-specific collections
        path (``<project_root>/.gludd/collections/``) without re-instantiating
        the adapter.
        """
        self._project_root = Path(project_root) if project_root else None
        self._refresh_collections_env()

    def _refresh_collections_env(self) -> None:
        """Resolve project/user/bundled collections paths into ANSIBLE_* env.

        Logs the resolved path order at INFO for operator visibility. Missing
        directories are silently skipped (a project may have no
        ``.gludd/collections/``).
        """
        entries = resolve_collections_paths(project_root=self._project_root)
        self._collections_env = to_ansible_env(entries)
        if entries:
            rendered = ", ".join(f"{e.source}:{e.path}" for e in entries)
            logger.info(
                "Resolved ansible collections search path (precedence high→low): %s",
                rendered,
            )

    def activate_collection(self, namespace: str, collection: str, version: str | None = None) -> Path:
        """Activate a specific collection version and return its root path."""
        base: Path | None = None
        proj_root = self._project_root
        if proj_root is not None:
            candidate = proj_root / ".gludd" / "collections"
            if candidate.is_dir():
                base = candidate
        if base is None:
            entries = resolve_collections_paths(project_root=self._project_root)
            for entry in entries:
                if entry.path.is_dir():
                    base = entry.path
                    break
        if base is None:
            raise FileNotFoundError(f"No collections directory found for {namespace}.{collection}")
        root, cleanup = activate_collection_version(base, namespace=namespace, collection=collection, version=version)
        if cleanup is not None and cleanup not in self._version_cleanup_dirs:
            self._version_cleanup_dirs.append(cleanup)
        self._version_activation_roots.append(root)
        return root

    def clear_collection_versions(self) -> None:
        """Remove every version activation root and cleanup dir created."""
        for d in self._version_cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)
        self._version_cleanup_dirs.clear()
        self._version_activation_roots.clear()

    def resolve_playbook(self, playbook_name: str) -> str:
        """Return the file path registered for a playbook name."""
        if playbook_name not in self.registry:
            raise ValueError(f"Playbook '{playbook_name}' is not registered")
        return self.registry[playbook_name]

    def prepare_job_dirs(self, job_id: str) -> dict[str, str]:
        """Create the per-job workspace directories under the private data dir."""
        safe_id = sanitize_job_id(job_id)
        if safe_id is None:
            raise ValueError(f"Invalid job_id: {job_id!r}")
        job_dir = os.path.join(self.private_data_dir, safe_id)
        dirs = {
            "root": job_dir,
            "env": os.path.join(job_dir, "env"),
            "project": os.path.join(job_dir, "project"),
            "inventory": os.path.join(job_dir, "inventory"),
            "artifacts": os.path.join(job_dir, "artifacts"),
        }
        try:
            os.makedirs(job_dir, exist_ok=False)
        except FileExistsError as exc:
            raise FileExistsError(
                f"Job workspace already exists for job_id={job_id!r} "
                f"(dir={job_dir}). Refusing to overwrite an existing job workspace."
            ) from exc
        for d in (v for k, v in dirs.items() if k != "root"):
            os.makedirs(d, exist_ok=True)
        return dirs

    def write_vars(
        self,
        job_id: str,
        job_vars: dict[str, Any],
        shared_vars: dict[str, Any] | None = None,
        filename: str = "extravars",
    ) -> str:
        """Write validated extravars to the per-job env dir (mode 0600)."""
        # job_id is attacker-controllable (JobSpec.job_id from the HTTP body) and
        # only whitespace-validated upstream. Sanitize here too — write_vars is a
        # public method reachable independently of prepare_job_dirs — so a crafted
        # id like "../../etc" cannot escape the per-job workspace. Mirrors
        # prepare_job_dirs above.
        safe_id = sanitize_job_id(job_id)
        if safe_id is None:
            raise ValueError(f"Invalid job_id: {job_id!r}")
        payload: dict[str, Any] = {"job_vars": job_vars}
        if shared_vars is not None:
            payload["shared_vars"] = shared_vars
        payload = validate_extravars(payload)
        vars_dir = os.path.join(self.private_data_dir, safe_id, "env")
        os.makedirs(vars_dir, exist_ok=True)
        path = os.path.join(vars_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, default_flow_style=False)
        os.chmod(path, 0o600)
        return path

    def run_playbook(
        self,
        playbook_name: str,
        private_data_dir: str | None = None,
        extravars: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        **runner_kwargs: Any,
    ) -> dict[str, Any]:
        """Run a registered playbook with a finite timeout and merged env."""
        try:
            playbook_path = self.resolve_playbook(playbook_name)
        except ValueError as exc:
            return {"status": "failed", "rc": 1, "error": str(exc), "events": []}
        _pdd = private_data_dir or self.private_data_dir
        # HIGH (global env mutation): do NOT mutate os.environ. Pass caller-
        # supplied env overrides as extra_env to core_runner, which merges them
        # into the scrubbed allowlist env immediately before pb_exec.run() and
        # restores os.environ unconditionally in a finally block. This keeps the
        # gludd process env pristine across concurrent playbook invocations.
        try:
            # Network-exposed path: ALWAYS bound the run with a FINITE timeout so
            # a runaway/sleeping playbook can never hang the worker. When the
            # caller does not specify one, resolve the env/default bound here
            # (GLUDD_PLAYBOOK_TIMEOUT, default 300s) so a finite value is always
            # passed down — never None (which would run unbounded inline).
            from general_ludd.ansible.core_runner import _env_default_timeout

            effective_timeout = _env_default_timeout() if timeout is None else timeout
            # Merge precedence: caller-supplied env (highest) > collections-path
            # env (resolved from project_root) > per-adapter default_env. The
            # collections env is rebuilt on project-root changes via
            # set_project_root / _refresh_collections_env.
            _merged_env = {
                **self._default_env,
                **self._collections_env,
                **(env or {}),
            }
            if self._version_activation_roots:
                activation_paths = os.pathsep.join(str(r) for r in self._version_activation_roots)
                existing_cp = _merged_env.get("ANSIBLE_COLLECTIONS_PATH", "")
                _merged_env["ANSIBLE_COLLECTIONS_PATH"] = (
                    activation_paths + os.pathsep + existing_cp if existing_cp else activation_paths
                )
                existing_rp = _merged_env.get("ANSIBLE_ROLES_PATH", "")
                _merged_env["ANSIBLE_ROLES_PATH"] = (
                    activation_paths + os.pathsep + existing_rp if existing_rp else activation_paths
                )
            result = self._core_runner.run_playbook(
                playbook_path=playbook_path,
                # Do not evaluate truthiness on this untrusted mapping: a dict
                # subclass can override __bool__/__len__. CoreAnsibleRunner's
                # strict validator will reject non-exact built-in structures.
                extravars={} if extravars is None else extravars,
                timeout=effective_timeout,
                extra_env=_merged_env or None,
            )
            return result.model_dump()
        except Exception as exc:
            logger.error("Ansible core runner failed: %s", exc)
            return {"status": "failed", "rc": 1, "error": str(exc), "events": []}

    async def run_role(self, task_args: dict[str, Any]) -> dict[str, Any]:
        """Execute a language role script with a 30s timeout and parse its JSON."""
        role = str(task_args.get("role", ""))
        if not role:
            return {"error": "No 'role' specified in task_args"}

        script_path = _LANGUAGE_ROLES_ROOT / role / "files" / f"{role}.py"
        if not script_path.is_file():
            return {"error": f"Role script not found: {script_path}"}

        extra = {k: v for k, v in task_args.items() if k not in ("collection", "role")}
        cli_args = _convert_role_args(role, extra)
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        cmd = [sys.executable, str(script_path), *cli_args]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(repo_root),
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            stdout_text = stdout_b.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_b.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                return {
                    "error": f"Role '{role}' exited {proc.returncode}",
                    "stderr": stderr_text[:500],
                }

            if not stdout_text:
                return {"error": f"Role '{role}' produced no output"}

            raw = json.loads(stdout_text)
            if not isinstance(raw, dict):
                return {"error": f"Role '{role}' returned non-dict JSON"}
            return _normalize_role_output(role, raw)
        except TimeoutError:
            return {"error": f"Role '{role}' timed out after 30s"}
        except json.JSONDecodeError as exc:
            return {"error": f"Invalid JSON from role '{role}': {exc}"}
        except Exception as exc:
            logger.error("run_role failed for %s: %s", role, exc)
            return {"error": str(exc)}

    def refresh_playbooks(self) -> dict[str, Any]:
        """Rescan the playbooks directory and return registered names."""
        if self._playbooks_dir:
            self._scan_playbook_dir(self._playbooks_dir)
        return {"playbooks": list(self.registry.keys())}

    def register_playbook(self, name: str, path: str) -> None:
        """Register a playbook path under a name and publish the event."""
        self.registry[name] = path
        if self._event_bus:
            self._event_bus.publish(PlaybookRegisteredEvent(playbook=name))

    def unregister_playbook(self, name: str) -> None:
        """Remove a playbook from the registry (missing names are no-ops)."""
        self.registry.pop(name, None)

    def list_playbooks(self) -> list[str]:
        """Return the names of all registered playbooks."""
        return list(self.registry.keys())

    def _scan_playbook_dir(self, playbooks_dir: str) -> None:
        pdir = Path(playbooks_dir)
        if pdir.is_dir():
            for f in sorted(pdir.glob("*.yml")):
                self.registry[f.name] = str(f)
                if self._event_bus:
                    from general_ludd.events.types import PlaybookRegisteredEvent

                    self._event_bus.publish(PlaybookRegisteredEvent(playbook=f.name))
