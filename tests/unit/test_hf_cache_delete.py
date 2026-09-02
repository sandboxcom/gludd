"""Exact Hugging Face cache deletion adapter tests."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.self_improve.hf_cache_delete import (
    CacheArtifactIdentity,
    CacheDeletionError,
    HuggingFaceCacheDeletion,
)

_REVISION = "a" * 40
_FILENAME = "weights/model.gguf"


class _FakeStrategy:
    def __init__(
        self,
        *,
        repo_path: Path,
        snapshot_path: Path,
        target_path: Path,
        state: dict[str, bool],
        execute_error: BaseException | None = None,
        leave_present: bool = False,
        override: dict[str, object] | None = None,
        expected_freed_size: object = 4,
    ) -> None:
        self.expected_freed_size = expected_freed_size
        self.repos = frozenset({repo_path})
        self.snapshots: frozenset[Path] = frozenset()
        self.refs: frozenset[Path] = frozenset()
        self.blobs: frozenset[Path] = frozenset()
        for name, value in (override or {}).items():
            setattr(self, name, value)
        self._snapshot_path = snapshot_path
        self._target_path = target_path
        self._state = state
        self._execute_error = execute_error
        self._leave_present = leave_present

    def execute(self) -> None:
        self._state["executed"] = True
        if self._execute_error is not None:
            raise self._execute_error
        if not self._leave_present:
            self._target_path.unlink()
            self._state["deleted"] = True


class _FakeCacheInfo:
    def __init__(
        self,
        *,
        repos: tuple[SimpleNamespace, ...],
        strategy: object | None,
        warnings: tuple[BaseException, ...] = (),
        observed: list[tuple[str, ...]] | None = None,
    ) -> None:
        self.repos = repos
        self.warnings = warnings
        self._strategy = strategy
        self._observed = observed if observed is not None else []

    def delete_revisions(self, *revisions: str) -> object:
        self._observed.append(("delete_revisions", *revisions))
        if self._strategy is None:
            raise AssertionError("delete_revisions must not be called")
        return self._strategy


def _cache_fixture(
    tmp_path: Path,
    *,
    strategy_override: dict[str, object] | None = None,
    execute_error: BaseException | None = None,
    expected_freed_size: object = 4,
    leave_present: bool = False,
) -> tuple[
    Path,
    CacheArtifactIdentity,
    SimpleNamespace,
    _FakeStrategy,
    dict[str, bool],
]:
    cache_root = tmp_path / "cache"
    repo_path = cache_root / "models--example--model"
    snapshot_path = repo_path / "snapshots" / _REVISION
    target_path = snapshot_path / _FILENAME
    target_path.parent.mkdir(parents=True)
    target_path.write_bytes(b"GGUF")

    cached_file = SimpleNamespace(
        file_name=PurePosixPath(_FILENAME).name,
        file_path=target_path,
        blob_path=target_path,
        size_on_disk=4,
    )
    revision = SimpleNamespace(
        commit_hash=_REVISION,
        snapshot_path=snapshot_path,
        files=(cached_file,),
    )
    repo = SimpleNamespace(
        repo_id="example/model",
        repo_type="model",
        repo_path=repo_path,
        revisions=(revision,),
    )
    state = {"executed": False, "deleted": False}
    strategy = _FakeStrategy(
        repo_path=repo_path,
        snapshot_path=snapshot_path,
        target_path=target_path,
        state=state,
        execute_error=execute_error,
        leave_present=leave_present,
        override=strategy_override,
        expected_freed_size=expected_freed_size,
    )
    identity = CacheArtifactIdentity(
        repo_id="example/model",
        revision=_REVISION,
        filename=_FILENAME,
        path=target_path,
    )
    return cache_root, identity, repo, strategy, state


def test_plan_dry_run_then_execute_rescans_and_verifies_absence(
    tmp_path: Path,
) -> None:
    cache_root, identity, repo, strategy, state = _cache_fixture(tmp_path)
    observed: list[tuple[str, ...]] = []
    scan_calls: list[Path] = []

    def scanner(*, cache_dir: Path) -> _FakeCacheInfo:
        scan_calls.append(cache_dir)
        repos = () if state["deleted"] else (repo,)
        return _FakeCacheInfo(
            repos=repos,
            strategy=strategy,
            observed=observed,
        )

    plan = HuggingFaceCacheDeletion(cache_root, scanner=scanner).plan(identity)

    preview = plan.dry_run()
    assert preview.identity == identity
    assert preview.expected_freed_bytes == 4
    assert state == {"executed": False, "deleted": False}
    assert scan_calls == [cache_root.resolve()]

    result = plan.execute_and_verify()

    assert result.identity == identity
    assert result.expected_freed_bytes == 4
    assert result.verified_absent is True
    assert state == {"executed": True, "deleted": True}
    assert scan_calls == [cache_root.resolve()] * 3
    assert observed == [
        ("delete_revisions", _REVISION),
        ("delete_revisions", _REVISION),
    ]


@pytest.mark.parametrize(
    ("identity_update", "message"),
    [
        ({"repo_id": "../model"}, "repository identity"),
        ({"revision": "main"}, "immutable revision"),
        ({"filename": "../model.gguf"}, "artifact filename"),
    ],
)
def test_invalid_identity_is_rejected_before_scan(
    tmp_path: Path,
    identity_update: dict[str, Any],
    message: str,
) -> None:
    cache_root, identity, _repo, _strategy, _state = _cache_fixture(tmp_path)
    values: dict[str, Any] = {
        "repo_id": identity.repo_id,
        "revision": identity.revision,
        "filename": identity.filename,
        "path": identity.path,
    }
    values.update(identity_update)
    scanner_called = False

    def scanner(*, cache_dir: Path) -> _FakeCacheInfo:
        del cache_dir
        nonlocal scanner_called
        scanner_called = True
        raise AssertionError("scanner must not run")

    with pytest.raises(CacheDeletionError, match=message):
        HuggingFaceCacheDeletion(cache_root, scanner=scanner).plan(
            CacheArtifactIdentity(**values)
        )

    assert scanner_called is False


def test_path_outside_cache_root_is_rejected_before_scan(tmp_path: Path) -> None:
    cache_root, identity, _repo, _strategy, _state = _cache_fixture(tmp_path)
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"GGUF")

    with pytest.raises(CacheDeletionError, match="outside cache root"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: pytest.fail("scanner must not run"),
        ).plan(
            CacheArtifactIdentity(
                repo_id=identity.repo_id,
                revision=identity.revision,
                filename=identity.filename,
                path=outside,
            )
        )


def test_scan_warnings_and_exceptions_are_bounded_and_fail_closed(
    tmp_path: Path,
) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(tmp_path)

    def warning_scanner(*, cache_dir: Path) -> _FakeCacheInfo:
        del cache_dir
        return _FakeCacheInfo(
            repos=(repo,),
            strategy=strategy,
            warnings=(RuntimeError("private corrupt path"),),
        )

    with pytest.raises(CacheDeletionError, match="cache scan reported warnings") as raised:
        HuggingFaceCacheDeletion(cache_root, scanner=warning_scanner).plan(identity)
    assert "private corrupt path" not in str(raised.value)

    def failing_scanner(*, cache_dir: Path) -> _FakeCacheInfo:
        del cache_dir
        raise OSError("private filesystem detail")

    with pytest.raises(CacheDeletionError, match="cache scan failed") as raised:
        HuggingFaceCacheDeletion(cache_root, scanner=failing_scanner).plan(identity)
    assert "private filesystem detail" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_exact_repo_revision_filename_and_path_must_all_match(
    tmp_path: Path,
) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(tmp_path)
    wrong_file = tmp_path / "cache" / "models--example--model" / "other.gguf"
    wrong_file.write_bytes(b"GGUF")
    wrong_identity = CacheArtifactIdentity(
        repo_id=identity.repo_id,
        revision=identity.revision,
        filename=identity.filename,
        path=wrong_file,
    )

    with pytest.raises(CacheDeletionError, match="exact artifact was not found"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(wrong_identity)


def test_revision_collision_across_repositories_is_rejected(
    tmp_path: Path,
) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(tmp_path)
    other_repo_path = cache_root / "models--other--model"
    other_snapshot = other_repo_path / "snapshots" / _REVISION
    other_snapshot.mkdir(parents=True)
    other_repo = SimpleNamespace(
        repo_id="other/model",
        repo_type="model",
        repo_path=other_repo_path,
        revisions=(
            SimpleNamespace(
                commit_hash=_REVISION,
                snapshot_path=other_snapshot,
                files=(),
            ),
        ),
    )

    with pytest.raises(CacheDeletionError, match="revision is not unique"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo, other_repo),
                strategy=strategy,
            ),
        ).plan(identity)


@pytest.mark.parametrize("field", ["repos", "snapshots", "refs", "blobs"])
def test_cross_repository_strategy_is_rejected(
    tmp_path: Path,
    field: str,
) -> None:
    outside = tmp_path / "cache" / "models--other--model"
    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path,
        strategy_override={field: frozenset({outside})},
    )

    with pytest.raises(CacheDeletionError, match="outside exact repository"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)


def test_strategy_must_target_the_exact_revision(tmp_path: Path) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path,
        strategy_override={"repos": frozenset()},
    )

    with pytest.raises(CacheDeletionError, match="does not target exact revision"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)


def test_cache_root_must_be_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(CacheDeletionError, match="cache root is unavailable"):
        HuggingFaceCacheDeletion(tmp_path / "missing")

    cache_file = tmp_path / "cache-file"
    cache_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(CacheDeletionError, match="cache root is unavailable"):
        HuggingFaceCacheDeletion(cache_file)


def test_artifact_path_must_be_absolute_present_and_confined(
    tmp_path: Path,
) -> None:
    cache_root, identity, _repo, _strategy, _state = _cache_fixture(tmp_path)
    adapter = HuggingFaceCacheDeletion(
        cache_root,
        scanner=lambda **_kwargs: pytest.fail("scanner must not run"),
    )

    with pytest.raises(CacheDeletionError, match="path must be absolute"):
        adapter.plan(
            CacheArtifactIdentity(
                repo_id=identity.repo_id,
                revision=identity.revision,
                filename=identity.filename,
                path=Path("relative.gguf"),
            )
        )

    with pytest.raises(CacheDeletionError, match="exact artifact was not found"):
        adapter.plan(
            CacheArtifactIdentity(
                repo_id=identity.repo_id,
                revision=identity.revision,
                filename=identity.filename,
                path=cache_root / "missing.gguf",
            )
        )

    directory = cache_root / "not-a-file"
    directory.mkdir()
    with pytest.raises(CacheDeletionError, match="outside cache root"):
        adapter.plan(
            CacheArtifactIdentity(
                repo_id=identity.repo_id,
                revision=identity.revision,
                filename=identity.filename,
                path=directory,
            )
        )

    outside = tmp_path / "outside-target.gguf"
    outside.write_bytes(b"GGUF")
    escaped = cache_root / "escaped.gguf"
    escaped.symlink_to(outside)
    with pytest.raises(CacheDeletionError, match="outside cache root"):
        adapter.plan(
            CacheArtifactIdentity(
                repo_id=identity.repo_id,
                revision=identity.revision,
                filename=identity.filename,
                path=escaped,
            )
        )


def test_invalid_scan_shape_and_repo_identity_fail_closed(tmp_path: Path) -> None:
    cache_root, identity, _repo, _strategy, _state = _cache_fixture(tmp_path)
    invalid_info = SimpleNamespace(warnings=(), repos=(SimpleNamespace(),))
    with pytest.raises(CacheDeletionError, match="cache scan result is invalid"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: invalid_info,
        ).plan(identity)

    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path / "identity"
    )
    repo.repo_type = "dataset"
    with pytest.raises(CacheDeletionError, match="exact artifact was not found"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)


def test_repository_and_snapshot_metadata_must_be_canonical(
    tmp_path: Path,
) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path / "outside"
    )
    foreign_repo = tmp_path / "foreign-repo"
    foreign_repo.mkdir()
    repo.repo_path = foreign_repo
    with pytest.raises(CacheDeletionError, match="repository is outside"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)

    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path / "missing"
    )
    repo.repo_path = cache_root / "missing-repo"
    with pytest.raises(CacheDeletionError, match="repository is unavailable"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)

    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path / "alias"
    )
    repo_alias = cache_root / "models--alias--model"
    repo_alias.symlink_to(repo.repo_path, target_is_directory=True)
    repo.repo_path = repo_alias
    with pytest.raises(CacheDeletionError, match="path is not canonical"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)

    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path / "snapshot"
    )
    revision = repo.revisions[0]
    wrong_snapshot = Path(repo.repo_path) / "snapshots" / ("c" * 40)
    wrong_snapshot.mkdir(parents=True)
    revision.snapshot_path = wrong_snapshot
    with pytest.raises(CacheDeletionError, match="snapshot identity is invalid"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)


def test_cached_file_metadata_and_planning_fail_closed(tmp_path: Path) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path / "malformed"
    )
    repo.revisions[0].files = (SimpleNamespace(file_name="model.gguf"),)
    with pytest.raises(CacheDeletionError, match="cache scan result is invalid"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)

    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path / "blob"
    )
    foreign_blob = tmp_path / "foreign-blob"
    foreign_blob.write_bytes(b"GGUF")
    repo.revisions[0].files[0].blob_path = foreign_blob
    with pytest.raises(CacheDeletionError, match="artifact target is invalid"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)

    cache_root, identity, repo, _strategy, _state = _cache_fixture(
        tmp_path / "planning"
    )
    with pytest.raises(CacheDeletionError, match="deletion planning failed"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=None,
            ),
        ).plan(identity)


def test_partial_revision_strategy_is_scoped_to_hf_layout(tmp_path: Path) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(tmp_path)
    repo_path = Path(repo.repo_path)
    snapshot_path = Path(repo.revisions[0].snapshot_path)
    strategy.repos = frozenset()
    strategy.snapshots = frozenset({snapshot_path})
    strategy.refs = frozenset({repo_path / "refs" / "main"})
    strategy.blobs = frozenset({repo_path / "blobs" / ("b" * 64)})

    preview = HuggingFaceCacheDeletion(
        cache_root,
        scanner=lambda **_kwargs: _FakeCacheInfo(
            repos=(repo,),
            strategy=strategy,
        ),
    ).plan(identity).dry_run()

    assert preview.expected_freed_bytes == 4


@pytest.mark.parametrize(
    ("strategy_update", "message"),
    [
        ({"snapshots": frozenset({_REVISION})}, "strategy is invalid"),
        ({"refs": frozenset({_REVISION})}, "strategy is invalid"),
        ({"blobs": frozenset({_REVISION})}, "strategy is invalid"),
    ],
)
def test_contained_but_wrong_strategy_subtree_is_rejected(
    tmp_path: Path,
    strategy_update: dict[str, object],
    message: str,
) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(tmp_path)
    repo_path = Path(repo.repo_path)
    snapshot_path = Path(repo.revisions[0].snapshot_path)
    strategy.repos = frozenset()
    strategy.snapshots = frozenset({snapshot_path})
    if "snapshots" in strategy_update:
        strategy.repos = frozenset({repo_path})
        strategy.snapshots = frozenset({snapshot_path})
    if "refs" in strategy_update:
        strategy.refs = frozenset({repo_path / "wrong-ref"})
    if "blobs" in strategy_update:
        strategy.blobs = frozenset({repo_path / "wrong-blob"})

    with pytest.raises(CacheDeletionError, match=message):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)


@pytest.mark.parametrize(
    "expected_freed_size",
    [True, -1, 0.5, float("inf"), "4"],
)
def test_strategy_size_must_be_a_bounded_nonnegative_integer(
    tmp_path: Path,
    expected_freed_size: object,
) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path,
        expected_freed_size=expected_freed_size,
    )
    with pytest.raises(CacheDeletionError, match="strategy is invalid"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=strategy,
            ),
        ).plan(identity)


def test_strategy_shape_and_execute_callback_are_required(tmp_path: Path) -> None:
    cache_root, identity, repo, _strategy, _state = _cache_fixture(tmp_path)
    missing_paths = SimpleNamespace(expected_freed_size=4, execute=lambda: None)
    with pytest.raises(CacheDeletionError, match="strategy is invalid"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=missing_paths,
            ),
        ).plan(identity)

    repo_path = Path(repo.repo_path)
    no_execute = SimpleNamespace(
        expected_freed_size=4,
        repos=frozenset({repo_path}),
        snapshots=frozenset(),
        refs=frozenset(),
        blobs=frozenset(),
    )
    with pytest.raises(CacheDeletionError, match="strategy is invalid"):
        HuggingFaceCacheDeletion(
            cache_root,
            scanner=lambda **_kwargs: _FakeCacheInfo(
                repos=(repo,),
                strategy=no_execute,
            ),
        ).plan(identity)


def test_verification_requires_revision_to_leave_rescanned_inventory(
    tmp_path: Path,
) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(tmp_path)
    adapter = HuggingFaceCacheDeletion(
        cache_root,
        scanner=lambda **_kwargs: _FakeCacheInfo(
            repos=(repo,),
            strategy=strategy,
        ),
    )

    with pytest.raises(CacheDeletionError, match="deletion verification failed"):
        adapter.plan(identity).execute_and_verify()


def test_default_scanner_deletes_a_real_hf_cache_layout(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    repo_path = cache_root / "models--example--model"
    blob_path = repo_path / "blobs" / ("b" * 64)
    blob_path.parent.mkdir(parents=True)
    blob_path.write_bytes(b"GGUF")

    snapshot_path = repo_path / "snapshots" / _REVISION
    target_path = snapshot_path / _FILENAME
    target_path.parent.mkdir(parents=True)
    target_path.symlink_to(blob_path)
    refs_path = repo_path / "refs"
    refs_path.mkdir()
    (refs_path / "main").write_text(_REVISION, encoding="utf-8")

    identity = CacheArtifactIdentity(
        repo_id="example/model",
        revision=_REVISION,
        filename=_FILENAME,
        path=target_path,
    )
    plan = HuggingFaceCacheDeletion(cache_root).plan(identity)

    assert plan.dry_run().expected_freed_bytes == 4
    result = plan.execute_and_verify()
    assert result.verified_absent is True
    assert not repo_path.exists()


def test_execution_error_and_failed_verification_are_bounded(
    tmp_path: Path,
) -> None:
    cache_root, identity, repo, strategy, _state = _cache_fixture(
        tmp_path,
        execute_error=OSError("private deletion detail"),
    )
    adapter = HuggingFaceCacheDeletion(
        cache_root,
        scanner=lambda **_kwargs: _FakeCacheInfo(
            repos=(repo,),
            strategy=strategy,
        ),
    )
    with pytest.raises(CacheDeletionError, match="cache deletion execution failed") as raised:
        adapter.plan(identity).execute_and_verify()
    assert "private deletion detail" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True

    other_tmp = tmp_path / "verification"
    cache_root, identity, repo, strategy, _state = _cache_fixture(
        other_tmp,
        leave_present=True,
    )
    adapter = HuggingFaceCacheDeletion(
        cache_root,
        scanner=lambda **_kwargs: _FakeCacheInfo(
            repos=(repo,),
            strategy=strategy,
        ),
    )
    with pytest.raises(CacheDeletionError, match="deletion verification failed"):
        adapter.plan(identity).execute_and_verify()
