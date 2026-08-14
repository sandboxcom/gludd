"""MCP stdio client transport — hand-rolled, kept as-is.

KEEP LIST (V3.2): Both named protocol bugs are fixed in this file:
  - transport.py:52 matches responses by ``id`` to guard against interleaving.
  - transport.py:98 sends ``notifications/initialized`` after handshake.
The official ``mcp`` Python SDK is NOT a declared dependency; adopting it would
add a heavy transitive closure for marginal benefit. The two-bug rationale from
guide 2 no longer applies (both fixed). Decision: keep this 125-LOC client
until a concrete need for SDK features (e.g., sampling, roots, auth) arises.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import os
import re
import shutil
from collections import deque
from typing import Any, cast

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.exceptions import MCPTransportError
from general_ludd.mcp.registry import MCPTool
from general_ludd.security.sanitize import sanitize_error_message

logger = logging.getLogger(__name__)

__all__ = ("MCPStdioClient", "MCPTransportError")

# Package managers / runtimes that are explicitly permitted as MCP launchers.
# Anything not on this list is rejected by default (operator can opt out via
# GLUDD_MCP_ALLOW_ANY_EXEC=1).
_MCP_EXEC_ALLOWLIST = frozenset({"npx", "npm", "pnpm", "yarn", "bunx", "uvx", "python", "python3", "node"})

# Package managers that fetch-and-run code from a remote index.
# D8: bunx added here so _REMOTE_FETCH_LAUNCHERS includes it and the pin gate fires.
_NPM_FAMILY_LAUNCHERS = frozenset({"npx", "npm", "pnpm", "yarn", "bunx"})
# pip-style runners that also download from a remote index.
_UVX_FAMILY_LAUNCHERS = frozenset({"uvx"})
# All launchers that need package-spec injection validation.
_REMOTE_FETCH_LAUNCHERS = _NPM_FAMILY_LAUNCHERS | _UVX_FAMILY_LAUNCHERS

# Version-pin spec: package name followed by ==VERSION or @VERSION.
# Bare names, ranges (>=, <=, ~=), and globs (==1.*, ==2.*) are rejected.
_UVX_VERSION_PINNED_RE = re.compile(r"^[^<>=!~*]+(?:==[\w.+\-]+|@[\w.+\-]+)$")


def _is_uvx_version_pinned_spec(spec: str) -> bool:
    return bool(_UVX_VERSION_PINNED_RE.match(spec))


# Shell metacharacters that must never appear in a package spec or binary name
# passed to a remote-fetch launcher. These would be harmless in exec()-land
# (no shell expansion), but their presence strongly suggests an injection
# attempt — refuse early rather than relying on the exec layer to be safe.
_SHELL_META_RE = re.compile(r"[;&|$`\\<>()\s]")

# Remote-fetch launchers accept flags that can re-root package/config discovery.
# An MCP config must not redirect resolution into an attacker-controlled local
# tree while presenting a harmless pinned package later in argv.  The union is
# deliberate: launcher versions expose overlapping spellings, and rejecting an
# irrelevant flag is safer than letting a newly-supported alias bypass policy.
_REMOTE_FETCH_DIRECTORY_REDIRECT_FLAGS = frozenset(
    {
        "-C",
        "-w",
        "--config-file",
        "--cwd",
        "--directory",
        "--include-workspace-root",
        "--prefix",
        "--project",
        "--workspace",
        "--workspaces",
    }
)
_INLINE_FLAG_VALUE_RE = re.compile(r"^(-[^=]+)=(.*)$", re.DOTALL)

# Python-family launchers (module/script runtimes, no remote fetch).
_PYTHON_FAMILY_LAUNCHERS = frozenset({"python", "python3"})
# Node-family launchers (script runtime, no remote fetch).
_NODE_FAMILY_LAUNCHERS = frozenset({"node"})
# All non-remote-fetch launchers that still need argv validation.
_LOCAL_RUNTIME_LAUNCHERS = _PYTHON_FAMILY_LAUNCHERS | _NODE_FAMILY_LAUNCHERS

# Path-traversal pattern: two or more "../" segments, or an absolute path
# outside the repo. exec()-land prevents shell expansion, but a path pointing
# outside the project is an attempted jailbreak — refuse it.
_PATH_TRAVERSAL_RE = re.compile(r"(?:^|/)\.\.[/\\]")


def _strip_suffix(name: str) -> str:
    """Remove a Windows-style .cmd/.exe/.bat/.ps1 suffix from an executable name."""
    return re.sub(r"\.(cmd|exe|bat|ps1)$", "", name)


# Pattern for a concrete npm version pin: ``pkg@<number>`` or ``pkg@<n.n.n>``
# (including pre-release and build metadata). Rejects ``@latest``, ``@^1.x``,
# ``@~1.0`` and any other range or tag specifiers so only exact versions pass.
_VERSION_PINNED_RE = re.compile(
    r"@"  # version separator
    r"(?!latest$|next$)"  # NOT the special "latest"/"next" dist-tags
    r"\d"  # must start with a digit (not ^, ~, >, <, =, *, etc.)
    r"[0-9a-zA-Z.\-+]*$"  # allow semver-compat chars (pre-release + build meta)
)


def _is_version_pinned_spec(spec: str) -> bool:
    """Return True iff ``spec`` is a concretely version-pinned npm package spec.

    Accepts:
    * ``pkg@1.2.3`` — simple semver pin
    * ``@scope/pkg@2026.1.26`` — scoped package with date-based version
    * ``pkg@2`` — major-only pin (still concrete, not a range)

    Rejects:
    * ``pkg`` — bare name (no version)
    * ``@scope/pkg`` — scoped, no version
    * ``pkg@latest`` / ``pkg@next`` — dist-tags (float, not pinned)
    * ``pkg@^1.0.0`` / ``pkg@~1.0.0`` / ``pkg@>=1.0`` — semver ranges
    """
    # The last '@' (after stripping a leading '@' for scoped packages) is the
    # version separator for npm. For scoped packages like ``@scope/pkg@ver`` the
    # last '@' is the right one; for plain ``pkg@ver`` it's the only one.
    if not spec:
        return False
    # Strip a leading '@' that's part of the scope, then look for the LAST '@'.
    remainder = spec.lstrip("@")
    at_pos = remainder.rfind("@")
    if at_pos < 0:
        # No version separator at all.
        return False
    version_part = remainder[at_pos:]  # includes the leading '@'
    return bool(_VERSION_PINNED_RE.match(version_part))


def _launcher_basename(cmd0: str) -> str:
    """Normalised (lowercase, suffix-stripped) basename of argv[0]."""
    return _strip_suffix(os.path.basename(cmd0).lower())


def _validate_launch_command(cmd: list[str]) -> None:
    """Validate ``cmd`` before spawning an MCP subprocess.

    Fail closed on any policy violation.

    Checks (in order):
    1. Empty argv → MCPTransportError("empty …")
    2. Executable basename must be on the allowlist (or GLUDD_MCP_ALLOW_ANY_EXEC=1).
    3. Executable must resolve via ``shutil.which`` or be an existing absolute path.
    4. For remote-fetch launchers (npx/npm/pnpm/yarn/uvx): every non-flag token
       that acts as a package spec must not contain shell metacharacters.  An arg
       that consists ENTIRELY of ``-…`` tokens (no package spec at all) is also
       rejected, since a bare ``npx --some-flag`` with no package name is
       semantically broken and likely injection.  Additionally, npm-family specs
       must be version-pinned for supply-chain safety.
    5. For local-runtime launchers (python/python3/node): ``-c``/``-e``/``-p``
       code-execution flags are rejected, module/script paths are checked for
       path-traversal and shell metacharacters, and at least one module or script
       argument is required (C27).
    """
    if not cmd:
        raise MCPTransportError("Refusing to spawn MCP subprocess: empty command (argv is empty).")

    launcher = _launcher_basename(cmd[0])
    allow_any = os.environ.get("GLUDD_MCP_ALLOW_ANY_EXEC", "").strip() in {
        "1",
        "true",
        "yes",
    }

    if not allow_any and launcher not in _MCP_EXEC_ALLOWLIST:
        raise MCPTransportError(
            f"Executable {cmd[0]!r} (basename {launcher!r}) is not in the MCP "
            f"executable allowlist {sorted(_MCP_EXEC_ALLOWLIST)}. Set "
            "GLUDD_MCP_ALLOW_ANY_EXEC=1 to opt out of this check."
        )

    # Resolve the executable — accept absolute paths that exist on disk.
    resolved = shutil.which(cmd[0])
    if resolved is None and not os.path.isfile(cmd[0]):
        raise MCPTransportError(
            f"MCP executable {cmd[0]!r} could not be resolved on PATH and is "
            "not an existing absolute path.  Check the MCP server config."
        )

    # Package-spec injection guard for remote-fetch launchers.
    if launcher in _REMOTE_FETCH_LAUNCHERS:
        _validate_package_spec(cmd, launcher)

    # Argv validation for local-runtime launchers (python/python3/node).
    if launcher in _LOCAL_RUNTIME_LAUNCHERS:
        _validate_python_node_argv(cmd, launcher)


# JS npm-family and uvx launchers whose package spec MUST be version-pinned
# (a mutable dist-tag / range / bare name is a supply-chain substitution risk).
# D8: _NPM_FAMILY_LAUNCHERS is defined once at module top (includes bunx).
# H.10: _UVX_FAMILY_LAUNCHERS uses ==X.Y.Z / @X.Y.Z pin via _is_uvx_version_pinned_spec.


def _validate_package_spec(cmd: list[str], launcher: str) -> None:
    """Validate the package spec argument(s) for a remote-fetch launcher.

    Raises MCPTransportError if:
    - The first non-flag argument (the package spec) contains shell metacharacters.
    - There are ONLY flag arguments (no package spec at all — likely injection).
    - A value passed via --package / -p (flags that supply a package spec to
      npx/npm/pnpm/yarn/bunx) is not version-pinned or contains metacharacters.
      These flags bypass the original positional-arg check and represent a
      supply-chain substitution risk identical to an unpinned positional spec.
    """
    # Flags that accept a following argument which is itself a package spec.
    # npx supports both ``--package pkg`` and ``-p pkg``; pnpm/yarn follow the
    # same convention.  Values supplied this way must pass the same checks as
    # the positional package spec.
    _PACKAGE_VALUE_FLAGS = frozenset({"--package", "-p"})

    args_after_launcher = cmd[1:]
    found_spec = False
    # True once a package spec has been supplied via --package/-p (space or
    # inline form). After that, the next bare positional is the BINARY to run
    # from the already-pinned package (e.g. ``npx --package pkg@1.2.3 some-cmd``)
    # — NOT another package spec to fetch — so it must not be re-validated.
    spec_from_flag = False

    def _check_flag(arg: str) -> None:
        """Reject option values that bypass pinned-package validation."""
        flag_name = arg.split("=", 1)[0]
        # Compact short form, e.g. ``-C/tmp/project``.
        if arg.startswith("-C") and not arg.startswith("--"):
            flag_name = "-C"
        if flag_name in _REMOTE_FETCH_DIRECTORY_REDIRECT_FLAGS:
            raise MCPTransportError(
                f"MCP flag {flag_name!r} for launcher {launcher!r} is a "
                "directory-redirect flag. Re-rooting package or config "
                "discovery is not permitted in MCP server configs."
            )

        match = _INLINE_FLAG_VALUE_RE.match(arg)
        if match is None:
            return
        value = match.group(2)
        if "@" in value or _SHELL_META_RE.search(value):
            raise MCPTransportError(
                f"MCP flag value for {match.group(1)!r} on launcher {launcher!r} embeds "
                "a package spec or shell metacharacters and is refused."
            )

    def _check_spec(arg: str) -> None:
        """Apply metacharacter and version-pin checks to a single spec."""
        if _SHELL_META_RE.search(arg):
            raise MCPTransportError(
                f"MCP package spec {arg!r} for launcher {launcher!r} contains "
                "shell metacharacters. This looks like an injection attempt and "
                "is refused."
            )
        if launcher in _NPM_FAMILY_LAUNCHERS and not _is_version_pinned_spec(arg):
            raise MCPTransportError(
                f"MCP package spec {arg!r} for launcher {launcher!r} is not "
                "version-pinned (bare name, dist-tag, or range). Pin it to a "
                "concrete version (e.g. pkg@1.2.3) — refused for supply-chain safety."
            )
        if launcher in _UVX_FAMILY_LAUNCHERS and not _is_uvx_version_pinned_spec(arg):
            raise MCPTransportError(
                f"MCP package spec {arg!r} for launcher {launcher!r} is not "
                "version-pinned (bare name, range, or glob). Pin it to a "
                "concrete version (e.g. pkg==1.2.3 or pkg@1.2.3) — refused for supply-chain safety."
            )

    i = 0
    while i < len(args_after_launcher):
        arg = args_after_launcher[i]
        if arg in _PACKAGE_VALUE_FLAGS:
            # The next token is the package spec supplied via this flag.
            if i + 1 >= len(args_after_launcher):
                raise MCPTransportError(
                    f"MCP launcher {launcher!r}: flag {arg!r} requires a "
                    "following package-spec argument but none was found."
                )
            spec = args_after_launcher[i + 1]
            _check_spec(spec)
            found_spec = True
            spec_from_flag = True
            i += 2
            continue
        if arg.startswith("--package=") or arg.startswith("-p="):
            # ``--package=pkg@1.2.3`` inline form.
            spec = arg.split("=", 1)[1]
            _check_spec(spec)
            found_spec = True
            spec_from_flag = True
            i += 1
            continue
        if arg.startswith("-"):
            _check_flag(arg)
        if not arg.startswith("-"):
            if spec_from_flag:
                # A pinned package was already supplied via --package/-p, so
                # this bare positional is the command to execute FROM that
                # package (e.g. ``npx --package pkg@1.2.3 some-cmd``). It is
                # not a package spec to fetch — do not re-validate it.
                break
            # First positional non-flag arg is the package spec.
            _check_spec(arg)
            found_spec = True
            # Remaining args are arguments forwarded to the server binary, not
            # package specs — stop scanning here.
            break
        i += 1

    if not found_spec:
        # No non-flag arg found — no package spec supplied.
        raise MCPTransportError(
            f"MCP launcher {launcher!r} has no package spec argument (only flags "
            "were found). Provide a package name to fetch/run."
        )


# Flags that imply arbitrary code execution from the command line — never
# passable through an MCP config.
_PYTHON_CODE_EXEC_FLAGS = frozenset({"-c"})
_NODE_CODE_EXEC_FLAGS = frozenset({"-e", "-p"})


def _validate_python_node_argv(cmd: list[str], launcher: str) -> None:
    """Validate argv for python/python3/node launchers.

    These launchers do NOT fetch packages from a remote index, but they CAN
    execute arbitrary code via ``-c``/``-e``/``-p`` flags or run scripts
    outside the project tree. This function:

    * Rejects ``-c`` (python) — arbitrary code execution.
    * Rejects ``-e`` / ``-p`` (node) — arbitrary code evaluation.
    * Rejects path-traversal patterns (``../``) in module/script arguments.
    * Rejects shell metacharacters in module/script arguments.
    * Requires at least one module or script argument (bare-flag-only argv is
      semantically broken and likely injection).
    """
    code_exec_flags = _PYTHON_CODE_EXEC_FLAGS if launcher in _PYTHON_FAMILY_LAUNCHERS else _NODE_CODE_EXEC_FLAGS

    args_after_launcher = cmd[1:]
    if not args_after_launcher:
        raise MCPTransportError(
            f"MCP launcher {launcher!r} requires a module name or script path but none was provided."
        )

    found_module_or_script = False

    for arg in args_after_launcher:
        if arg in code_exec_flags:
            raise MCPTransportError(
                f"Refusing MCP launcher {launcher!r}: {arg!r} flag is arbitrary "
                "code execution and is forbidden in MCP configs."
            )

        if arg.startswith("-"):
            # Safe flags (e.g. -u, -B, -I for python; --no-warnings for node)
            # are skipped.
            continue

        # This is a positional argument — a module name or script path.
        found_module_or_script = True

        if _PATH_TRAVERSAL_RE.search(arg):
            raise MCPTransportError(
                f"MCP launcher {launcher!r}: argument {arg!r} contains path "
                "traversal (../). Script and module paths must be jailed to "
                "the project tree."
            )

        if _SHELL_META_RE.search(arg):
            raise MCPTransportError(
                f"MCP launcher {launcher!r}: argument {arg!r} contains shell "
                "metacharacters. This looks like an injection attempt and is "
                "refused."
            )

        # Stop at the first positional arg — everything after is forwarded to
        # the server binary, not a path to validate.
        break

    if not found_module_or_script:
        raise MCPTransportError(
            f"MCP launcher {launcher!r} has no module or script argument "
            "(only flags were found). Provide a module name (e.g. -m my_server) "
            "or script path."
        )


# Cap on how many non-matching (interleaved) JSON-RPC frames we will skip while
# waiting for our request's response before giving up. Bounds the read loop so a
# misbehaving server that streams unrelated frames can never spin it forever
# (defense in depth alongside the per-read timeout). Finding 5.
_MAX_INTERLEAVE_SKIPS = 100

# D-24: stderr is drained concurrently so a noisy MCP server cannot fill its
# pipe and deadlock.  These defaults retain a small redacted diagnostic tail,
# while the larger policy limits cap the total work accepted from one server.
# Every value is operator-configurable (constructor keyword or environment),
# but a hard safety ceiling prevents a typo from turning diagnostics into an
# unbounded-memory or unbounded-I/O path.
_STDERR_READ_CHUNK_BYTES = 4096
_STDERR_DEFAULT_TAIL_BYTES = 16 * 1024
_STDERR_DEFAULT_TAIL_LINES = 128
_STDERR_DEFAULT_LINE_BYTES = 8 * 1024
_STDERR_DEFAULT_MAX_BYTES = 1024 * 1024
_STDERR_DEFAULT_MAX_LINES = 10_000
_STDERR_LIMIT_CEILINGS = {
    "tail_bytes": 1024 * 1024,
    "tail_lines": 4096,
    "line_bytes": 64 * 1024,
    "max_bytes": 64 * 1024 * 1024,
    "max_lines": 1_000_000,
}
_STDERR_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Minimal base environment handed to every MCP subprocess. The full host
# environment (which includes ANTHROPIC_API_KEY, GLUDD_PSK, cloud creds, etc.)
# is NEVER inherited — only these process-hygiene vars plus the server's own
# declared `env`/resolved secrets are passed. Finding 2.
_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


def _stderr_limit(
    explicit: int | None,
    *,
    env_name: str,
    default: int,
    limit_name: str,
) -> int:
    """Resolve and validate one bounded stderr policy setting."""
    raw: object = explicit if explicit is not None else os.environ.get(env_name, default)
    if isinstance(raw, bool):
        raise MCPTransportError(f"{env_name} must be a positive integer")
    try:
        value = int(cast("str | int", raw))
    except (TypeError, ValueError) as exc:
        raise MCPTransportError(f"{env_name} must be a positive integer") from exc
    ceiling = _STDERR_LIMIT_CEILINGS[limit_name]
    if value <= 0 or value > ceiling:
        raise MCPTransportError(f"{env_name} must be between 1 and {ceiling}, got {value}")
    return value


class MCPStdioClient:
    """Manages a single MCP server subprocess via stdio JSON-RPC."""

    def __init__(
        self,
        config: MCPServerConfig,
        secrets_mgr: Any = None,
        *,
        stderr_tail_bytes: int | None = None,
        stderr_tail_lines: int | None = None,
        stderr_line_bytes: int | None = None,
        stderr_max_bytes: int | None = None,
        stderr_max_lines: int | None = None,
    ) -> None:
        """Initialize transport state and bounded stderr policies."""
        self._config = config
        self._secrets_mgr = secrets_mgr
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._stderr_tail_bytes_limit = _stderr_limit(
            stderr_tail_bytes,
            env_name="GLUDD_MCP_STDERR_TAIL_BYTES",
            default=_STDERR_DEFAULT_TAIL_BYTES,
            limit_name="tail_bytes",
        )
        self._stderr_tail_lines_limit = _stderr_limit(
            stderr_tail_lines,
            env_name="GLUDD_MCP_STDERR_TAIL_LINES",
            default=_STDERR_DEFAULT_TAIL_LINES,
            limit_name="tail_lines",
        )
        self._stderr_line_bytes_limit = _stderr_limit(
            stderr_line_bytes,
            env_name="GLUDD_MCP_STDERR_LINE_BYTES",
            default=_STDERR_DEFAULT_LINE_BYTES,
            limit_name="line_bytes",
        )
        self._stderr_max_bytes = _stderr_limit(
            stderr_max_bytes,
            env_name="GLUDD_MCP_STDERR_MAX_BYTES",
            default=_STDERR_DEFAULT_MAX_BYTES,
            limit_name="max_bytes",
        )
        self._stderr_max_lines = _stderr_limit(
            stderr_max_lines,
            env_name="GLUDD_MCP_STDERR_MAX_LINES",
            default=_STDERR_DEFAULT_MAX_LINES,
            limit_name="max_lines",
        )
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_secret_values: tuple[str, ...] = ()
        self._reset_stderr_diagnostics()

    @property
    def pid(self) -> int | None:
        """Return the child process ID when a process has been started."""
        if self._process is None:
            return None
        return self._process.pid

    @property
    def stderr_diagnostics(self) -> dict[str, Any]:
        """Return a bounded, already-redacted stderr tail and its counters.

        Raw stderr is never exposed.  The metadata is intentionally sufficient
        for an event/log sink to persist an early failure event without copying
        an untrusted payload into that event.
        """
        tail = "\n".join(self._stderr_tail)
        return {
            "tail": tail,
            "tail_bytes": len(tail.encode("utf-8")),
            "tail_lines": len(self._stderr_tail),
            "observed_bytes": self._stderr_observed_bytes,
            "observed_lines": self._stderr_observed_lines,
            "truncated": bool(
                self._stderr_truncated_bytes or self._stderr_truncated_lines or self._stderr_policy_reason
            ),
            "truncated_bytes": self._stderr_truncated_bytes,
            "truncated_lines": self._stderr_truncated_lines,
            "policy_breached": self._stderr_policy_reason is not None,
            "policy_reason": self._stderr_policy_reason,
            "limits": {
                "tail_bytes": self._stderr_tail_bytes_limit,
                "tail_lines": self._stderr_tail_lines_limit,
                "line_bytes": self._stderr_line_bytes_limit,
                "max_bytes": self._stderr_max_bytes,
                "max_lines": self._stderr_max_lines,
            },
        }

    def _reset_stderr_diagnostics(self) -> None:
        self._stderr_tail: deque[str] = deque()
        self._stderr_observed_bytes = 0
        self._stderr_observed_lines = 0
        self._stderr_truncated_bytes = 0
        self._stderr_truncated_lines = 0
        self._stderr_policy_reason: str | None = None

    def _raise_stderr_policy_breach(self) -> None:
        if self._stderr_policy_reason is None:
            return
        raise MCPTransportError(
            "MCP stderr policy breach "
            f"({self._stderr_policy_reason}); subprocess terminated after "
            f"{self._stderr_observed_bytes} bytes and "
            f"{self._stderr_observed_lines} lines"
        )

    def _redact_stderr_line(self, raw: bytes) -> str:
        text = raw.decode("utf-8", errors="replace")
        for secret in self._stderr_secret_values:
            text = text.replace(secret, "[REDACTED_MCP_ENV]")
        text = sanitize_error_message(text)
        return _STDERR_CONTROL_CHARS_RE.sub("�", text)

    @staticmethod
    def _tail_bytes(text: str, max_bytes: int) -> str:
        """Return at most the last ``max_bytes`` without broken UTF-8."""
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text
        return encoded[-max_bytes:].decode("utf-8", errors="ignore")

    def _tail_size(self) -> int:
        return len("\n".join(self._stderr_tail).encode("utf-8"))

    def _retain_stderr_line(self, raw: bytes) -> None:
        line = self._redact_stderr_line(raw)
        encoded_size = len(line.encode("utf-8"))
        retained_line_limit = min(
            self._stderr_line_bytes_limit,
            self._stderr_tail_bytes_limit,
        )
        if encoded_size > retained_line_limit:
            line = self._tail_bytes(line, retained_line_limit)
            self._stderr_truncated_bytes += encoded_size - len(line.encode("utf-8"))
        self._stderr_tail.append(line)

        while (
            len(self._stderr_tail) > self._stderr_tail_lines_limit or self._tail_size() > self._stderr_tail_bytes_limit
        ):
            removed = self._stderr_tail.popleft()
            self._stderr_truncated_lines += 1
            self._stderr_truncated_bytes += len(removed.encode("utf-8"))

    async def _stderr_policy_breach(self, reason: str) -> None:
        if self._stderr_policy_reason is not None:
            return
        self._stderr_policy_reason = reason
        # Stable, bounded metadata makes this an immediately persistable
        # failure event in Gunicorn/container logs; never include raw stderr.
        safe_server_id = json.dumps(
            sanitize_error_message(self._config.server_id),
            ensure_ascii=True,
        )[:258]
        logger.error(
            "event=mcp.stderr.policy_breach server_id=%s reason=%s "
            "observed_bytes=%d observed_lines=%d truncated_bytes=%d "
            "truncated_lines=%d",
            safe_server_id,
            reason,
            self._stderr_observed_bytes,
            self._stderr_observed_lines,
            self._stderr_truncated_bytes,
            self._stderr_truncated_lines,
        )
        await self._force_terminate()

    async def _drain_stderr(self) -> None:
        """Continuously drain stderr under bounded memory and I/O policies."""
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        stream = proc.stderr
        pending = bytearray()
        try:
            while True:
                remaining = self._stderr_max_bytes - self._stderr_observed_bytes
                read_size = min(
                    _STDERR_READ_CHUNK_BYTES,
                    self._stderr_line_bytes_limit + 1,
                    remaining + 1,
                )
                read_result = stream.read(max(1, read_size))
                # Some existing unit-test process doubles predate stderr
                # capture and expose a synchronous MagicMock. Treat that as no
                # diagnostic stream; real asyncio StreamReader.read is awaitable.
                if not inspect.isawaitable(read_result):
                    return
                chunk = await read_result
                if not isinstance(chunk, bytes):
                    await self._stderr_policy_breach("drain_error")
                    return
                if not chunk:
                    if pending:
                        if len(pending) > self._stderr_line_bytes_limit:
                            self._stderr_truncated_lines += 1
                            self._stderr_truncated_bytes += len(pending)
                            await self._stderr_policy_breach("line_bytes")
                            return
                        self._stderr_observed_lines += 1
                        if self._stderr_observed_lines > self._stderr_max_lines:
                            self._stderr_truncated_lines += 1
                            self._stderr_truncated_bytes += len(pending)
                            await self._stderr_policy_breach("max_lines")
                            return
                        self._retain_stderr_line(bytes(pending))
                    return

                self._stderr_observed_bytes += len(chunk)
                if self._stderr_observed_bytes > self._stderr_max_bytes:
                    self._stderr_truncated_bytes += self._stderr_observed_bytes - self._stderr_max_bytes
                    await self._stderr_policy_breach("max_bytes")
                    return

                pending.extend(chunk)
                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        if len(pending) > self._stderr_line_bytes_limit:
                            self._stderr_truncated_lines += 1
                            self._stderr_truncated_bytes += len(pending)
                            await self._stderr_policy_breach("line_bytes")
                            return
                        break
                    if newline > self._stderr_line_bytes_limit:
                        self._stderr_truncated_lines += 1
                        self._stderr_truncated_bytes += newline
                        await self._stderr_policy_breach("line_bytes")
                        return
                    raw_line = bytes(pending[:newline])
                    del pending[: newline + 1]
                    if raw_line.endswith(b"\r"):
                        raw_line = raw_line[:-1]
                    self._stderr_observed_lines += 1
                    if self._stderr_observed_lines > self._stderr_max_lines:
                        self._stderr_truncated_lines += 1
                        self._stderr_truncated_bytes += len(raw_line)
                        await self._stderr_policy_breach("max_lines")
                        return
                    self._retain_stderr_line(raw_line)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed drain can recreate the original pipe-deadlock. Fail
            # closed and log only the exception type via the stable reason.
            await self._stderr_policy_breach("drain_error")
        finally:
            # Resolved env values are needed only while redacting the live
            # stream. Do not retain credential material for the client lifetime.
            self._stderr_secret_values = ()

    async def _finish_stderr_drain(self) -> None:
        task = self._stderr_task
        if task is None or task is asyncio.current_task():
            return
        if not task.done():
            # Once the process has exited, give the StreamReader one short,
            # bounded turn to consume its buffered diagnostic suffix and EOF.
            # Never let diagnostics delay shutdown by the full server timeout.
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=min(self._config.timeout_seconds, 0.25),
                )
            except TimeoutError:
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _build_env(self) -> dict[str, str]:
        """Minimal allowlisted base env + the server's declared/resolved env.

        Finding 2: never hand the full host environment to the subprocess.
        Finding 7: if a secrets manager is available, resolve env_aliases (Vault/
        OpenBao) so credentials are injected at start time rather than stored in
        plaintext config; otherwise fall back to the static declared env.
        """
        base: dict[str, str] = {}
        for key in _ENV_ALLOWLIST:
            val = os.environ.get(key)
            if val is not None:
                base[key] = val

        if self._secrets_mgr is not None:
            # Local import avoids a module-load cycle (secrets imports config).
            from general_ludd.mcp.secrets import resolve_mcp_env

            server_env = resolve_mcp_env(self._config, self._secrets_mgr)
        else:
            server_env = dict(self._config.env)

        base.update(server_env)
        return base

    async def _readline_with_timeout(self) -> bytes:
        """readline() bounded by the configured timeout. Finding 1.

        On timeout the subprocess is force-terminated (it is presumed hung) and
        an MCPTransportError is raised so the caller fails fast instead of
        blocking forever.

        An oversized frame (>64 KB asyncio default) raises ValueError or
        LimitOverrunError from readline().  Catch those too: force-terminate
        the subprocess so it doesn't linger, then raise a clean
        MCPTransportError rather than leaking the raw exception.
        """
        assert self._process is not None
        assert self._process.stdout is not None
        try:
            return await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError as exc:
            await self._force_terminate()
            raise MCPTransportError(
                f"MCP server timed out after {self._config.timeout_seconds}s waiting for response (method read)"
            ) from exc
        except (ValueError, asyncio.LimitOverrunError) as exc:
            # asyncio raises LimitOverrunError (subclass of Exception) or
            # ValueError when a line exceeds the StreamReader buffer limit
            # (~64 KB by default).  The subprocess is left running if we do
            # not kill it, so terminate first, then surface a clean error.
            await self._force_terminate()
            raise MCPTransportError(
                "MCP server sent an oversized JSON-RPC frame (exceeds "
                "asyncio StreamReader limit).  The subprocess has been "
                "terminated."
            ) from exc

    async def _force_terminate(self) -> None:
        """Best-effort kill of a hung/misbehaving subprocess."""
        proc = self._process
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=self._config.timeout_seconds)

    async def _drain_with_timeout(self) -> None:
        """stdin.drain() bounded by the configured timeout. Finding 1."""
        assert self._process is not None
        assert self._process.stdin is not None
        try:
            await asyncio.wait_for(
                self._process.stdin.drain(),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError as exc:
            await self._force_terminate()
            raise MCPTransportError(
                f"MCP server timed out after {self._config.timeout_seconds}s draining stdin"
            ) from exc

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._raise_stderr_policy_breach()
        if self._process is None or self._process.returncode is not None:
            raise MCPTransportError("Process not running")
        assert self._process.stdin is not None
        assert self._process.stdout is not None

        request_id = self._next_id()
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        line = json.dumps(request) + "\n"
        self._process.stdin.write(line.encode())
        await self._drain_with_timeout()

        skips = 0
        while True:
            response_line = await self._readline_with_timeout()
            if not response_line:
                self._raise_stderr_policy_breach()
                raise MCPTransportError("Connection closed")
            response = json.loads(response_line.decode())
            if response.get("id") != request_id:
                # Finding 5: bound the interleave-skip loop so a server that
                # streams a flood of unrelated frames can't spin us forever.
                skips += 1
                if skips >= _MAX_INTERLEAVE_SKIPS:
                    await self._force_terminate()
                    raise MCPTransportError(
                        f"Exceeded {_MAX_INTERLEAVE_SKIPS} interleaved frames without a response for id {request_id}"
                    )
                continue
            if "error" in response:
                raise MCPTransportError(f"JSON-RPC error: {response['error']}")
            return dict[str, Any](response.get("result", {}))

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._process is None or self._process.returncode is not None:
            return
        assert self._process.stdin is not None
        notification: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params
        line = json.dumps(notification) + "\n"
        self._process.stdin.write(line.encode())
        await self._drain_with_timeout()

    async def start(self) -> None:
        """Validate, spawn, and initialize the configured MCP server."""
        cmd = (self._config.command or []) + self._config.args
        # LAUNCH VALIDATION: fail closed BEFORE spawning — check allowlist,
        # PATH resolution, and package-spec injection in one go.
        _validate_launch_command(cmd)
        env = self._build_env()
        self._reset_stderr_diagnostics()
        declared_env_keys = set(self._config.env) | set(self._config.env_aliases)
        self._stderr_secret_values = tuple(
            sorted(
                {env[key] for key in declared_env_keys if env.get(key)},
                key=len,
                reverse=True,
            )
        )

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        # Start the drain before the initialize request so even startup errors
        # cannot fill the OS pipe and delay failure detection.
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(),
            name=f"mcp-stderr-{self._config.server_id}",
        )

        await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "general-ludd-agent",
                    "version": "0.1.0",
                },
            },
        )

        await self._send_notification("notifications/initialized", {})

    async def list_tools(self) -> list[MCPTool]:
        """Fetch and validate the server's advertised tools."""
        result = await self._send_request("tools/list")
        tools: list[MCPTool] = []
        for tool_data in result.get("tools", []):
            tools.append(
                MCPTool(
                    name=tool_data.get("name", ""),
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("inputSchema", {}),
                )
            )
        return tools

    async def list_resources(self) -> list[dict[str, Any]]:
        """Fetch resources advertised by the server."""
        result = await self._send_request("resources/list")
        return cast("list[dict[str, Any]]", result.get("resources", []))

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read one server resource by URI."""
        return await self._send_request(
            "resources/read",
            {"uri": uri},
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one tool with JSON-compatible arguments."""
        return await self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )

    async def stop(self) -> None:
        """Stop the child process and its stderr-drain task."""
        try:
            if self._process is not None and self._process.returncode is None:
                if self._process.stdin is not None:
                    self._process.stdin.close()
                self._process.terminate()
                # Finding 4: bound the wait() so a process that ignores SIGTERM
                # can't hang stop() forever — escalate to kill() on timeout.
                try:
                    await asyncio.wait_for(
                        self._process.wait(),
                        timeout=self._config.timeout_seconds,
                    )
                except TimeoutError:
                    try:
                        self._process.kill()
                    except ProcessLookupError:
                        return
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            self._process.wait(),
                            timeout=self._config.timeout_seconds,
                        )
        finally:
            await self._finish_stderr_drain()
