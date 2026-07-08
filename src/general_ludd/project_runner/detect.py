"""WP-E1: ``ToolchainDetector`` — marker-file → :class:`ProjectProfile` detection.

A target project that ships NO ``project.yml`` is detected from its marker
files (``pyproject.toml`` / ``package.json`` / ``go.mod`` / ``Cargo.toml`` /
``Makefile``) and a sensible default profile is synthesized. Detection is the
FALLBACK: :func:`general_ludd.project_runner.profile.load_project_profile` tries
an explicit ``project.yml`` first, then falls back to detection, and raises only
when both fail (no profile ⇒ fail-closed, never silent "run nothing").

Resolution order (highest precedence first):
    1. ``pyproject.toml``  → Python (pytest / ruff / mypy)
    2. ``package.json``    → Node    (npm test / npm run lint — only if declared)
    3. ``go.mod``          → Go      (go test / go vet)
    4. ``Cargo.toml``      → Rust    (cargo test / cargo clippy)
    5. ``Makefile``        → Generic (make test / make lint)

The detected profile's ``allowed_exec`` is derived from the basename of every
declared command's argv[0] so :meth:`ProjectProfile.resolve_argv` does not reject
the synthesized commands as a configuration error (the safety allowlist is the
same one an explicit ``project.yml`` must satisfy).
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from general_ludd.project_runner.profile import ProjectProfile

# Marker filename → ecosystem identifier. ORDER MATTERS: the first present
# marker wins (so a polyglot repo with both pyproject.toml + Makefile resolves
# deterministically to the Python profile).
MARKER_MAP: dict[str, str] = {
    "pyproject.toml": "python",
    "package.json": "node",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "Makefile": "make",
}


def _python_commands() -> dict[str, str]:
    return {
        "test": "pytest -q",
        "lint": "ruff check .",
        "typecheck": "mypy src",
    }


def _go_commands() -> dict[str, str]:
    return {
        "test": "go test ./...",
        "lint": "go vet ./...",
    }


def _rust_commands() -> dict[str, str]:
    return {
        "test": "cargo test --quiet",
        "lint": "cargo clippy --all-targets",
    }


def _make_commands() -> dict[str, str]:
    return {
        "test": "make test",
        "lint": "make lint",
    }


def _node_commands(workspace: Path) -> dict[str, str]:
    """Node commands are derived from ``scripts`` in ``package.json``.

    Unlike the other ecosystems (where the marker file alone implies the
    toolchain), Node projects commonly omit ``test``/``lint`` scripts — so we
    only declare the commands the project actually defines. This keeps the
    profile truthful: a declared ``npm test`` only exists if ``scripts.test``
    is set.
    """
    pkg_path = workspace / "package.json"
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict):
        return {}
    commands: dict[str, str] = {}
    if isinstance(scripts.get("test"), str) and scripts["test"].strip():
        commands["test"] = "npm test"
    if isinstance(scripts.get("lint"), str) and scripts["lint"].strip():
        commands["lint"] = "npm run lint"
    if isinstance(scripts.get("build"), str) and scripts["build"].strip():
        commands["build"] = "npm run build"
    return commands


def _derive_allowed_exec(commands: dict[str, str]) -> list[str]:
    """Basenames of every declared command's argv[0], de-duplicated, order-
    preserved. The profile's safety allowlist requires that the basename of
    argv[0] for each command be present; deriving it from the commands keeps a
    detected profile self-consistent without the operator having to hand-list
    every executable."""
    seen: list[str] = []
    for cmd in commands.values():
        try:
            argv = shlex.split(cmd)
        except ValueError:
            continue
        if not argv:
            continue
        exe = os.path.basename(argv[0])
        if exe and exe not in seen:
            seen.append(exe)
    return seen


class ToolchainDetector:
    """Marker-file → :class:`ProjectProfile` detection (no ``project.yml`` needed).

    Stateless; :meth:`detect` is the single entry point. The class exists so
    callers can substitute/override the marker table (``MARKER_MAP``) in tests
    or for unusual toolchains.
    """

    MARKER_MAP: dict[str, str] = MARKER_MAP

    @staticmethod
    def detect(workspace: str | Path) -> ProjectProfile | None:
        """Synthesize a profile from marker files in ``workspace``.

        Returns ``None`` when no recognized marker is present — the caller
        decides fail-closed behavior (raising vs. erroring). Resolution order
        follows :data:`MARKER_MAP`'s iteration order (pyproject > package.json
        > go.mod > Cargo.toml > Makefile), so a polyglot repo resolves
        deterministically.
        """
        root = Path(workspace).resolve()
        if not root.is_dir():
            return None
        for marker, ecosystem in ToolchainDetector.MARKER_MAP.items():
            marker_path = root / marker
            if not marker_path.is_file():
                continue
            commands = ToolchainDetector._commands_for(ecosystem, root, marker_path)
            if not commands:
                # The marker file exists but yielded no usable commands (e.g. a
                # package.json with no scripts). Skip rather than emit an empty
                # profile — the next marker in the precedence order may apply.
                continue
            return ProjectProfile(
                name=f"{ecosystem}-detected",
                commands=commands,
                allowed_exec=_derive_allowed_exec(commands),
            )
        return None

    @staticmethod
    def _commands_for(
        ecosystem: str, workspace: Path, marker_path: Path
    ) -> dict[str, str]:
        if ecosystem == "python":
            return _python_commands()
        if ecosystem == "node":
            return _node_commands(workspace)
        if ecosystem == "go":
            return _go_commands()
        if ecosystem == "rust":
            return _rust_commands()
        if ecosystem == "make":
            return _make_commands()
        return {}
