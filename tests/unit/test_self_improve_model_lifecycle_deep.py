"""Deep fail-closed contracts for self-improvement model ownership."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Literal

import pytest

import general_ludd.self_improve.model_lifecycle as lifecycle
from general_ludd.local_model._local_model_configs import LocalModelConfig
from general_ludd.self_improve.model_lifecycle import ModelLeaseManager
from general_ludd.small_models.download import DownloadedModel, DownloadSource, ModelDownloader

_REVISION = "a" * 40
_OTHER_REVISION = "b" * 40
_PROCESS_STARTED = 123.25


def _config(
    *,
    name: str = "deep-coder",
    repo: str = "example/deep-coder-GGUF",
    category: Literal["coding", "general"] = "coding",
) -> LocalModelConfig:
    return LocalModelConfig(
        name=name,
        repo=repo,
        filename=f"{name}.Q4_K_M.gguf",
        size_mb=1,
        category=category,
        ci_safe=True,
    )


class _Downloader:
    def __init__(
        self,
        cache_root: Path,
        *,
        returned_path: Path | None = None,
        returned_revision: str = _REVISION,
        create: bool = True,
    ) -> None:
        self.cache_root = cache_root
        self.returned_path = returned_path
        self.returned_revision = returned_revision
        self.create = create
        self.calls = 0

    def download_gguf(
        self,
        model_id: str,
        filename: str,
        revision: str | None = None,
        *,
        local_files_only: bool = False,
    ) -> DownloadedModel:
        del local_files_only
        self.calls += 1
        path = self.returned_path or (
            self.cache_root
            / f"models--{model_id.replace('/', '--')}"
            / "snapshots"
            / str(revision)
            / filename
        )
        if self.create:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"deep immutable gguf")
        return DownloadedModel(
            model_id=model_id,
            local_path=str(path),
            source=DownloadSource.GGUF,
            filename=filename,
            revision=self.returned_revision,
            size_bytes=path.stat().st_size if path.exists() else 0,
        )


def _manager(
    tmp_path: Path,
    *,
    downloader: _Downloader | None = None,
    selector: Callable[[str], LocalModelConfig] | None = None,
    revision_resolver: Callable[[str], str] | None = None,
    quota_bytes: int = 64 * 1024 * 1024,
    reserve_bytes: int = 0,
    disk_free: Callable[[Path], int] | None = None,
    revision_deleter: Callable[[Path, str], None] | None = None,
    process_started: Callable[[int], float | None] | None = None,
) -> ModelLeaseManager:
    cache_root = tmp_path / "cache"
    owned_downloader = downloader or _Downloader(cache_root)
    return ModelLeaseManager(
        cache_root=cache_root,
        quota_bytes=quota_bytes,
        reserve_bytes=reserve_bytes,
        model_selector=selector or (lambda _task: _config()),
        revision_resolver=revision_resolver or (lambda _repo: _REVISION),
        downloader_factory=lambda _cache: owned_downloader,
        disk_free=disk_free or (lambda _cache: 1 << 50),
        revision_deleter=revision_deleter,
        process_started=process_started
        or (lambda pid: _PROCESS_STARTED if pid == os.getpid() else None),
    )


def test_default_selector_uses_override_priority_and_bounded_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coding = _config(name="custom-coder", repo="example/custom")
    monkeypatch.setattr(lifecycle, "_LOCAL_MODELS", (coding,))
    monkeypatch.setenv("GLUDD_SELF_IMPROVE_MODEL", coding.repo)
    assert lifecycle._default_selector("repair code") == coding

    monkeypatch.setenv("GLUDD_SELF_IMPROVE_MODEL", "missing")
    with pytest.raises(RuntimeError, match="exactly one coding model"):
        lifecycle._default_selector("repair code")

    monkeypatch.delenv("GLUDD_SELF_IMPROVE_MODEL")
    assert lifecycle._default_selector("repair code") == coding

    monkeypatch.setattr(lifecycle, "_LOCAL_MODELS", ())
    with pytest.raises(RuntimeError, match="no coding model"):
        lifecycle._default_selector("repair code")


def test_default_cache_and_environment_limits_are_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_root = tmp_path / "env-cache"
    monkeypatch.setenv("GLUDD_SELF_IMPROVE_MODEL_CACHE", str(cache_root))
    monkeypatch.setenv("GLUDD_SELF_IMPROVE_MODEL_QUOTA_BYTES", "4096")
    monkeypatch.setenv("GLUDD_SELF_IMPROVE_MODEL_RESERVE_BYTES", "128")

    assert lifecycle._default_cache_root() == cache_root
    manager = ModelLeaseManager(
        model_selector=lambda _task: _config(),
        revision_resolver=lambda _repo: _REVISION,
        downloader_factory=lambda root: _Downloader(root),
        disk_free=lambda _root: 1 << 20,
        process_started=lambda _pid: _PROCESS_STARTED,
    )
    assert manager.cache_root == cache_root
    assert manager.quota_bytes == 4096
    assert manager.reserve_bytes == 128


@pytest.mark.parametrize(("quota", "reserve"), [(0, 0), (1, -1), (True, 0)])
def test_constructor_rejects_invalid_resource_limits(
    tmp_path: Path,
    quota: int,
    reserve: int,
) -> None:
    with pytest.raises(ValueError, match="quota"):
        _manager(tmp_path, quota_bytes=quota, reserve_bytes=reserve)


def test_constructor_rejects_symlinked_cache_and_unverifiable_owner(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError, match="must not be a symlink"):
        ModelLeaseManager(cache_root=linked)

    with pytest.raises(RuntimeError, match="cannot identify current"):
        _manager(tmp_path / "owner", process_started=lambda _pid: None)


def test_acquisition_rejects_empty_task_non_coding_selector_and_mutable_revision(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(ValueError, match="must not be empty"), manager.acquire("   "):
        pytest.fail("empty task must not acquire")

    general = _config(category="general")
    wrong = _manager(tmp_path / "wrong", selector=lambda _task: general)
    with pytest.raises(RuntimeError, match="coding model"), wrong.acquire("coding"):
        pytest.fail("non-coding selection must not acquire")

    mutable = _manager(
        tmp_path / "mutable",
        revision_resolver=lambda _repo: "main",
    )
    with pytest.raises(RuntimeError, match="40-character commit"), mutable.acquire("coding"):
        pytest.fail("mutable revision must not acquire")


def test_explicit_path_rejects_missing_and_empty_artifacts(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with (
        pytest.raises(FileNotFoundError, match="not readable"),
        manager.acquire("coding", explicit_path=tmp_path / "missing.gguf"),
    ):
        pytest.fail("missing override must not acquire")

    empty = tmp_path / "empty.gguf"
    empty.touch()
    with (
        pytest.raises(RuntimeError, match="non-empty regular file"),
        manager.acquire("coding", explicit_path=empty),
    ):
        pytest.fail("empty override must not acquire")


@pytest.mark.parametrize(
    ("outside", "returned_revision", "create", "message"),
    [
        (True, _REVISION, True, "escaped"),
        (False, _OTHER_REVISION, True, "immutable artifact identity"),
        (False, _REVISION, False, "immutable artifact identity"),
    ],
)
def test_download_result_must_be_confined_present_and_revision_bound(
    tmp_path: Path,
    outside: bool,
    returned_revision: str,
    create: bool,
    message: str,
) -> None:
    cache_root = tmp_path / "cache"
    path = (tmp_path / "outside.gguf") if outside else (cache_root / "missing.gguf")
    downloader = _Downloader(
        cache_root,
        returned_path=path,
        returned_revision=returned_revision,
        create=create,
    )
    manager = _manager(tmp_path, downloader=downloader)
    with pytest.raises(RuntimeError, match=message), manager.acquire("coding"):
        pytest.fail("invalid download must not acquire")
    assert list((cache_root / ".gludd" / "acquiring").glob("*.lock")) == []


@pytest.mark.parametrize("mutation", ["missing", "size", "digest"])
def test_owned_cache_reuse_fails_closed_on_artifact_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    manager = _manager(tmp_path)
    with manager.acquire("coding") as model:
        path = model.path

    if mutation == "missing":
        path.unlink()
    elif mutation == "size":
        path.write_bytes(b"x")
    else:
        path.write_bytes(b"deep mutable gguf")

    with pytest.raises(RuntimeError, match=r"missing or changed|digest changed"), manager.acquire("coding"):
        pytest.fail("drifted artifact must not be reused")


def test_stale_and_reused_pid_leases_are_removed_without_deleting_active(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    with manager.acquire("coding") as model:
        payload = json.loads(model.lease_path.read_text(encoding="utf-8"))
        payload["pid"] = os.getpid() + 100_000
        model.lease_path.write_text(json.dumps(payload), encoding="utf-8")
        assert manager.reclaim(required_bytes=0) == ()
        assert not model.lease_path.exists()

    with manager.acquire("coding") as reused:
        payload = json.loads(reused.lease_path.read_text(encoding="utf-8"))
        payload["process_started"] = _PROCESS_STARTED + 1
        reused.lease_path.write_text(json.dumps(payload), encoding="utf-8")
        assert manager.reclaim(required_bytes=0) == ()
        assert not reused.lease_path.exists()


def test_invalid_lease_and_manifest_variants_block_reclamation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    bad_lease = manager.cache_root / ".gludd" / "leases" / "bad.json"
    bad_lease.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="model lease is invalid"):
        manager.reclaim(required_bytes=0)
    bad_lease.unlink()

    artifact = manager.cache_root / "artifact.gguf"
    artifact.write_bytes(b"owned")
    base: dict[str, object] = {
        "schema_version": 1,
        "model_id": "deep-coder",
        "repo_id": "example/deep",
        "filename": "deep.gguf",
        "revision": _REVISION,
        "artifact_sha256": "c" * 64,
        "path": str(artifact),
        "size_bytes": artifact.stat().st_size,
        "last_used_ns": 1,
    }
    manifest = manager.cache_root / ".gludd" / "models" / "bad.json"
    variants = [
        {**base, "schema_version": 2},
        {**base, "model_id": ""},
        {**base, "revision": "main"},
        {**base, "path": str(tmp_path / "escaped.gguf")},
    ]
    for value in variants:
        manifest.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(RuntimeError, match="manifest"):
            manager.reclaim(required_bytes=0)
    manifest.unlink()


def test_pressure_fails_closed_on_disk_query_failed_eviction_and_lock_collision(
    tmp_path: Path,
) -> None:
    def failed_disk_query(_root: Path) -> int:
        raise OSError("disk unavailable")

    manager = _manager(tmp_path, disk_free=failed_disk_query)
    with pytest.raises(RuntimeError, match="disk headroom"):
        manager.reclaim(required_bytes=0)

    healthy = _manager(tmp_path / "evict")
    with healthy.acquire("coding") as model:
        artifact = model.path
    pressure = _manager(
        tmp_path / "evict",
        quota_bytes=1,
        revision_deleter=lambda _root, _revision: None,
    )
    with pytest.raises(RuntimeError, match="did not remove owned artifact"):
        pressure.reclaim(required_bytes=0)
    assert artifact.exists()

    locked = _manager(tmp_path / "locked")
    lock_path = locked._acquisition_lock_path(_config().repo, _REVISION)
    lock_path.write_text("owned", encoding="utf-8")
    with pytest.raises(RuntimeError, match="already owned"), locked.acquire("coding"):
        pytest.fail("parallel acquisition must fail closed")


def test_atomic_json_and_lease_cleanup_report_owner_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        lifecycle._atomic_json(target, {"ok": True})
    assert list(tmp_path.glob(".*.tmp")) == []

    directory = tmp_path / "lease-directory"
    directory.mkdir()
    with pytest.raises(RuntimeError, match="lease cleanup failed"):
        lifecycle.ModelLeaseManager._release_lease(directory)


def test_primary_failure_survives_lease_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)

    def fail_release(_lease_path: Path) -> None:
        raise RuntimeError("lease unlink failed")

    monkeypatch.setattr(
        ModelLeaseManager,
        "_release_lease",
        staticmethod(fail_release),
    )
    with pytest.raises(ValueError, match="proposal failed") as raised, manager.acquire("coding"):
        raise ValueError("proposal failed")

    assert any("lease unlink failed" in note for note in raised.value.__notes__)


def test_hugging_face_helpers_pin_revision_and_execute_exact_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[object] = []
    fake_hub = ModuleType("huggingface_hub")

    class FakeApi:
        def __init__(self, *, token: str | None) -> None:
            observed.append(("token", token))

        def model_info(self, *, repo_id: str, revision: str) -> SimpleNamespace:
            observed.append((repo_id, revision))
            return SimpleNamespace(sha=_REVISION.upper())

    class FakeStrategy:
        def execute(self) -> None:
            observed.append("execute")

    class FakeCache:
        def delete_revisions(self, revision: str) -> FakeStrategy:
            observed.append(("delete", revision))
            return FakeStrategy()

    def scan_cache_dir(*, cache_dir: Path) -> FakeCache:
        observed.append(("cache", cache_dir))
        return FakeCache()

    fake_hub.__dict__["HfApi"] = FakeApi
    fake_hub.__dict__["scan_cache_dir"] = scan_cache_dir
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setenv("HF_TOKEN", "secret")

    assert lifecycle._default_revision_resolver("example/repo") == _REVISION
    assert isinstance(lifecycle._default_downloader(tmp_path), ModelDownloader)
    lifecycle._delete_hf_revision(tmp_path, _REVISION)
    assert ("example/repo", "main") in observed
    assert ("delete", _REVISION) in observed
    assert "execute" in observed

    class BadApi(FakeApi):
        def model_info(self, *, repo_id: str, revision: str) -> SimpleNamespace:
            del repo_id, revision
            return SimpleNamespace(sha=None)

    fake_hub.__dict__["HfApi"] = BadApi
    with pytest.raises(RuntimeError, match="immutable revision"):
        lifecycle._default_revision_resolver("example/repo")


def test_process_identity_helper_handles_gone_and_unverifiable_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_psutil = ModuleType("psutil")

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    class MissingProcess:
        def __init__(self, _pid: int) -> None:
            raise NoSuchProcess

    fake_psutil.__dict__.update(
        {
            "NoSuchProcess": NoSuchProcess,
            "AccessDenied": AccessDenied,
            "Process": MissingProcess,
        }
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    assert lifecycle._default_process_started(123) is None

    class DeniedProcess:
        def __init__(self, _pid: int) -> None:
            raise AccessDenied

    fake_psutil.__dict__["Process"] = DeniedProcess
    with pytest.raises(RuntimeError, match="cannot verify model lease owner"):
        lifecycle._default_process_started(123)


def test_planned_candidate_identity_bypasses_selector_and_revision_network(
    tmp_path: Path,
) -> None:
    config = _config(name="planned-coder", repo="example/planned-coder")
    cache_root = tmp_path / "cache"
    downloader = _Downloader(cache_root, returned_revision=_OTHER_REVISION)
    manager = _manager(
        tmp_path,
        downloader=downloader,
        selector=lambda _task: pytest.fail("planned candidate must bypass selector"),
        revision_resolver=lambda _repo: pytest.fail(
            "planned immutable revision must bypass resolver"
        ),
    )

    with manager.acquire(
        "implement code",
        model_config=config,
        resolved_revision=_OTHER_REVISION.upper(),
    ) as acquired:
        assert acquired.model_id == config.name
        assert acquired.resolved_revision == _OTHER_REVISION
        assert acquired.repo_id == config.repo

    assert downloader.calls == 1


@pytest.mark.parametrize(
    ("model_config", "revision", "explicit", "message"),
    [
        (_config(), None, None, "paired"),
        (None, _REVISION, None, "paired"),
        (_config(), _REVISION, Path("/tmp/operator.gguf"), "cannot combine"),
    ],
)
def test_planned_candidate_identity_is_complete_and_unambiguous(
    tmp_path: Path,
    model_config: LocalModelConfig | None,
    revision: str | None,
    explicit: Path | None,
    message: str,
) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(ValueError, match=message), manager.acquire(
        "implement code",
        explicit_path=explicit,
        model_config=model_config,
        resolved_revision=revision,
    ):
        pytest.fail("invalid acquisition identity must not yield")
