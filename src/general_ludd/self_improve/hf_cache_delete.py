"""Fail-closed deletion of one exact Hugging Face cache artifact."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


class CacheDeletionError(RuntimeError):
    """Raised when exact cache deletion cannot be safely proven."""


class _CachedFileInfo(Protocol):
    file_name: str
    file_path: Path
    blob_path: Path


class _CachedRevisionInfo(Protocol):
    commit_hash: str
    snapshot_path: Path
    files: Iterable[_CachedFileInfo]


class _CachedRepoInfo(Protocol):
    repo_id: str
    repo_type: str
    repo_path: Path
    revisions: Iterable[_CachedRevisionInfo]


class _DeleteStrategy(Protocol):
    expected_freed_size: int | float
    repos: Iterable[Path]
    snapshots: Iterable[Path]
    refs: Iterable[Path]
    blobs: Iterable[Path]

    def execute(self) -> None: ...


class _CacheInfo(Protocol):
    warnings: Sequence[BaseException]
    repos: Iterable[_CachedRepoInfo]

    def delete_revisions(self, *revisions: str) -> _DeleteStrategy: ...


_CacheScanner = Callable[..., object]


@dataclass(frozen=True, slots=True)
class CacheArtifactIdentity:
    """Immutable Hub coordinates and local path for one cached artifact."""

    repo_id: str
    revision: str
    filename: str
    path: Path


@dataclass(frozen=True, slots=True)
class CacheDeletionPreview:
    """Side-effect-free evidence for a validated deletion strategy."""

    identity: CacheArtifactIdentity
    expected_freed_bytes: int


@dataclass(frozen=True, slots=True)
class CacheDeletionResult:
    """Evidence that an exact cache revision was deleted and rescanned."""

    identity: CacheArtifactIdentity
    expected_freed_bytes: int
    verified_absent: bool


@dataclass(frozen=True, slots=True)
class CacheDeletionPlan:
    """Validated preview that rescans immediately before an exact deletion."""

    identity: CacheArtifactIdentity
    expected_freed_bytes: int
    _adapter: HuggingFaceCacheDeletion = field(repr=False, compare=False)

    def dry_run(self) -> CacheDeletionPreview:
        """Return immutable deletion evidence without executing the strategy."""
        return CacheDeletionPreview(
            identity=self.identity,
            expected_freed_bytes=self.expected_freed_bytes,
        )

    def execute_and_verify(self) -> CacheDeletionResult:
        """Rescan, execute the current exact strategy, and prove absence."""
        return self._adapter._execute_and_verify(self.identity)


@dataclass(frozen=True, slots=True)
class _PreparedDeletion:
    strategy: _DeleteStrategy
    expected_freed_bytes: int


def _default_scanner(*, cache_dir: Path) -> _CacheInfo:
    from huggingface_hub import scan_cache_dir

    return cast(_CacheInfo, scan_cache_dir(cache_dir=cache_dir))


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _paths_from_strategy(strategy: _DeleteStrategy, field_name: str) -> frozenset[Path]:
    try:
        raw_paths = getattr(strategy, field_name)
        return frozenset(_lexical_absolute(Path(path)) for path in raw_paths)
    except (AttributeError, OSError, TypeError, ValueError):
        raise CacheDeletionError("cache deletion strategy is invalid") from None


class HuggingFaceCacheDeletion:
    """Plan and prove revision deletion through Hugging Face's cache API."""

    __slots__ = ("_cache_root", "_scanner")

    def __init__(
        self,
        cache_root: Path,
        *,
        scanner: _CacheScanner | None = None,
    ) -> None:
        """Bind deletion to one existing cache root and injectable scanner."""
        try:
            resolved_root = Path(cache_root).resolve(strict=True)
        except (OSError, RuntimeError):
            raise CacheDeletionError("cache root is unavailable") from None
        if not resolved_root.is_dir():
            raise CacheDeletionError("cache root is unavailable")
        self._cache_root = resolved_root
        self._scanner = scanner or _default_scanner

    def plan(self, identity: CacheArtifactIdentity) -> CacheDeletionPlan:
        """Validate one exact artifact and return a side-effect-free plan."""
        validated = self._validate_identity(identity)
        prepared = self._prepare(validated)
        return CacheDeletionPlan(
            identity=validated,
            expected_freed_bytes=prepared.expected_freed_bytes,
            _adapter=self,
        )

    def _validate_identity(
        self,
        identity: CacheArtifactIdentity,
    ) -> CacheArtifactIdentity:
        repo_id = identity.repo_id
        repo_parts = repo_id.split("/")
        if (
            not repo_id
            or repo_id.strip() != repo_id
            or "\\" in repo_id
            or any(part in {"", ".", ".."} for part in repo_parts)
            or len(repo_parts) > 2
        ):
            raise CacheDeletionError("repository identity is invalid")

        if _REVISION_RE.fullmatch(identity.revision) is None:
            raise CacheDeletionError("immutable revision is invalid")

        filename = identity.filename
        filename_path = PurePosixPath(filename)
        if (
            not filename
            or "\\" in filename
            or filename_path.is_absolute()
            or filename_path.as_posix() != filename
            or any(part in {"", ".", ".."} for part in filename_path.parts)
        ):
            raise CacheDeletionError("artifact filename is invalid")

        path = Path(identity.path)
        if not path.is_absolute():
            raise CacheDeletionError("artifact path must be absolute")
        lexical_path = _lexical_absolute(path)
        if not _is_within(lexical_path, self._cache_root):
            raise CacheDeletionError("artifact path is outside cache root")
        try:
            resolved_path = lexical_path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise CacheDeletionError("exact artifact was not found") from None
        if not resolved_path.is_file() or not _is_within(
            resolved_path,
            self._cache_root,
        ):
            raise CacheDeletionError("artifact path is outside cache root")

        return CacheArtifactIdentity(
            repo_id=repo_id,
            revision=identity.revision,
            filename=filename,
            path=lexical_path,
        )

    def _scan(self) -> _CacheInfo:
        try:
            cache_info = cast(
                _CacheInfo,
                self._scanner(cache_dir=self._cache_root),
            )
            warnings = cache_info.warnings
        except Exception:
            raise CacheDeletionError("cache scan failed") from None
        if warnings:
            raise CacheDeletionError("cache scan reported warnings")
        return cache_info

    def _prepare(self, identity: CacheArtifactIdentity) -> _PreparedDeletion:
        cache_info = self._scan()
        owners: list[tuple[_CachedRepoInfo, _CachedRevisionInfo]] = []
        try:
            repos = tuple(cache_info.repos)
            for repo in repos:
                for revision in repo.revisions:
                    if revision.commit_hash == identity.revision:
                        owners.append((repo, revision))
        except (AttributeError, TypeError):
            raise CacheDeletionError("cache scan result is invalid") from None

        if len(owners) != 1:
            if owners:
                raise CacheDeletionError("cache revision is not unique")
            raise CacheDeletionError("exact artifact was not found")

        repo, revision = owners[0]
        if repo.repo_id != identity.repo_id or repo.repo_type != "model":
            raise CacheDeletionError("exact artifact was not found")

        repo_path = _lexical_absolute(Path(repo.repo_path))
        if not _is_within(repo_path, self._cache_root):
            raise CacheDeletionError("cache repository is outside cache root")
        try:
            resolved_repo_path = repo_path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise CacheDeletionError("cache repository is unavailable") from None
        if resolved_repo_path != repo_path or not resolved_repo_path.is_dir():
            raise CacheDeletionError("cache repository path is not canonical")

        snapshot_path = _lexical_absolute(Path(revision.snapshot_path))
        expected_snapshot = repo_path / "snapshots" / identity.revision
        if snapshot_path != expected_snapshot:
            raise CacheDeletionError("cache snapshot identity is invalid")

        expected_file_path = snapshot_path.joinpath(*PurePosixPath(identity.filename).parts)
        if expected_file_path != identity.path:
            raise CacheDeletionError("exact artifact was not found")

        try:
            exact_files = [
                cached_file
                for cached_file in revision.files
                if cached_file.file_name == PurePosixPath(identity.filename).name
                and _lexical_absolute(Path(cached_file.file_path)) == identity.path
            ]
        except (AttributeError, OSError, TypeError, ValueError):
            raise CacheDeletionError("cache scan result is invalid") from None
        if len(exact_files) != 1:
            raise CacheDeletionError("exact artifact was not found")

        cached_file = exact_files[0]
        try:
            resolved_artifact = identity.path.resolve(strict=True)
            resolved_blob = Path(cached_file.blob_path).resolve(strict=True)
        except (OSError, RuntimeError):
            raise CacheDeletionError("exact artifact was not found") from None
        if (
            resolved_artifact != resolved_blob
            or not _is_within(resolved_blob, repo_path)
            or not resolved_blob.is_file()
        ):
            raise CacheDeletionError("cache artifact target is invalid")

        try:
            strategy = cache_info.delete_revisions(identity.revision)
        except Exception:
            raise CacheDeletionError("cache deletion planning failed") from None
        expected_freed_bytes = self._validate_strategy(
            strategy,
            repo_path=repo_path,
            snapshot_path=snapshot_path,
        )
        return _PreparedDeletion(
            strategy=strategy,
            expected_freed_bytes=expected_freed_bytes,
        )

    def _validate_strategy(
        self,
        strategy: _DeleteStrategy,
        *,
        repo_path: Path,
        snapshot_path: Path,
    ) -> int:
        repos = _paths_from_strategy(strategy, "repos")
        snapshots = _paths_from_strategy(strategy, "snapshots")
        refs = _paths_from_strategy(strategy, "refs")
        blobs = _paths_from_strategy(strategy, "blobs")
        for path in repos | snapshots | refs | blobs:
            if not _is_within(path, repo_path):
                raise CacheDeletionError(
                    "cache deletion strategy reaches outside exact repository"
                )

        deletes_repo = repos == frozenset({repo_path})
        deletes_snapshot = not repos and snapshots == frozenset({snapshot_path})
        if not (deletes_repo or deletes_snapshot):
            raise CacheDeletionError(
                "cache deletion strategy does not target exact revision"
            )
        if deletes_repo and (snapshots or refs or blobs):
            raise CacheDeletionError("cache deletion strategy is invalid")
        if deletes_snapshot:
            refs_root = repo_path / "refs"
            blobs_root = repo_path / "blobs"
            if any(not _is_within(path, refs_root) for path in refs):
                raise CacheDeletionError("cache deletion strategy is invalid")
            if any(not _is_within(path, blobs_root) for path in blobs):
                raise CacheDeletionError("cache deletion strategy is invalid")

        expected = strategy.expected_freed_size
        if (
            isinstance(expected, bool)
            or not isinstance(expected, (int, float))
            or not math.isfinite(expected)
            or expected < 0
            or int(expected) != expected
        ):
            raise CacheDeletionError("cache deletion strategy is invalid")
        if not callable(getattr(strategy, "execute", None)):
            raise CacheDeletionError("cache deletion strategy is invalid")
        return int(expected)

    def _execute_and_verify(
        self,
        identity: CacheArtifactIdentity,
    ) -> CacheDeletionResult:
        prepared = self._prepare(identity)
        try:
            prepared.strategy.execute()
        except Exception:
            raise CacheDeletionError("cache deletion execution failed") from None

        try:
            self._verify_absent(identity)
        except CacheDeletionError:
            raise CacheDeletionError("deletion verification failed") from None
        return CacheDeletionResult(
            identity=identity,
            expected_freed_bytes=prepared.expected_freed_bytes,
            verified_absent=True,
        )

    def _verify_absent(self, identity: CacheArtifactIdentity) -> None:
        cache_info = self._scan()
        if identity.path.exists() or identity.path.is_symlink():
            raise CacheDeletionError("exact artifact remains after deletion")
        try:
            for repo in cache_info.repos:
                if repo.repo_id != identity.repo_id or repo.repo_type != "model":
                    continue
                for revision in repo.revisions:
                    if revision.commit_hash == identity.revision:
                        raise CacheDeletionError(
                            "exact revision remains after deletion"
                        )
        except (AttributeError, TypeError):
            raise CacheDeletionError("cache scan result is invalid") from None


__all__ = [
    "CacheArtifactIdentity",
    "CacheDeletionError",
    "CacheDeletionPlan",
    "CacheDeletionPreview",
    "CacheDeletionResult",
    "HuggingFaceCacheDeletion",
]
