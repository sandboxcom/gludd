"""Atomic reservations for immutable self-improvement model plans."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest

import general_ludd.self_improve.model_lifecycle as lifecycle
from general_ludd.local_model import LocalModelConfig
from general_ludd.self_improve.hf_cache_delete import CacheArtifactIdentity
from general_ludd.self_improve.model_lifecycle import ModelArtifactIdentity, ModelLeaseManager
from general_ludd.small_models.download import DownloadedModel, DownloadSource

_REV_A = "a" * 40
_REV_B = "b" * 40
_REV_C = "c" * 40
_BIRTH = 123.25


def _config(name: str, repo: str) -> LocalModelConfig:
    return LocalModelConfig(
        name=name,
        repo=repo,
        filename=f"{name}.Q4_K_M.gguf",
        size_mb=1,
        category="coding",
        ci_safe=True,
    )


def _identity(config: LocalModelConfig, revision: str) -> ModelArtifactIdentity:
    return ModelArtifactIdentity(config.name, config.repo, config.filename, revision)


class _Downloader:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[str] = []

    def download_gguf(
        self,
        model_id: str,
        filename: str,
        revision: str | None = None,
        *,
        local_files_only: bool = False,
    ) -> DownloadedModel:
        del local_files_only
        assert revision is not None
        path = (
            self.root
            / f"models--{model_id.replace('/', '--')}"
            / "snapshots"
            / revision
            / filename
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"GGUF" + model_id.encode())
        self.calls.append(model_id)
        return DownloadedModel(
            model_id=model_id,
            local_path=str(path),
            source=DownloadSource.GGUF,
            filename=filename,
            revision=revision,
            size_bytes=path.stat().st_size,
        )


def _manager(
    tmp_path: Path,
    downloader: _Downloader,
    configs: dict[str, LocalModelConfig],
    revisions: dict[str, str],
    *,
    quota: int = 64 * 1024 * 1024,
    process_started: Callable[[int], float | None] | None = None,
) -> ModelLeaseManager:
    return ModelLeaseManager(
        cache_root=tmp_path / "cache",
        quota_bytes=quota,
        reserve_bytes=0,
        model_selector=lambda task: configs[task],
        revision_resolver=lambda repo: revisions[repo],
        downloader_factory=lambda _root: downloader,
        disk_free=lambda _root: 1 << 50,
        process_started=process_started
        or (lambda pid: _BIRTH if pid == os.getpid() else None),
    )


def _seed(manager: ModelLeaseManager, task: str) -> tuple[Path, Path]:
    with manager.acquire(task) as model:
        return model.path, model.manifest_path


def _deleter(
    deleted: list[str],
    paths: dict[str, Path],
) -> Callable[[Path, str], None]:
    def delete(_cache_root: Path, revision: str) -> None:
        deleted.append(revision)
        paths[revision].unlink()

    return delete


def test_atomic_plan_reservation_prevents_retry_churn_and_releases(
    tmp_path: Path,
) -> None:
    configs = {
        "first": _config("first-coder", "example/first"),
        "second": _config("second-coder", "example/second"),
    }
    revisions = {"example/first": _REV_A, "example/second": _REV_B}
    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(tmp_path, downloader, configs, revisions)
    first_path, _ = _seed(manager, "first")
    second_path, _ = _seed(manager, "second")
    pressure = _manager(
        tmp_path,
        downloader,
        configs,
        revisions,
        quota=second_path.stat().st_size,
    )
    deleted: list[str] = []
    pressure._delete_revision = _deleter(
        deleted, {_REV_A: first_path, _REV_B: second_path}
    )
    first_identity = _identity(configs["first"], _REV_A)
    calls_before = tuple(downloader.calls)
    identities = (
        first_identity,
        _identity(configs["second"], _REV_B),
    )

    with pressure.reserve_plan(identities) as reservation:
        assert reservation.path.is_file()
        with pytest.raises(RuntimeError, match="insufficient model cache headroom"):
            pressure.reclaim(required_bytes=0)
        assert first_path.is_file() and second_path.is_file()
        assert deleted == []

        reservation.mark_failed(first_identity)
        assert pressure.reclaim(required_bytes=0) == (first_path,)
        assert deleted == [_REV_A]
        assert second_path.is_file()
        with pressure.acquire(
            "second",
            model_config=configs["second"],
            resolved_revision=_REV_B,
        ) as acquired:
            assert acquired.path == second_path
        assert tuple(downloader.calls) == calls_before

    assert not reservation.path.exists()
    assert tuple((pressure.cache_root / ".gludd" / "reservations").glob("*.json")) == ()


def test_failed_exact_identity_precedes_lru_but_live_protection_wins(
    tmp_path: Path,
) -> None:
    configs = {
        "old": _config("old-coder", "example/old"),
        "failed": _config("failed-coder", "example/failed"),
    }
    revisions = {"example/old": _REV_A, "example/failed": _REV_B}
    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(tmp_path, downloader, configs, revisions)
    old_path, _ = _seed(manager, "old")
    failed_path, _ = _seed(manager, "failed")
    pressure = _manager(
        tmp_path, downloader, configs, revisions, quota=old_path.stat().st_size
    )
    deleted: list[str] = []
    pressure._delete_revision = _deleter(
        deleted, {_REV_A: old_path, _REV_B: failed_path}
    )
    failed = _identity(configs["failed"], _REV_B)

    with pressure.reserve_plan(
        (ModelArtifactIdentity("future", "example/future", "future.gguf", _REV_C),),
        failure_hints=(failed,),
    ):
        assert pressure.reclaim(required_bytes=0) == (failed_path,)
    assert deleted == [_REV_B]
    assert old_path.is_file()


def test_live_protection_overrides_matching_failure_hint(tmp_path: Path) -> None:
    configs = {
        "protected": _config("protected-coder", "example/protected"),
        "other": _config("other-coder", "example/other"),
    }
    revisions = {"example/protected": _REV_A, "example/other": _REV_B}
    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(tmp_path, downloader, configs, revisions)
    protected_path, _ = _seed(manager, "protected")
    other_path, _ = _seed(manager, "other")
    pressure = _manager(
        tmp_path, downloader, configs, revisions, quota=protected_path.stat().st_size
    )
    deleted: list[str] = []
    pressure._delete_revision = _deleter(
        deleted, {_REV_A: protected_path, _REV_B: other_path}
    )
    protected = _identity(configs["protected"], _REV_A)

    with pressure.reserve_plan((protected,), failure_hints=(protected,)):
        assert pressure.reclaim(required_bytes=0) == (other_path,)

    assert deleted == [_REV_B]
    assert protected_path.is_file()


def test_future_identity_is_protected_when_it_materializes(
    tmp_path: Path,
) -> None:
    configs = {
        "other": _config("other-coder", "example/other"),
        "future": _config("future-coder", "example/future"),
    }
    revisions = {"example/other": _REV_A, "example/future": _REV_B}
    downloader = _Downloader(tmp_path / "cache")
    owner = _manager(tmp_path, downloader, configs, revisions)
    other_path, _ = _seed(owner, "other")
    future = _identity(configs["future"], _REV_B)

    with owner.reserve_plan((future,), failure_hints=(future,)):
        with owner.acquire(
            "future",
            model_config=configs["future"],
            resolved_revision=_REV_B,
        ) as acquired:
            future_path = acquired.path
        pressure = _manager(
            tmp_path,
            downloader,
            configs,
            revisions,
            quota=future_path.stat().st_size,
        )
        deleted: list[str] = []
        pressure._delete_revision = _deleter(
            deleted,
            {_REV_A: other_path, _REV_B: future_path},
        )

        assert pressure.reclaim(required_bytes=0) == (other_path,)
        assert future_path.is_file()
        assert deleted == [_REV_A]


def test_concurrent_reservations_refuse_before_next_download(tmp_path: Path) -> None:
    configs = {
        "one": _config("one-coder", "example/one"),
        "two": _config("two-coder", "example/two"),
        "next": _config("next-coder", "example/next"),
    }
    revisions = {
        "example/one": _REV_A,
        "example/two": _REV_B,
        "example/next": _REV_C,
    }
    downloader = _Downloader(tmp_path / "cache")
    first = _manager(tmp_path, downloader, configs, revisions)
    second = _manager(tmp_path, downloader, configs, revisions)
    one_path, _ = _seed(first, "one")
    two_path, _ = _seed(first, "two")
    pressure = _manager(
        tmp_path,
        downloader,
        configs,
        revisions,
        quota=one_path.stat().st_size + two_path.stat().st_size,
    )
    calls_before = tuple(downloader.calls)

    with (
        first.reserve_plan((_identity(configs["one"], _REV_A),)),
        second.reserve_plan((_identity(configs["two"], _REV_B),)),
        pytest.raises(RuntimeError, match="insufficient model cache headroom"),
        pressure.acquire(
            "next",
            model_config=configs["next"],
            resolved_revision=_REV_C,
        ),
    ):
        pytest.fail("protected-only pressure must refuse before download")

    assert tuple(downloader.calls) == calls_before
    assert one_path.is_file() and two_path.is_file()


def test_diagnose_reclaim_accounts_for_atomic_plan_protection(
    tmp_path: Path,
) -> None:
    config = _config("diagnostic-coder", "example/diagnostic")
    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(
        tmp_path,
        downloader,
        {"diagnostic": config},
        {config.repo: _REV_A},
    )
    path, _ = _seed(manager, "diagnostic")
    identity = _identity(config, _REV_A)

    relaxed = manager.diagnose_reclaim(required_bytes=0)
    assert not relaxed.under_pressure
    assert relaxed.can_reclaim
    assert relaxed.eviction_candidate_count == 1
    assert relaxed.owned_count == 1
    assert relaxed.leased_count == 0

    manager.quota_bytes = 1
    reclaimable = manager.diagnose_reclaim(required_bytes=0)
    assert reclaimable.under_pressure
    assert reclaimable.can_reclaim
    assert reclaimable.eviction_candidate_count == 1

    with manager.reserve_plan((identity,)):
        protected = manager.diagnose_reclaim(required_bytes=0)
        assert protected.under_pressure
        assert not protected.can_reclaim
        assert protected.eviction_candidate_count == 0

    assert path.is_file()


def test_corrupt_reservation_blocks_deletion(tmp_path: Path) -> None:
    config = _config("safe-coder", "example/safe")
    configs = {"safe": config}
    revisions = {config.repo: _REV_A}
    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(tmp_path, downloader, configs, revisions)
    path, _ = _seed(manager, "safe")
    manager.quota_bytes = 1
    reservation = manager.cache_root / ".gludd" / "reservations" / "bad.json"
    reservation.parent.mkdir(parents=True, exist_ok=True)
    reservation.write_text("{broken", encoding="utf-8")
    deleted: list[str] = []
    manager._delete_revision = _deleter(deleted, {_REV_A: path})

    with pytest.raises(RuntimeError, match="model plan reservation"):
        manager.reclaim(required_bytes=0)

    assert path.is_file() and deleted == []


@pytest.mark.parametrize(
    ("observed_birth", "is_live"),
    [(None, False), (_BIRTH + 1.0, False), (_BIRTH, True)],
)
def test_stale_reservation_requires_pid_and_birth_match(
    tmp_path: Path,
    observed_birth: float | None,
    is_live: bool,
) -> None:
    config = _config("reserved-coder", "example/reserved")
    configs = {"reserved": config}
    revisions = {config.repo: _REV_A}
    downloader = _Downloader(tmp_path / "cache")
    owner = _manager(tmp_path, downloader, configs, revisions)
    path, _ = _seed(owner, "reserved")
    pressure = _manager(
        tmp_path,
        downloader,
        configs,
        revisions,
        quota=1,
        process_started=lambda pid: _BIRTH if pid == os.getpid() else observed_birth,
    )
    pressure._delete_revision = _deleter([], {_REV_A: path})

    with owner.reserve_plan((_identity(config, _REV_A),)) as reservation:
        payload = json.loads(reservation.path.read_text(encoding="utf-8"))
        payload.update({"pid": 424242, "process_started": _BIRTH})
        reservation.path.write_text(json.dumps(payload), encoding="utf-8")
        if is_live:
            with pytest.raises(RuntimeError, match="insufficient model cache headroom"):
                pressure.reclaim(required_bytes=0)
            assert reservation.path.is_file() and path.is_file()
        else:
            assert pressure.reclaim(required_bytes=0) == (path,)
            assert not reservation.path.exists()


def test_unverifiable_owner_fails_before_deletion(tmp_path: Path) -> None:
    config = _config("reserved-coder", "example/reserved")
    configs = {"reserved": config}
    revisions = {config.repo: _REV_A}
    downloader = _Downloader(tmp_path / "cache")
    owner = _manager(tmp_path, downloader, configs, revisions)
    path, _ = _seed(owner, "reserved")

    def process_started(pid: int) -> float | None:
        if pid == os.getpid():
            return _BIRTH
        raise RuntimeError("cannot verify reservation owner")

    pressure = _manager(
        tmp_path,
        downloader,
        configs,
        revisions,
        quota=1,
        process_started=process_started,
    )
    pressure._delete_revision = _deleter([], {_REV_A: path})

    with owner.reserve_plan((_identity(config, _REV_A),)) as reservation:
        payload = json.loads(reservation.path.read_text(encoding="utf-8"))
        payload["pid"] = 424242
        reservation.path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(RuntimeError, match="cannot verify reservation owner"):
            pressure.reclaim(required_bytes=0)
        assert reservation.path.is_file()

    assert path.is_file()


@pytest.mark.parametrize("raised", [RuntimeError("failed"), KeyboardInterrupt()])
def test_reservation_cleanup_covers_failure_and_cancellation(
    tmp_path: Path,
    raised: BaseException,
) -> None:
    config = _config("planned-coder", "example/planned")
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        {"planned": config},
        {config.repo: _REV_A},
    )

    with (
        pytest.raises(type(raised)),
        manager.reserve_plan((_identity(config, _REV_A),)) as reservation,
    ):
        assert reservation.path.is_file()
        raise raised

    assert not reservation.path.exists()


def test_failure_ids_expand_only_to_exact_owned_identities(tmp_path: Path) -> None:
    configs = {
        "failed": _config("failed-coder", "example/failed"),
        "other": _config("other-coder", "example/other"),
    }
    revisions = {"example/failed": _REV_A, "example/other": _REV_B}
    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(tmp_path, downloader, configs, revisions)
    _seed(manager, "failed")
    _seed(manager, "other")

    assert manager.owned_identities_for_model_ids(("failed-coder", "missing")) == (
        _identity(configs["failed"], _REV_A),
    )


def test_reservation_rejects_unhashable_or_duplicate_identity_sets(
    tmp_path: Path,
) -> None:
    config = _config("planned-coder", "example/planned")
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        {"planned": config},
        {config.repo: _REV_A},
    )
    identity = _identity(config, _REV_A)

    for invalid_candidates in (
        cast(Sequence[ModelArtifactIdentity], ()),
        cast(Sequence[ModelArtifactIdentity], "invalid"),
    ):
        with (
            pytest.raises(ValueError, match="at least one"),
            manager.reserve_plan(invalid_candidates),
        ):
            pytest.fail("empty or string plans must fail before reservation")
    with (
        pytest.raises(ValueError, match="unique validated artifacts"),
        manager.reserve_plan(
            (identity,),
            failure_hints=cast(Sequence[ModelArtifactIdentity], ({},)),
        ),
    ):
        pytest.fail("unvalidated failure hints must fail before reservation")
    with (
        pytest.raises(ValueError, match="unique validated artifacts"),
        manager.reserve_plan(
            (identity,),
            failure_hints=(identity, identity),
        ),
    ):
        pytest.fail("duplicate failure hints must fail before reservation")
    with (
        pytest.raises(ValueError, match="unique validated artifacts"),
        manager.reserve_plan((identity, identity)),
    ):
        pytest.fail("duplicate identities must fail before reservation")
    with (
        pytest.raises(ValueError, match="unique validated artifacts"),
        manager.reserve_plan(cast(Sequence[ModelArtifactIdentity], ({},))),
    ):
        pytest.fail("unvalidated identities must fail before reservation")


def test_non_finite_observed_process_birth_fails_before_deletion(
    tmp_path: Path,
) -> None:
    config = _config("reserved-coder", "example/reserved")
    configs = {"reserved": config}
    revisions = {config.repo: _REV_A}
    downloader = _Downloader(tmp_path / "cache")
    owner = _manager(tmp_path, downloader, configs, revisions)
    path, _ = _seed(owner, "reserved")
    pressure = _manager(
        tmp_path,
        downloader,
        configs,
        revisions,
        quota=1,
        process_started=lambda pid: _BIRTH if pid == os.getpid() else float("nan"),
    )
    deleted: list[str] = []
    pressure._delete_revision = _deleter(deleted, {_REV_A: path})

    with owner.reserve_plan((_identity(config, _REV_A),)) as reservation:
        payload = json.loads(reservation.path.read_text(encoding="utf-8"))
        payload["pid"] = 424242
        reservation.path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(RuntimeError, match="process birth"):
            pressure.reclaim(required_bytes=0)

    assert path.is_file()
    assert deleted == []


def test_reservation_apis_reject_ambiguous_scalar_inputs(
    tmp_path: Path,
) -> None:
    config = _config("validation-coder", "example/validation")
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        {"validation": config},
        {config.repo: _REV_A},
    )

    with pytest.raises(ValueError, match="non-empty strings"):
        manager.owned_identities_for_model_ids("validation-coder")
    with pytest.raises(ValueError, match="non-negative integer"):
        manager.diagnose_reclaim(required_bytes=-1)


@pytest.mark.parametrize(
    "cleanup_error",
    [FileNotFoundError("raced cleanup"), OSError("cleanup unavailable")],
)
def test_stale_reservation_cleanup_race_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_error: OSError,
) -> None:
    config = _config("stale-coder", "example/stale")
    configs = {"stale": config}
    revisions = {config.repo: _REV_A}
    downloader = _Downloader(tmp_path / "cache")
    owner = _manager(tmp_path, downloader, configs, revisions)
    path, _ = _seed(owner, "stale")
    inspector = _manager(tmp_path, downloader, configs, revisions)
    real_unlink = Path.unlink

    with owner.reserve_plan((_identity(config, _REV_A),)) as reservation:
        payload = json.loads(reservation.path.read_text(encoding="utf-8"))
        payload["pid"] = 424242
        reservation.path.write_text(json.dumps(payload), encoding="utf-8")

        def fail_stale_unlink(target: Path, missing_ok: bool = False) -> None:
            if target == reservation.path:
                raise cleanup_error
            real_unlink(target, missing_ok=missing_ok)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", fail_stale_unlink)
            if isinstance(cleanup_error, FileNotFoundError):
                assert inspector.reclaim(required_bytes=0) == ()
            else:
                with pytest.raises(
                    RuntimeError,
                    match="stale model plan reservation cleanup failed",
                ):
                    inspector.reclaim(required_bytes=0)

        assert path.is_file()

    assert not reservation.path.exists()


def test_default_reclaim_uses_exact_hugging_face_cache_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config("exact-coder", "example/exact")
    configs = {"exact": config}
    revisions = {config.repo: _REV_A}
    downloader = _Downloader(tmp_path / "cache")
    manager = _manager(tmp_path, downloader, configs, revisions)
    path, _ = _seed(manager, "exact")
    manager.quota_bytes = 1
    identities: list[CacheArtifactIdentity] = []
    events: list[str] = []

    class ExactPlan:
        def dry_run(self) -> object:
            events.append("dry_run")
            return object()

        def execute_and_verify(self) -> object:
            events.append("execute_and_verify")
            path.unlink()
            return object()

    class ExactDeletion:
        def __init__(self, cache_root: Path) -> None:
            assert cache_root == manager.cache_root

        def plan(self, identity: CacheArtifactIdentity) -> ExactPlan:
            identities.append(identity)
            events.append("plan")
            return ExactPlan()

    monkeypatch.setattr(lifecycle, "HuggingFaceCacheDeletion", ExactDeletion)

    assert manager.reclaim(required_bytes=0) == (path,)
    assert identities == [
        CacheArtifactIdentity(
            repo_id=config.repo,
            revision=_REV_A,
            filename=config.filename,
            path=path,
        )
    ]
    assert events == ["plan", "dry_run", "execute_and_verify"]


@pytest.mark.parametrize(
    ("model_id", "repo_id", "filename", "revision", "message"),
    [
        ("", "example/repo", "model.gguf", _REV_A, "non-empty"),
        ("model", " example/repo", "model.gguf", _REV_A, "non-empty"),
        ("model", "example/repo", "model.gguf", "main", "immutable"),
        ("model", "example/repo", "/model.gguf", _REV_A, "inside"),
        ("model", "example/repo", "../model.gguf", _REV_A, "inside"),
    ],
)
def test_model_artifact_identity_rejects_ambiguous_coordinates(
    model_id: str,
    repo_id: str,
    filename: str,
    revision: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ModelArtifactIdentity(model_id, repo_id, filename, revision)

    normalized = ModelArtifactIdentity(
        "model",
        "example/repo",
        "nested/model.gguf",
        _REV_A.upper(),
    )
    assert normalized.revision == _REV_A


def test_reservation_transitions_are_monotonic_and_require_planned_identity(
    tmp_path: Path,
) -> None:
    config = _config("state-coder", "example/state")
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        {"state": config},
        {config.repo: _REV_A},
    )
    identity = _identity(config, _REV_A)
    unrelated = ModelArtifactIdentity(
        "other",
        "example/other",
        "other.gguf",
        _REV_B,
    )

    with manager.reserve_plan((identity,)) as reservation:
        reservation.mark_eligible(identity)
        reservation.mark_eligible(identity)
        reservation.mark_failed(identity)
        reservation.mark_failed(identity)
        with pytest.raises(RuntimeError, match="cannot become eligible"):
            reservation.mark_eligible(identity)
        with pytest.raises(ValueError, match="planned identity"):
            reservation.mark_failed(unrelated)


def test_missing_reservation_update_fails_closed_and_cleanup_is_idempotent(
    tmp_path: Path,
) -> None:
    config = _config("missing-coder", "example/missing")
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        {"missing": config},
        {config.repo: _REV_A},
    )
    identity = _identity(config, _REV_A)

    with manager.reserve_plan((identity,)) as reservation:
        reservation.path.unlink()
        with pytest.raises(RuntimeError, match="disappeared before update"):
            reservation.mark_failed(identity)


def test_failed_transition_persistence_rolls_back_in_memory_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config("rollback-coder", "example/rollback")
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        {"rollback": config},
        {config.repo: _REV_A},
    )
    identity = _identity(config, _REV_A)

    with manager.reserve_plan((identity,)) as reservation:

        def fail_write(
            _reservation: object,
            *,
            require_existing: bool,
        ) -> None:
            assert require_existing
            raise OSError("injected atomic update failure")

        with monkeypatch.context() as patch:
            patch.setattr(manager, "_write_plan_reservation", fail_write)
            with pytest.raises(OSError, match="atomic update failure"):
                reservation.mark_failed(identity)

        reservation.mark_eligible(identity)
        payload = json.loads(reservation.path.read_text(encoding="utf-8"))
        assert payload["candidates"][0]["state"] == "eligible"


@pytest.mark.parametrize("has_primary_error", [False, True])
def test_reservation_cleanup_failure_is_reported_without_masking_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_primary_error: bool,
) -> None:
    config = _config("cleanup-coder", "example/cleanup")
    manager = _manager(
        tmp_path,
        _Downloader(tmp_path / "cache"),
        {"cleanup": config},
        {config.repo: _REV_A},
    )
    identity = _identity(config, _REV_A)
    reservation_path: Path | None = None
    real_unlink = Path.unlink

    def fail_reservation_unlink(target: Path, missing_ok: bool = False) -> None:
        if target == reservation_path:
            raise OSError("injected reservation unlink failure")
        real_unlink(target, missing_ok=missing_ok)

    if has_primary_error:
        with (
            pytest.raises(ValueError, match="primary failure") as raised,
            monkeypatch.context() as patch,
            manager.reserve_plan((identity,)) as reservation,
        ):
            reservation_path = reservation.path
            patch.setattr(Path, "unlink", fail_reservation_unlink)
            raise ValueError("primary failure")
        assert any("reservation cleanup failed" in note for note in raised.value.__notes__)
    else:
        with (
            pytest.raises(RuntimeError, match="reservation cleanup failed"),
            monkeypatch.context() as patch,
            manager.reserve_plan((identity,)) as reservation,
        ):
            reservation_path = reservation.path
            patch.setattr(Path, "unlink", fail_reservation_unlink)

    assert reservation_path is not None
    real_unlink(reservation_path)


@pytest.mark.parametrize(
    "corruption",
    [
        "extra-key",
        "schema",
        "pid-bool",
        "pid-type",
        "pid-zero",
        "started-bool",
        "started-type",
        "started-nonfinite",
        "started-zero",
        "created-bool",
        "created-type",
        "created-zero",
        "candidates-shape",
        "empty-candidates",
        "failure-shape",
        "candidate-shape",
        "identity-shape",
        "identity-value",
        "state-type",
        "state-value",
        "candidate-duplicate",
        "failure-duplicate",
    ],
)
def test_structurally_corrupt_reservation_blocks_every_deletion(
    tmp_path: Path,
    corruption: str,
) -> None:
    config = _config("corrupt-coder", "example/corrupt")
    configs = {"corrupt": config}
    revisions = {config.repo: _REV_A}
    downloader = _Downloader(tmp_path / "cache")
    owner = _manager(tmp_path, downloader, configs, revisions)
    path, _ = _seed(owner, "corrupt")
    pressure = _manager(tmp_path, downloader, configs, revisions, quota=1)
    deleted: list[str] = []
    pressure._delete_revision = _deleter(deleted, {_REV_A: path})

    with owner.reserve_plan((_identity(config, _REV_A),)) as reservation:
        payload = json.loads(reservation.path.read_text(encoding="utf-8"))
        candidates = cast(list[dict[str, object]], payload["candidates"])
        candidate = candidates[0]
        identity_payload = cast(dict[str, object], candidate["identity"])
        if corruption == "extra-key":
            payload["unexpected"] = True
        elif corruption == "schema":
            payload["schema_version"] = 2
        elif corruption == "pid-bool":
            payload["pid"] = True
        elif corruption == "pid-type":
            payload["pid"] = "1"
        elif corruption == "pid-zero":
            payload["pid"] = 0
        elif corruption == "started-bool":
            payload["process_started"] = True
        elif corruption == "started-type":
            payload["process_started"] = "123.25"
        elif corruption == "started-nonfinite":
            payload["process_started"] = float("nan")
        elif corruption == "started-zero":
            payload["process_started"] = 0
        elif corruption == "created-bool":
            payload["created_ns"] = True
        elif corruption == "created-type":
            payload["created_ns"] = "1"
        elif corruption == "created-zero":
            payload["created_ns"] = 0
        elif corruption == "candidates-shape":
            payload["candidates"] = "invalid"
        elif corruption == "empty-candidates":
            payload["candidates"] = []
        elif corruption == "failure-shape":
            payload["failure_hints"] = "invalid"
        elif corruption == "candidate-shape":
            payload["candidates"] = [{"identity": identity_payload}]
        elif corruption == "identity-shape":
            candidate["identity"] = {"model_id": config.name}
        elif corruption == "identity-value":
            identity_payload["revision"] = "main"
        elif corruption == "state-type":
            candidate["state"] = 0
        elif corruption == "state-value":
            candidate["state"] = "unknown"
        elif corruption == "candidate-duplicate":
            payload["candidates"] = [candidate, dict(candidate)]
        else:
            payload["failure_hints"] = [
                dict(identity_payload),
                dict(identity_payload),
            ]
        reservation.path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(RuntimeError, match="model plan reservation"):
            pressure.reclaim(required_bytes=0)

    assert path.is_file()
    assert deleted == []
