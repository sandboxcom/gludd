"""Dependency manager with uv-first / pip-fallback strategy."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class InvalidPackageSpecError(ValueError):
    """Raised when a package spec fails argument-injection validation."""


# PEP 508-ish: a package name (letters/digits/_-.), optional extras in
# brackets, and an optional version specifier built from the comparison
# operators and version tokens uv/pip accept. Deliberately strict: no
# whitespace, no shell metacharacters, no leading dash, no path separators,
# no URLs / VCS refs / local-file installs.
_PACKAGE_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_EXTRAS = r"(?:\[[A-Za-z0-9._,\s-]*\])?"
_VERSION_SPEC = r"(?:(?:===|==|!=|~=|>=|<=|>|<)[A-Za-z0-9][A-Za-z0-9._*+!-]*)"
_SPEC_RE = re.compile(
    rf"^{_PACKAGE_NAME}{_EXTRAS}"
    rf"(?:{_VERSION_SPEC}(?:\s*,\s*{_VERSION_SPEC})*)?$"
)


def _validate_package_spec(spec: str) -> str:
    """Validate a uv/pip package spec against argument injection.

    Accepts a PEP 508-ish ``name[extras]<version-spec>`` value. Rejects
    empty/whitespace specs, leading-dash option injection
    (``--index-url=...``, ``-e .``), shell metacharacters, internal
    whitespace, and path-like values. Returns the spec unchanged on success
    so callers can use it inline.
    """
    if not isinstance(spec, str):
        raise InvalidPackageSpecError(f"package spec must be a string: {spec!r}")
    if not spec or spec != spec.strip():
        raise InvalidPackageSpecError(f"empty or padded package spec: {spec!r}")
    if spec.startswith("-"):
        raise InvalidPackageSpecError(
            f"package spec must not start with '-' (option injection): {spec!r}"
        )
    if not _SPEC_RE.match(spec):
        raise InvalidPackageSpecError(
            f"unsafe or malformed package spec: {spec!r}"
        )
    return spec


@dataclass
class UpdateResult:
    package_name: str
    old_version: str
    new_version: str
    changed: bool
    tool_used: str


@dataclass
class SyncResult:
    success: bool
    packages_synced: int
    tool_used: str


@dataclass
class OutdatedPackage:
    name: str
    current_version: str
    latest_version: str


def _has_uv() -> bool:
    return shutil.which("uv") is not None


class DependencyManager:
    def __init__(self, project_root: str | None = None) -> None:
        self.project_root = project_root or "."

    async def _run(
        self, *args: str
    ) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.project_root,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        return (
            proc.returncode or 0,
            stdout_bytes.decode(),
            stderr_bytes.decode(),
        )

    async def update_package(
        self,
        package_name: str,
        version_constraint: str | None = None,
    ) -> UpdateResult:
        spec = (
            f"{package_name}{version_constraint}"
            if version_constraint
            else package_name
        )
        # Harden against argument/option injection (issue #69): the spec is
        # derived from caller input and is passed positionally to uv/pip.
        # Reject anything that is not a PEP 508-ish package spec BEFORE any
        # subprocess is launched.
        spec = _validate_package_spec(spec)

        if _has_uv():
            return await self._update_with_uv(package_name, spec)
        return await self._update_with_pip(package_name, spec)

    async def _update_with_uv(
        self, package_name: str, spec: str
    ) -> UpdateResult:
        # `--` separates positional package args from uv options so a spec
        # can never be reinterpreted as a flag.
        rc, stdout, _ = await self._run("uv", "add", "--", spec)
        if rc != 0:
            logger.error("uv add failed for %s", spec)
            return UpdateResult(
                package_name=package_name,
                old_version="",
                new_version="",
                changed=False,
                tool_used="uv",
            )

        changed = "Resolved 0 packages" not in stdout
        return UpdateResult(
            package_name=package_name,
            old_version="",
            new_version="",
            changed=changed,
            tool_used="uv",
        )

    async def _update_with_pip(
        self, package_name: str, spec: str
    ) -> UpdateResult:
        rc, stdout, _ = await self._run(
            "pip", "install", "--upgrade", "--", spec
        )
        if rc != 0:
            logger.error("pip install failed for %s", spec)
            return UpdateResult(
                package_name=package_name,
                old_version="",
                new_version="",
                changed=False,
                tool_used="pip",
            )

        changed = "Requirement already satisfied" not in stdout
        return UpdateResult(
            package_name=package_name,
            old_version="",
            new_version="",
            changed=changed,
            tool_used="pip",
        )

    async def sync_environment(self) -> SyncResult:
        if _has_uv():
            return await self._sync_with_uv()
        return await self._sync_with_pip()

    async def _sync_with_uv(self) -> SyncResult:
        rc, _, stderr = await self._run("uv", "sync")
        if rc != 0:
            logger.error("uv sync failed: %s", stderr)
            return SyncResult(success=False, packages_synced=0, tool_used="uv")
        return SyncResult(success=True, packages_synced=0, tool_used="uv")

    async def _sync_with_pip(self) -> SyncResult:
        rc, _, stderr = await self._run(
            "pip", "install", "-r", "requirements.txt"
        )
        if rc != 0:
            logger.error("pip sync failed: %s", stderr)
            return SyncResult(success=False, packages_synced=0, tool_used="pip")
        return SyncResult(success=True, packages_synced=0, tool_used="pip")

    async def check_for_updates(self) -> list[OutdatedPackage]:
        if _has_uv():
            return await self._check_outdated_uv()
        return await self._check_outdated_pip()

    async def _check_outdated_uv(self) -> list[OutdatedPackage]:
        rc, stdout, _ = await self._run(
            "uv", "pip", "list", "--outdated", "--format=json"
        )
        if rc != 0 or not stdout.strip():
            _, stdout2, _ = await self._run(
                "uv", "pip", "list", "--outdated"
            )
            return self._parse_outdated_text(stdout2)
        return self._parse_outdated_json(stdout)

    async def _check_outdated_pip(self) -> list[OutdatedPackage]:
        rc, stdout, _ = await self._run(
            "pip", "list", "--outdated", "--format=json"
        )
        if rc != 0 or not stdout.strip():
            _, stdout2, _ = await self._run("pip", "list", "--outdated")
            return self._parse_outdated_text(stdout2)
        return self._parse_outdated_json(stdout)

    def _parse_outdated_text(self, text: str) -> list[OutdatedPackage]:
        packages: list[OutdatedPackage] = []
        for line in text.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] not in ("Package", "-----"):
                packages.append(
                    OutdatedPackage(
                        name=parts[0],
                        current_version=parts[1],
                        latest_version=parts[2],
                    )
                )
        return packages

    def _parse_outdated_json(self, text: str) -> list[OutdatedPackage]:
        import json

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [
            OutdatedPackage(
                name=item["name"],
                current_version=item["version"],
                latest_version=item["latest_version"],
            )
            for item in data
        ]

    async def generate_requirements(self) -> None:
        if _has_uv():
            await self._run(
                "uv", "pip", "freeze", ">", "requirements.txt"
            )
        else:
            await self._run("pip", "freeze")
