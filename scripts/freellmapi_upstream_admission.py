#!/usr/bin/env python3
"""Refresh or validate a non-runnable FreeLLMAPI upstream candidate lock."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from general_ludd.models.freellmapi_upstream_admission import (
    FREELLMAPI_REPOSITORY,
    FreeLLMAPIAdmissionError,
    build_candidate_lock,
    validate_candidate_lock,
)

_REPOSITORY_API = f"repos/{FREELLMAPI_REPOSITORY}"
_STABLE_TAG_RE = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_JSON_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_ARCHIVE_RESPONSE_BYTES = 64 * 1024 * 1024
_MAX_LOCK_BYTES = 1024 * 1024
_GITHUB_TIMEOUT_SECONDS = 120
_MAX_TAG_INDIRECTIONS = 4


class FreeLLMAPIUpdateFault(StrEnum):
    """Content-free operator-workflow failures."""

    INPUT = "input_invalid"
    GITHUB_API = "github_api_failed"
    TAG_RESOLUTION = "tag_resolution"
    LOCK_IO = "candidate_lock_io"
    LOCK_INVALID = "candidate_lock_invalid"


class FreeLLMAPIUpdateError(RuntimeError):
    """Operator-workflow error that never includes response or credential text."""

    def __init__(self, fault: FreeLLMAPIUpdateFault) -> None:
        """Create an error containing only its stable fault category."""
        self.fault = fault
        super().__init__(fault.value)


class UpstreamClient(Protocol):
    """Narrow transport used by the updater."""

    def get_json(self, endpoint: str) -> Mapping[str, object]:
        """Return one metadata object from a fixed repository endpoint."""
        ...

    def get_bytes(self, endpoint: str) -> bytes:
        """Return one bounded byte response from a fixed repository endpoint."""
        ...


class GitHubCLIClient:
    """Use the maintained GitHub CLI and the caller's existing authentication."""

    def _request(self, endpoint: str) -> bytes:
        try:
            result = subprocess.run(
                ("gh", "api", "--method", "GET", endpoint),
                check=False,
                capture_output=True,
                timeout=_GITHUB_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.GITHUB_API) from exc
        if result.returncode != 0 or not isinstance(result.stdout, bytes):
            raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.GITHUB_API)
        return result.stdout

    def get_json(self, endpoint: str) -> Mapping[str, object]:
        """Fetch one bounded GitHub API object."""
        raw = self._request(endpoint)
        if not raw or len(raw) > _MAX_JSON_RESPONSE_BYTES:
            raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.GITHUB_API)
        try:
            parsed: object = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.GITHUB_API) from exc
        if not isinstance(parsed, dict):
            raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.GITHUB_API)
        return cast(dict[str, object], parsed)

    def get_bytes(self, endpoint: str) -> bytes:
        """Fetch one bounded source archive."""
        raw = self._request(endpoint)
        if not raw or len(raw) > _MAX_ARCHIVE_RESPONSE_BYTES:
            raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.GITHUB_API)
        return raw


def _metadata_object(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.TAG_RESOLUTION)
    return value


def _resolve_tag_ref(
    tag_ref: Mapping[str, object], *, expected_tag: str, client: UpstreamClient
) -> dict[str, object]:
    if tag_ref.get("ref") != f"refs/tags/{expected_tag}":
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.TAG_RESOLUTION)
    target = _metadata_object(tag_ref.get("object"))
    seen: set[str] = set()
    for _ in range(_MAX_TAG_INDIRECTIONS + 1):
        object_type = target.get("type")
        sha = target.get("sha")
        if not isinstance(sha, str) or _COMMIT_RE.fullmatch(sha) is None:
            raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.TAG_RESOLUTION)
        if object_type == "commit":
            return {
                "ref": f"refs/tags/{expected_tag}",
                "object": {"type": "commit", "sha": sha},
            }
        if object_type != "tag" or sha in seen:
            raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.TAG_RESOLUTION)
        seen.add(sha)
        annotated = client.get_json(f"{_REPOSITORY_API}/git/tags/{sha}")
        target = _metadata_object(annotated.get("object"))
    raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.TAG_RESOLUTION)


def _artifact_paths(repository_root: Path) -> tuple[Path, Path]:
    vendor = repository_root / "src/general_ludd/models/vendor/freellmapi"
    return vendor / "scoring_kernel.json", vendor / "scoring_kernel.js"


def _read_artifacts(repository_root: Path) -> tuple[bytes, bytes]:
    manifest_path, bundle_path = _artifact_paths(repository_root)
    try:
        return manifest_path.read_bytes(), bundle_path.read_bytes()
    except OSError as exc:
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.LOCK_IO) from exc


def _validate_inputs(expected_tag: str, expected_commit: str) -> None:
    if _STABLE_TAG_RE.fullmatch(expected_tag) is None or _COMMIT_RE.fullmatch(expected_commit) is None:
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.INPUT)


def fetch_candidate_lock(
    *,
    expected_tag: str,
    expected_commit: str,
    client: UpstreamClient,
    repository_root: Path,
) -> dict[str, object]:
    """Fetch fixed-repository evidence and build a validated candidate lock."""
    _validate_inputs(expected_tag, expected_commit)
    repository = client.get_json(_REPOSITORY_API)
    release = client.get_json(f"{_REPOSITORY_API}/releases/tags/{expected_tag}")
    unresolved_ref = client.get_json(f"{_REPOSITORY_API}/git/ref/tags/{expected_tag}")
    tag_ref = _resolve_tag_ref(unresolved_ref, expected_tag=expected_tag, client=client)
    commit = client.get_json(f"{_REPOSITORY_API}/commits/{expected_commit}")
    archive = client.get_bytes(f"{_REPOSITORY_API}/tarball/{expected_commit}")
    manifest, bundle = _read_artifacts(repository_root)
    candidate = build_candidate_lock(
        expected_tag=expected_tag,
        expected_commit=expected_commit,
        repository_metadata=repository,
        release_metadata=release,
        tag_ref_metadata=tag_ref,
        commit_metadata=commit,
        archive_bytes=archive,
        admitted_manifest_bytes=manifest,
        admitted_bundle_bytes=bundle,
    )
    validate_candidate_lock(
        candidate,
        expected_tag=expected_tag,
        expected_commit=expected_commit,
        admitted_manifest_bytes=manifest,
        admitted_bundle_bytes=bundle,
    )
    return candidate


def write_candidate_lock(output: Path, candidate: Mapping[str, object]) -> None:
    """Atomically replace one candidate lock without an invalid visible state."""
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(candidate, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        if len(payload) > _MAX_LOCK_BYTES:
            raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.LOCK_INVALID)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output.parent,
                prefix=f".{output.name}.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o644)
            temporary.replace(output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except FreeLLMAPIUpdateError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.LOCK_IO) from exc


def validate_existing_lock(
    output: Path,
    *,
    expected_tag: str,
    expected_commit: str,
    repository_root: Path,
) -> dict[str, object]:
    """Validate a tracked candidate without network access or writes."""
    _validate_inputs(expected_tag, expected_commit)
    try:
        raw = output.read_bytes()
    except OSError as exc:
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.LOCK_IO) from exc
    if not raw or len(raw) > _MAX_LOCK_BYTES:
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.LOCK_INVALID)
    try:
        parsed: object = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.LOCK_INVALID) from exc
    if not isinstance(parsed, dict):
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.LOCK_INVALID)
    candidate = cast(dict[str, object], parsed)
    manifest, bundle = _read_artifacts(repository_root)
    validate_candidate_lock(
        candidate,
        expected_tag=expected_tag,
        expected_commit=expected_commit,
        admitted_manifest_bytes=manifest,
        admitted_bundle_bytes=bundle,
    )
    return candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("validate", "refresh"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def _safe_output_path(output: Path, repository_root: Path) -> Path:
    candidate = output if output.is_absolute() else repository_root / output
    resolved = candidate.resolve(strict=False)
    config_root = (repository_root / "config/freellmapi").resolve(strict=False)
    if resolved.is_relative_to(config_root):
        return resolved
    temporary_root = Path("/tmp").resolve(strict=False)
    if resolved.is_relative_to(temporary_root):
        relative = resolved.relative_to(temporary_root)
        if relative.parts and relative.parts[0].startswith("gludd-freellmapi-"):
            return resolved
    raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.INPUT)


def _summary(candidate: Mapping[str, object], *, mode: str) -> dict[str, object]:
    upstream = _metadata_object(candidate.get("upstream"))
    decision = _metadata_object(candidate.get("decision"))
    return {
        "candidate_id": candidate.get("candidate_id"),
        "commit": upstream.get("commit"),
        "mode": mode,
        "runtime_admitted": decision.get("runtime_admitted"),
        "state": decision.get("state"),
        "tag": upstream.get("tag"),
    }


def main(argv: Sequence[str] | None = None, *, client: UpstreamClient | None = None) -> int:
    """Run one serial refresh or offline validation operation."""
    args = _parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    output = _safe_output_path(args.output, repository_root)
    if args.mode == "refresh":
        candidate = fetch_candidate_lock(
            expected_tag=args.tag,
            expected_commit=args.commit,
            client=client or GitHubCLIClient(),
            repository_root=repository_root,
        )
        write_candidate_lock(output, candidate)
    else:
        candidate = validate_existing_lock(
            output,
            expected_tag=args.tag,
            expected_commit=args.commit,
            repository_root=repository_root,
        )
    print(json.dumps(_summary(candidate, mode=args.mode), sort_keys=True))
    return 0


def _entrypoint() -> int:
    try:
        return main()
    except (FreeLLMAPIUpdateError, FreeLLMAPIAdmissionError) as exc:
        print(json.dumps({"fault": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
