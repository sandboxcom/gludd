#!/usr/bin/env python3
"""Validate or run the exact-pinned FreeLLMAPI upstream build plan."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import NoReturn, Protocol, cast

from scripts.freellmapi_upstream_admission import GitHubCLIClient

from general_ludd.models.freellmapi_upstream_build import (
    FreeLLMAPIUpstreamBuildError,
    FreeLLMAPIUpstreamBuildFault,
    build_upstream_evidence,
    validate_upstream_build_plan,
    validate_upstream_toolchain,
)
from general_ludd.models.freellmapi_upstream_source import (
    FREELLMAPI_REPOSITORY,
    FreeLLMAPIAdmissionError,
    inspect_upstream_archive,
)

_MAX_JSON_BYTES = 1024 * 1024
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
_MAX_REPORT_BYTES = 1024 * 1024
_VERSION_TIMEOUT_SECONDS = 30
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ArchiveClient(Protocol):
    """Narrow fixed-repository archive transport."""

    def get_bytes(self, endpoint: str) -> bytes:
        """Return one bounded source archive."""
        ...


class BuildExecutor(Protocol):
    """Narrow command runner for fixed upstream build argv."""

    def output(
        self, argv: tuple[str, ...], *, cwd: Path, env: dict[str, str]
    ) -> str:
        """Return one bounded tool version."""
        ...

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int,
    ) -> int:
        """Stream one fixed upstream command and return its status."""
        ...


class SubprocessBuildExecutor:
    """Execute fixed argv without a shell or inherited credentials."""

    def output(
        self, argv: tuple[str, ...], *, cwd: Path, env: dict[str, str]
    ) -> str:
        """Return a bounded version string."""
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FreeLLMAPIUpstreamBuildError(
                FreeLLMAPIUpstreamBuildFault.TOOLCHAIN_INVALID
            ) from exc
        output = result.stdout.strip()
        if result.returncode != 0 or not output or len(output) > 128:
            raise FreeLLMAPIUpstreamBuildError(
                FreeLLMAPIUpstreamBuildFault.TOOLCHAIN_INVALID
            )
        return output

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int,
    ) -> int:
        """Stream one phase so long-running upstream work remains observable."""
        print(
            json.dumps(
                {"event": "freellmapi_upstream_phase_started", "tool": argv[0]},
                sort_keys=True,
            ),
            flush=True,
        )
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return 124
        except OSError:
            return 126
        return result.returncode


def sha256_hex(content: bytes) -> str:
    """Return only the content-free SHA-256 identity of bytes."""
    return hashlib.sha256(content).hexdigest()


def canonical_sha256(value: object) -> str:
    """Return a deterministic JSON identity for test and lock composition."""
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return sha256_hex(encoded)


def _raise(fault: FreeLLMAPIUpstreamBuildFault) -> NoReturn:
    raise FreeLLMAPIUpstreamBuildError(fault)


def _load_json(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FreeLLMAPIUpstreamBuildError(
            FreeLLMAPIUpstreamBuildFault.IO_FAILED
        ) from exc
    if not raw or len(raw) > _MAX_JSON_BYTES:
        _raise(FreeLLMAPIUpstreamBuildFault.INPUT_INVALID)
    try:
        value: object = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreeLLMAPIUpstreamBuildError(
            FreeLLMAPIUpstreamBuildFault.INPUT_INVALID
        ) from exc
    if not isinstance(value, dict):
        _raise(FreeLLMAPIUpstreamBuildFault.INPUT_INVALID)
    return cast(dict[str, object], value)


def _candidate_commit(candidate: Mapping[str, object]) -> str:
    upstream = candidate.get("upstream")
    if not isinstance(upstream, Mapping):
        _raise(FreeLLMAPIUpstreamBuildFault.CANDIDATE_INVALID)
    commit = upstream.get("commit")
    if not isinstance(commit, str) or _COMMIT_RE.fullmatch(commit) is None:
        _raise(FreeLLMAPIUpstreamBuildFault.CANDIDATE_INVALID)
    return commit


def _archive_identity(candidate: Mapping[str, object]) -> tuple[str, int]:
    archive = candidate.get("archive")
    if not isinstance(archive, Mapping):
        _raise(FreeLLMAPIUpstreamBuildFault.CANDIDATE_INVALID)
    digest = archive.get("sha256")
    size = archive.get("size_bytes")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
    ):
        _raise(FreeLLMAPIUpstreamBuildFault.CANDIDATE_INVALID)
    return digest, size


def _verify_archive(candidate: Mapping[str, object], archive: bytes) -> None:
    expected_digest, expected_size = _archive_identity(candidate)
    if len(archive) != expected_size or sha256_hex(archive) != expected_digest:
        _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
    try:
        inspect_upstream_archive(archive, max_archive_bytes=_MAX_ARCHIVE_BYTES)
    except FreeLLMAPIAdmissionError as exc:
        raise FreeLLMAPIUpstreamBuildError(
            FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID
        ) from exc


def _member_parts(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name:
        _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
    return path.parts


def _materialize_archive(archive_bytes: bytes, destination: Path) -> Path:
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > _MAX_ARCHIVE_MEMBERS:
                _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
            expanded = sum(member.size for member in members if member.isfile())
            if expanded > _MAX_ARCHIVE_EXPANDED_BYTES:
                _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
            roots: set[str] = set()
            destinations: set[str] = set()
            for member in members:
                parts = _member_parts(member.name)
                roots.add(parts[0])
                if len(parts) == 1:
                    if not member.isdir():
                        _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
                    continue
                if not member.isfile() and not member.isdir():
                    _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
                relative = parts[1:]
                collision_key = "/".join(relative).casefold()
                if collision_key in destinations:
                    _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
                destinations.add(collision_key)
                target = destination.joinpath(*relative)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                if extracted is None or member.size < 0:
                    _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
                body = extracted.read(member.size + 1)
                if len(body) != member.size:
                    _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
                target.write_bytes(body)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            if len(roots) != 1:
                _raise(FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID)
    except FreeLLMAPIUpstreamBuildError:
        raise
    except (OSError, tarfile.TarError, EOFError) as exc:
        raise FreeLLMAPIUpstreamBuildError(
            FreeLLMAPIUpstreamBuildFault.ARCHIVE_INVALID
        ) from exc
    return destination


def _scripts(path: Path) -> Mapping[str, object]:
    manifest = _load_json(path)
    scripts = manifest.get("scripts")
    if not isinstance(scripts, Mapping):
        _raise(FreeLLMAPIUpstreamBuildFault.SCRIPTS_INVALID)
    return scripts


def _verify_upstream_scripts(source_root: Path) -> None:
    root_scripts = _scripts(source_root / "package.json")
    server_scripts = _scripts(source_root / "server/package.json")
    for name in ("test", "test:migrations", "lint", "build"):
        if not isinstance(root_scripts.get(name), str) or not root_scripts[name]:
            _raise(FreeLLMAPIUpstreamBuildFault.SCRIPTS_INVALID)
    if (
        not isinstance(server_scripts.get("test:coverage"), str)
        or not server_scripts["test:coverage"]
    ):
        _raise(FreeLLMAPIUpstreamBuildFault.SCRIPTS_INVALID)


def _execution_environment(source_root: Path) -> dict[str, str]:
    path = os.environ.get("PATH")
    if not path:
        _raise(FreeLLMAPIUpstreamBuildFault.TOOLCHAIN_INVALID)
    home = source_root / ".gludd-home"
    cache = source_root / ".gludd-npm-cache"
    home.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    return {
        "PATH": path,
        "HOME": str(home),
        "CI": "true",
        "NO_COLOR": "1",
        "NPM_CONFIG_USERCONFIG": "/dev/null",
        "NPM_CONFIG_CACHE": str(cache),
        "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
    }


def safe_report_path(report: Path, repository_root: Path) -> Path:
    """Allow reports only in tracked config or a Gludd-namespaced temp path."""
    resolved = (
        report if report.is_absolute() else repository_root / report
    ).resolve(strict=False)
    config_root = (repository_root / "config/freellmapi").resolve(strict=False)
    if resolved.is_relative_to(config_root):
        return resolved
    temp_roots = {
        Path(tempfile.gettempdir()).resolve(strict=False),
        Path("/tmp").resolve(strict=False),
    }
    for temp_root in temp_roots:
        if resolved.is_relative_to(temp_root):
            relative = resolved.relative_to(temp_root)
            if any(part.startswith("gludd-freellmapi-") for part in relative.parts):
                return resolved
    _raise(FreeLLMAPIUpstreamBuildFault.INPUT_INVALID)


def write_report(report: Path, evidence: Mapping[str, object]) -> None:
    """Atomically write one bounded content-free evidence document."""
    temporary: Path | None = None
    try:
        payload = json.dumps(evidence, indent=2, sort_keys=True).encode() + b"\n"
        if len(payload) > _MAX_REPORT_BYTES:
            _raise(FreeLLMAPIUpstreamBuildFault.IO_FAILED)
        report.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=report.parent,
            prefix=f".{report.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        temporary.replace(report)
    except FreeLLMAPIUpstreamBuildError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise FreeLLMAPIUpstreamBuildError(
            FreeLLMAPIUpstreamBuildFault.IO_FAILED
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _run_steps(
    *,
    executor: BuildExecutor,
    source_root: Path,
    environment: dict[str, str],
    install: tuple[str, ...],
    steps: tuple[tuple[str, tuple[str, ...]], ...],
    timeout_seconds: int,
) -> list[dict[str, object]]:
    planned = (("install", install), *steps)
    results: list[dict[str, object]] = []
    failed = False
    for step_id, argv in planned:
        if failed:
            results.append(
                {"step_id": step_id, "status": "not_run", "exit_code": None}
            )
            continue
        raw_exit_code = executor.run(
            argv,
            cwd=source_root,
            env=environment,
            timeout_seconds=timeout_seconds,
        )
        exit_code = (
            min(255, 128 + abs(raw_exit_code))
            if raw_exit_code < 0
            else min(255, raw_exit_code)
        )
        failed = exit_code != 0
        results.append(
            {
                "step_id": step_id,
                "status": "failed" if failed else "passed",
                "exit_code": exit_code,
            }
        )
    return results


def run_live_build(
    *,
    candidate_path: Path,
    plan_path: Path,
    report_path: Path,
    client: ArchiveClient,
    executor: BuildExecutor,
) -> dict[str, object]:
    """Run one ephemeral exact-source build and always tear down its source."""
    candidate = _load_json(candidate_path)
    plan = _load_json(plan_path)
    validated = validate_upstream_build_plan(candidate, plan)
    commit = _candidate_commit(candidate)
    archive = client.get_bytes(f"repos/{FREELLMAPI_REPOSITORY}/tarball/{commit}")
    _verify_archive(candidate, archive)
    with tempfile.TemporaryDirectory(prefix="gludd-freellmapi-build-") as temporary:
        source_root = _materialize_archive(archive, Path(temporary) / "source")
        _verify_upstream_scripts(source_root)
        environment = _execution_environment(source_root)
        node_version = executor.output(
            ("node", "--version"), cwd=source_root, env=environment
        )
        npm_version = executor.output(
            ("npm", "--version"), cwd=source_root, env=environment
        )
        validate_upstream_toolchain(
            node_version,
            npm_version,
            validated["toolchains"],
        )
        results = _run_steps(
            executor=executor,
            source_root=source_root,
            environment=environment,
            install=validated["install"],
            steps=validated["steps"],
            timeout_seconds=validated["step_timeout_seconds"],
        )
        evidence = build_upstream_evidence(
            candidate_lock=candidate,
            plan=plan,
            node_version=node_version,
            npm_version=npm_version,
            results=results,
        )
        write_report(report_path, evidence)
        if evidence["decision"] != "upstream_build_verified":
            _raise(FreeLLMAPIUpstreamBuildFault.STEP_FAILED)
        return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("validate", "live"))
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def _validation_summary(
    candidate: Mapping[str, object], plan: Mapping[str, object]
) -> dict[str, object]:
    validated = validate_upstream_build_plan(candidate, plan)
    return {
        "archive_sha256": validated["archive_sha256"],
        "candidate_id": validated["candidate_id"],
        "decision": "validated_not_run",
        "toolchains": [
            {"node_version": node, "npm_version": npm}
            for node, npm in validated["toolchains"]
        ],
        "plan_id": validated["plan_id"],
        "runtime_admitted": False,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    client: ArchiveClient | None = None,
    executor: BuildExecutor | None = None,
) -> int:
    """Validate offline by default or run an explicitly live upstream build."""
    args = _parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    candidate_path = args.candidate.resolve(strict=False)
    plan_path = args.plan.resolve(strict=False)
    if args.mode == "validate":
        summary = _validation_summary(
            _load_json(candidate_path),
            _load_json(plan_path),
        )
    else:
        report = safe_report_path(args.report, repository_root)
        summary = run_live_build(
            candidate_path=candidate_path,
            plan_path=plan_path,
            report_path=report,
            client=client or GitHubCLIClient(),
            executor=executor or SubprocessBuildExecutor(),
        )
    print(json.dumps(summary, sort_keys=True))
    return 0


def _entrypoint() -> int:
    try:
        return main()
    except (FreeLLMAPIUpstreamBuildError, FreeLLMAPIAdmissionError) as exc:
        print(json.dumps({"fault": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
