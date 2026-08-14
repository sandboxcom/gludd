"""Ripgrep-backed search — direct subprocess + ``rg --json``, bundled binary first.

This wraps ``rg`` (ripgrep) for fast, structured code search. It is *fail-soft*:
a missing binary, a timeout, or an OSError never raises — it returns
``RgResult(available=False)`` so callers can degrade to a slower in-process
search (see :mod:`general_ludd.code_intelligence.search`). ``rg`` exit code 1
means "no matches" (a normal, non-error result); exit code 2 means a real
ripgrep error and surfaces the captured stderr.

The binary is resolved bundled-first (``dist/binaries/rg`` via
:class:`~general_ludd.filestore.bootstrap.BinaryBootstrapper`) then from ``PATH``
(``shutil.which``), mirroring how OpenBao/OpenTofu are located.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from general_ludd.security.path_canonicalizer import is_denied_path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class RgMatch:
    """A single ripgrep match: a line of ``text`` at ``line`` in ``file``."""

    file: str
    line: int
    text: str


@dataclass
class RgResult:
    """Outcome of an :meth:`RgSearch.search` call.

    ``available`` is ``False`` when ripgrep could not be run at all (no binary,
    timeout, OSError). ``error`` carries ripgrep's stderr on a true rg error
    (exit code >= 2). A clean run with zero matches is ``available=True`` with an
    empty ``matches`` list (rg exit code 1).
    """

    available: bool
    matches: list[RgMatch] = field(default_factory=list)
    error: str | None = None
    returncode: int | None = None


class RgSearch:
    """Build, run, and parse ``rg --json`` searches with a bundled-binary fallback."""

    def __init__(
        self,
        rg_path: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        allowed_roots: list[str] | None = None,
    ) -> None:
        """Configure an optional binary, deadline, and canonical root allowlist."""
        self._rg_path = rg_path
        self._timeout = timeout
        self._allowed_roots = allowed_roots

    # --- binary location ------------------------------------------------

    @staticmethod
    def locate_rg() -> str | None:
        """Resolve the ``rg`` binary: bundled (``dist/binaries/rg``) first, then PATH.

        Returns the absolute path to an executable, or ``None`` if neither a
        bundled binary nor a ``PATH`` ``rg`` is available.
        """
        try:
            from general_ludd.filestore.bootstrap import BinaryBootstrapper

            bundled = BinaryBootstrapper().get_bundled_binary_path("rg")
            if bundled and Path(bundled).is_file():
                return bundled
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Bundled rg lookup failed: %s", exc)
        return shutil.which("rg")

    def _resolve_rg(self) -> str | None:
        if self._rg_path:
            return self._rg_path
        return self.locate_rg()

    # --- argv construction ----------------------------------------------

    @staticmethod
    def build_argv(
        rg: str,
        query: str,
        root: str = ".",
        *,
        globs: list[str] | None = None,
        types: list[str] | None = None,
        flags: list[str] | None = None,
    ) -> list[str]:
        """Build a ``rg --json`` argv.

        The ``--`` separator guards a ``query`` that begins with a dash (so it is
        always treated as the pattern, never as an option). ``root`` follows the
        pattern after ``--``. ``globs`` -> ``-g`` filters, ``types`` -> ``-t``
        filters, ``flags`` -> extra raw flags (e.g. ``-i``, ``--word-regexp``).
        One ripgrep thread bounds its internal per-file output buffering without
        silently truncating matches.
        """
        argv = [rg, "--json"]
        for g in globs or []:
            argv += ["-g", g]
        for t in types or []:
            argv += ["-t", t]
        # Allowlist: only these standalone boolean rg flags may be passed via
        # `flags`. Value-taking filters (-g/--glob, -t/--type) are handled above
        # via `globs`/`types`. Anything else is dropped with a warning so a caller
        # (or untrusted config) can never inject arbitrary rg flags (e.g.
        # --passthru, -e, --pre) past the `--` guard.
        _SAFE_FLAGS = frozenset({
            "-i", "--ignore-case",
            "-w", "--word-regexp",
            "-F", "--fixed-strings",
            "--multiline",
            "-U",
            "-s", "--case-sensitive",
        })
        for flag in flags or []:
            if flag in _SAFE_FLAGS:
                argv.append(flag)
            else:
                logger.warning("rg_search: dropping disallowed flag %r", flag)
        # ripgrep buffers per-file output when it searches in parallel. A
        # single worker preserves result correctness while bounding that
        # upstream buffering before Python applies its own result contract.
        argv += ["--threads", "1", "--", query, root]
        return argv

    # --- NDJSON parsing -------------------------------------------------

    @staticmethod
    def parse_json_stream(stdout: str) -> list[RgMatch]:
        """Parse ripgrep's NDJSON event stream into :class:`RgMatch` objects.

        Only ``type == "match"`` events produce matches. ``begin``/``end``/
        ``summary``/``context`` events are ignored. A path or line ``text`` may
        be delivered as ``{"text": ...}`` (UTF-8) or ``{"bytes": <base64>}``
        (non-UTF-8); both are decoded. Non-JSON lines are skipped.
        """
        matches: list[RgMatch] = []
        for raw in stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("type") != "match":
                continue
            data = event.get("data") or {}
            path = RgSearch._decode_text(data.get("path"))
            text = RgSearch._decode_text(data.get("lines"))
            line_no = data.get("line_number")
            if path is None or not isinstance(line_no, int):
                continue
            matches.append(RgMatch(file=path, line=line_no, text=(text or "").rstrip("\n")))
        return matches

    @staticmethod
    def _decode_text(obj: object) -> str | None:
        """Decode a ripgrep ``{"text": ...}`` / ``{"bytes": <base64>}`` object."""
        if not isinstance(obj, dict):
            return None
        if "text" in obj and isinstance(obj["text"], str):
            return obj["text"]
        if "bytes" in obj and isinstance(obj["bytes"], str):
            try:
                return base64.b64decode(obj["bytes"]).decode("utf-8", errors="replace")
            except (ValueError, binascii.Error):
                return None
        return None

    # --- path confinement -----------------------------------------------

    def _validate_root(self, root: str) -> str | RgResult:
        """Return the canonical root or an error result when it is unsafe.

        Resolves ``root`` to an absolute path and checks it is under at least
        one allowed root. Also checks the resolved path against the deny-list
        in :mod:`general_ludd.security.path_canonicalizer`.
        """
        try:
            resolved = Path(root).resolve()
        except OSError:
            return RgResult(available=False, error=f"Cannot resolve root: {root}")

        if not resolved.is_dir():
            return RgResult(available=False, error=f"Search root is not a directory: {root}")

        if is_denied_path(str(resolved)):
            return RgResult(available=False, error=f"Path denied: {root}")

        allowed = self._allowed_roots if self._allowed_roots else [str(Path().cwd())]
        for allowed_root in allowed:
            try:
                allowed_resolved = Path(allowed_root).resolve()
                resolved.relative_to(allowed_resolved)
                return str(resolved)
            except (ValueError, OSError):
                continue

        return RgResult(available=False, error=f"Path outside allowed directories: {root}")

    # --- run ------------------------------------------------------------

    def search(
        self,
        query: str,
        root: str = ".",
        *,
        globs: list[str] | None = None,
        types: list[str] | None = None,
        flags: list[str] | None = None,
    ) -> RgResult:
        """Run a ripgrep search, fail-soft.

        Returns ``RgResult(available=False)`` when rg cannot be located/run, on
        timeout, or on OSError. rg exit 0 = matches, exit 1 = no matches (both
        ``available=True``), exit >= 2 = rg error (``available=True`` with the
        stderr surfaced in ``error``).
        """
        resolved_root = self._validate_root(root)
        if isinstance(resolved_root, RgResult):
            return resolved_root

        rg = self._resolve_rg()
        if not rg:
            return RgResult(available=False, error="ripgrep (rg) not found")

        argv = self.build_argv(
            rg,
            query,
            resolved_root,
            globs=globs,
            types=types,
            flags=flags,
        )
        try:
            # argv is a fixed list (no shell); query/root are positional, not flags.
            # A timeout bounds runtime. stdout remains correctness-preserving and
            # fully captured; callers must not treat this as a total byte limit.
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("ripgrep timed out after %.0fs", self._timeout)
            return RgResult(available=False, error="ripgrep timed out")
        except OSError as exc:
            logger.warning("ripgrep failed to start: %s", exc)
            return RgResult(available=False, error=str(exc))

        if proc.returncode == 1:
            # No matches — a normal, non-error outcome.
            return RgResult(available=True, matches=[], returncode=1)
        if proc.returncode not in (0, 1):
            # Any non-success, non-"no-match" code is an error — including a
            # NEGATIVE code from a signal kill (e.g. -9 SIGKILL), which the old
            # `>= 2` test let fall through into the (empty) match-parse path.
            return RgResult(
                available=True,
                matches=[],
                error=(proc.stderr or "").strip() or f"ripgrep exited {proc.returncode}",
                returncode=proc.returncode,
            )

        matches = self.parse_json_stream(proc.stdout)
        return RgResult(available=True, matches=matches, returncode=proc.returncode)
