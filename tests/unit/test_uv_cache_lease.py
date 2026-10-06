"""Cross-worktree lease coverage for the shared uv cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_shared_uv_cache_lease_publishes_and_releases_exact_owner(
    tmp_path: Path,
) -> None:
    from scripts import uv_cache_lease

    cache = tmp_path / "cache"
    owner = tmp_path / "worktree-a"
    owner.mkdir()

    with uv_cache_lease.shared_uv_cache_lease(cache, owner_root=owner) as lease:
        assert lease.acquired is True
        assert lease.receipt_path is not None
        payload = json.loads(lease.receipt_path.read_text(encoding="utf-8"))
        assert payload["mode"] == "shared"
        assert payload["owner_root"] == str(owner.resolve())
        assert payload["cache_root"] == str(cache.resolve())
        receipt = lease.receipt_path

    assert not receipt.exists()


def test_exclusive_uv_cache_lease_reports_the_foreign_owner_when_busy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import uv_cache_lease

    cache = tmp_path / "cache"
    owner = tmp_path / "worktree-a"
    owner.mkdir()
    paths = uv_cache_lease.uv_cache_lease_paths(cache)
    paths.owners_dir.mkdir(parents=True)
    (paths.owners_dir / "foreign.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "shared",
                "token": "foreign-token",
                "pid": 8123,
                "pid_start_time": "foreign-start",
                "owner_root": str((tmp_path / "worktree-b").resolve()),
                "owner_namespace": "worktree-b-deadbeef0000",
                "cache_root": str(cache.resolve()),
                "acquired_at": "2026-10-06T17:23:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (paths.owners_dir / "malformed.json").write_text("not-json", encoding="utf-8")
    (paths.owners_dir / "other-cache.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "token": "other-token",
                "owner_root": str(owner),
                "owner_namespace": "other",
                "cache_root": str((tmp_path / "other-cache").resolve()),
            }
        ),
        encoding="utf-8",
    )

    def busy(_fd: int, _operation: int) -> None:
        raise BlockingIOError

    monkeypatch.setattr(uv_cache_lease.fcntl, "flock", busy)
    with uv_cache_lease.exclusive_uv_cache_lease(
        cache,
        owner_root=owner,
        wait_seconds=0.0,
    ) as lease:
        assert lease.acquired is False
        assert [entry["owner_root"] for entry in lease.owners] == [
            str((tmp_path / "worktree-b").resolve())
        ]


def test_exclusive_uv_cache_lease_clears_stale_receipts_only_after_lock(
    tmp_path: Path,
) -> None:
    from scripts import uv_cache_lease

    cache = tmp_path / "cache"
    owner = tmp_path / "worktree-a"
    owner.mkdir()
    paths = uv_cache_lease.uv_cache_lease_paths(cache)
    paths.owners_dir.mkdir(parents=True)
    stale = paths.owners_dir / "stale.json"
    stale.write_text("{}", encoding="utf-8")

    with uv_cache_lease.exclusive_uv_cache_lease(
        cache,
        owner_root=owner,
    ) as lease:
        assert lease.acquired is True
        assert lease.mode == "exclusive"
        assert not stale.exists()
        assert lease.receipt_path is not None
        assert lease.receipt_path.exists()

    assert not lease.receipt_path.exists()


def test_uv_cache_lease_rejects_invalid_owner_and_wait_contract(
    tmp_path: Path,
) -> None:
    from scripts import uv_cache_lease

    cache = tmp_path / "cache"
    with (
        pytest.raises(ValueError, match="owner root"),
        uv_cache_lease.shared_uv_cache_lease(
            cache,
            owner_root=Path("relative-owner"),
        ),
    ):
        pass
    with (
        pytest.raises(ValueError, match="lease wait"),
        uv_cache_lease.shared_uv_cache_lease(
            cache,
            owner_root=tmp_path,
            wait_seconds=-1.0,
        ),
    ):
        pass
    with (
        pytest.raises(ValueError, match="lease mode"),
        uv_cache_lease._uv_cache_lease(
            cache,
            owner_root=tmp_path,
            mode="invalid",
            wait_seconds=0.0,
            heartbeat_seconds=1.0,
        ),
    ):
        pass


def test_owned_receipt_removal_is_token_safe_and_malformed_safe(
    tmp_path: Path,
) -> None:
    from scripts import uv_cache_lease

    receipt = tmp_path / "owner.json"
    receipt.write_text("not-json", encoding="utf-8")
    uv_cache_lease._remove_owned_receipt(receipt, "expected")
    assert receipt.exists()

    receipt.write_text(json.dumps({"token": "other"}), encoding="utf-8")
    uv_cache_lease._remove_owned_receipt(receipt, "expected")
    assert receipt.exists()


@pytest.mark.parametrize("cache", [Path("relative"), Path("/")])
def test_uv_cache_lease_rejects_ambiguous_cache_roots(
    cache: Path,
    tmp_path: Path,
) -> None:
    from scripts import uv_cache_lease

    with (
        pytest.raises(ValueError, match="cache root"),
        uv_cache_lease.shared_uv_cache_lease(cache, owner_root=tmp_path),
    ):
        pass
