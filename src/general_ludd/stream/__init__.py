"""Server-side stream dispatch artifact builder.

``RoleCloner`` materializes a clone of an Ansible role under a per-chunk work
directory and emits the wrapper artifacts needed to run that clone against a
stream chunk:

  * ``clone-overrides.json`` — the per-chunk vars injected via ``vars_files``.
  * ``run-clone.yml``        — a wrapper playbook that includes the cloned
                                role with inline ``vars:`` from the overrides
                                plus a ``vars_files`` reference.
  * ``process-chunk.sh`` / ``wait-for-agent-result.yml`` — the external
                                processor shim for the requested tool kind.

The cloner is hermetic: no network, no daemon, no ansible invocation. The
router (``general_ludd.routers.stream``) drives it and (optionally) enqueues
the cloned role for execution via the existing job queue.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import uuid
from pathlib import Path
from typing import Any

from general_ludd.security.state import project_state, secure_directory

__all__ = [
    "SUPPORTED_PROCESSOR_TOOLS",
    "_FORBIDDEN_SHELL_CHARS",
    "_SAFE_BINARY_RE",
    "RoleCloner",
    "project_state",
    "secure_directory",
]

# Supported external processor tool kinds. Unknown tool kinds raise ValueError.
SUPPORTED_PROCESSOR_TOOLS: frozenset[str] = frozenset({"whisper.cpp", "ffmpeg", "agent"})

_SAFE_BINARY_RE = re.compile(r"^(?!.*\.\.)[a-zA-Z0-9_./-]+$")

_FORBIDDEN_SHELL_CHARS: frozenset[str] = frozenset("`$();|&<>!\n\r'\"\\\0")

_ARG_FORBIDDEN_CHARS: frozenset[str] = frozenset("`$();|&!<>\n\r\0")


def _parse_processor_args(raw: str) -> list[str]:
    if not raw.strip():
        return []
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise ValueError(f"processor args {raw!r} contain malformed shell quoting") from exc
    for token in tokens:
        if "\0" in token:
            raise ValueError("processor arg contains null byte")
        if "\n" in token or "\r" in token:
            raise ValueError("processor arg contains newline/carriage-return")
        for ch in token:
            if ch in _ARG_FORBIDDEN_CHARS:
                raise ValueError(f"processor arg {token!r} contains forbidden shell character {ch!r}")
    return tokens


class RoleCloner:
    """Copy a role into a fresh per-clone work directory and emit run wrappers."""

    def __init__(
        self,
        collection_root: Path,
        work_root: Path | None = None,
    ) -> None:
        """Initialize the cloner with a collection root and optional work root."""
        self.collection_root = Path(collection_root)
        self.work_root = (
            secure_directory(work_root)
            if work_root is not None
            else project_state(project_root=self.collection_root).directory("stream-clones")
        )

    def clone(self, role_name: str, overrides: dict[str, Any]) -> Path:
        """Copy ``roles/<role_name>/`` to a fresh per-clone dir and emit wrappers.

        Parameters
        ----------
        role_name:
            The role directory name under ``roles/``.
        overrides:
            Per-chunk variables serialized to ``clone-overrides.json`` and
            threaded into the wrapper playbook as inline ``vars:``.

        Returns the clone directory ``Path``. Raises ``FileNotFoundError`` when
        the requested role is not present under ``collection_root/roles/``.
        Raises ``ValueError`` when ``role_name`` resolves outside
        ``collection_root/roles/`` (path-traversal confinement — defense in
        depth behind the router's own identifier validation).
        """
        roles_root = (self.collection_root / "roles").resolve()
        src = (roles_root / role_name).resolve()
        try:
            src.relative_to(roles_root)
        except ValueError:
            raise ValueError(f"Role {role_name!r} resolves outside {roles_root}") from None
        if not src.is_dir():
            raise FileNotFoundError(f"Role {role_name!r} not found under {roles_root}")
        secure_directory(self.work_root)
        clone_path = self.work_root / f"{role_name}-{uuid.uuid4().hex}"
        src = src.resolve()
        roles_root = (self.collection_root / "roles").resolve()
        try:
            src.relative_to(roles_root)
        except ValueError:
            raise ValueError(f"Role {role_name!r} resolves outside {roles_root}") from None
        shutil.copytree(src, clone_path)
        secure_directory(clone_path)

        (clone_path / "clone-overrides.json").write_text(json.dumps(overrides, indent=2, sort_keys=True))

        wrapper_lines = [
            "---",
            "# Auto-generated wrapper playbook: run the cloned role with injected vars.",
            "- name: Run cloned role for stream chunk",
            "  hosts: localhost",
            "  gather_facts: false",
            "  vars:",
        ]
        for key, value in (overrides or {}).items():
            wrapper_lines.append(f"    {key}: {json.dumps(value)}")
        wrapper_lines.extend(
            [
                "  vars_files:",
                "    - clone-overrides.json",
                "  roles:",
                f"    - role: {role_name}",
            ]
        )
        (clone_path / "run-clone.yml").write_text("\n".join(wrapper_lines) + "\n")
        return clone_path

    def materialize_processor(self, clone_path: Path, processor: dict[str, Any]) -> Path:
        """Emit the external-processor shim for the requested tool kind.

        * ``whisper.cpp`` / ``ffmpeg`` — writes ``process-chunk.sh`` invoking
          the configured binary with ``$CHUNK_PATH``.
        * ``agent`` — writes ``wait-for-agent-result.yml``, a poll-style
          playbook that waits for an external result (it does NOT call the
          TodoRepository; it only produces the wait-playbook file).
        """
        tool = processor.get("tool")
        if tool == "whisper.cpp":
            return self._write_shell_processor(clone_path, processor, kind="whisper")
        if tool == "ffmpeg":
            return self._write_shell_processor(clone_path, processor, kind="ffmpeg")
        if tool == "agent":
            return self._write_agent_wait_playbook(clone_path, processor)
        raise ValueError(f"Unknown processor tool {tool!r}; expected one of {sorted(SUPPORTED_PROCESSOR_TOOLS)}")

    @staticmethod
    def _write_shell_processor(clone_path: Path, processor: dict[str, Any], *, kind: str) -> Path:
        binary = processor.get("binary", kind)
        extra_args_raw = processor.get("args", "")
        binary_str = str(binary)
        if not _SAFE_BINARY_RE.match(binary_str):
            raise ValueError(f"processor binary {binary!r} contains unsafe characters; expected [a-zA-Z0-9_./-]+")
        if binary_str.startswith("-"):
            raise ValueError(
                f"processor binary {binary!r} starts with '-'; leading-dash may be interpreted as an exec flag"
            )
        extra_args_list = _parse_processor_args(str(extra_args_raw))
        out_path = clone_path / "process-chunk.sh"
        quoted_binary = shlex.quote(binary_str)
        quoted_args = [shlex.quote(a) for a in extra_args_list]
        lines = [
            "#!/usr/bin/env bash",
            f"# Auto-generated {kind} chunk processor.",
            "set -euo pipefail",
            "# CHUNK_PATH is the stream chunk file to process; $1 is a fallback.",
            'if [ -z "${CHUNK_PATH:-}" ]; then',
            '  CHUNK_PATH="$1"',
            "fi",
            f"PROCESSOR_BINARY={quoted_binary}",
        ]
        if quoted_args:
            lines.append(f"PROCESSOR_ARGS=({' '.join(quoted_args)})")
        else:
            lines.append("PROCESSOR_ARGS=()")
        lines.append('exec "${PROCESSOR_BINARY}" "${PROCESSOR_ARGS[@]}" -- "$CHUNK_PATH"')
        out_path.write_text("\n".join(lines) + "\n")
        out_path.chmod(0o755)
        return out_path

    @staticmethod
    def _write_agent_wait_playbook(clone_path: Path, processor: dict[str, Any]) -> Path:
        out_path = clone_path / "wait-for-agent-result.yml"
        topic = processor.get("result_topic", "stream/agent-result")
        max_polls = int(processor.get("max_polls", 60))
        poll_interval = int(processor.get("poll_interval_seconds", 5))
        lines = [
            "---",
            "# Auto-generated wait playbook: poll for an external agent result.",
            "# This playbook does NOT call the daemon's TodoRepository directly;",
            "# it loops until the result file appears on disk (populated by an",
            "# external agent run) or the poll budget is exhausted.",
            "- name: Wait for agent result for stream chunk",
            "  hosts: localhost",
            "  gather_facts: false",
            "  vars:",
            f"    result_topic: {json.dumps(topic)}",
            f"    max_polls: {max_polls}",
            f"    poll_interval_seconds: {poll_interval}",
            "  tasks:",
            "    - name: Poll for result file",
            "      ansible.builtin.stat:",
            "        path: \"{{ lookup('env', 'GLUDD_AGENT_RESULT_FILE') | default('agent-result.json', true) }}\"",
            "      register: result_stat",
            '      loop: "{{ range(1, max_polls + 1) | list }}"',
            "      until: result_stat.stat.exists",
            '      delay: "{{ poll_interval_seconds }}"',
            '      retries: "{{ max_polls }}"',
        ]
        out_path.write_text("\n".join(lines) + "\n")
        return out_path
