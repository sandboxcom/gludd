"""Tests split from :mod:`tests.unit.test_automatic_disk_cleanup` by coherent behavior."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import automatic_disk_cleanup
from scripts.automatic_disk_cleanup import DiskSnapshot
from scripts.makefile_layout import compose_makefile
from scripts.prune_worktrees_safe import WorktreeRecord

from tests.unit.test_automatic_disk_cleanup import (
    ROOT,
    _record,
)


def _owned_terraform_workspace(root: Path, name: str = "a" * 24) -> Path:
    workspace = root / name
    providers = workspace / ".terraform" / "providers"
    providers.mkdir(parents=True)
    (providers / "provider.bin").write_bytes(b"regenerable")
    marker = workspace / ".gludd-azure-containerapp-live-proof.json"
    marker.write_text(
        json.dumps(
            {
                "operation_digest": name + ("b" * (64 - len(name))),
                "protocol": "gludd-azure-containerapp-live-proof-v1",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    marker.chmod(0o600)
    (workspace / "terraform.tfstate").write_text("{}\n", encoding="utf-8")
    return workspace


def test_owned_terraform_provider_cleanup_preserves_state_and_proof(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-azure-containerapp-live-proof"
    workspace = _owned_terraform_workspace(root)
    providers = workspace / ".terraform" / "providers"
    marker = workspace / ".gludd-azure-containerapp-live-proof.json"
    process_checks: list[Path] = []

    result = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,),
        active_process_pids=lambda path: process_checks.append(path) or [],
    )

    assert result == automatic_disk_cleanup.CleanupResult(
        removed=(str(providers),), skipped=(), errors=()
    )
    assert process_checks == [workspace.resolve(), workspace.resolve()]
    assert not providers.exists()
    assert marker.is_file()
    assert (workspace / "terraform.tfstate").read_text(encoding="utf-8") == "{}\n"


def test_owned_terraform_provider_cleanup_preserves_active_and_dry_run_workspaces(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-azure-containerapp-live-proof"
    workspace = _owned_terraform_workspace(root)
    providers = workspace / ".terraform" / "providers"

    active = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,), active_process_pids=lambda _path: [8123]
    )
    dry_run = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,), active_process_pids=lambda _path: [], dry_run=True
    )

    assert active.skipped == (f"{workspace}:active-pids=8123",)
    assert dry_run.skipped == (f"{providers}:would remove terraform provider cache",)
    assert providers.is_dir()


def test_owned_terraform_provider_cleanup_rejects_invalid_marker_and_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-azure-containerapp-live-proof"
    invalid = _owned_terraform_workspace(root, "c" * 24)
    marker = invalid / ".gludd-azure-containerapp-live-proof.json"
    marker.write_text('{"protocol":"wrong"}\n', encoding="utf-8")
    marker.chmod(0o600)
    linked = _owned_terraform_workspace(root, "d" * 24)
    providers = linked / ".terraform" / "providers"
    automatic_disk_cleanup._remove_tree(providers)
    external = tmp_path / "external-providers"
    external.mkdir()
    (external / "keep.bin").write_bytes(b"keep")
    providers.symlink_to(external, target_is_directory=True)

    result = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,), active_process_pids=lambda _path: []
    )

    assert result.errors == tuple(
        sorted(
            (
                f"{invalid}:terraform-ownership-invalid",
                f"{providers}:unsafe-terraform-provider-cache",
            )
        )
    )
    assert (invalid / ".terraform" / "providers").is_dir()
    assert (external / "keep.bin").read_bytes() == b"keep"


def test_owned_terraform_provider_cleanup_treats_absent_root_as_converged(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gludd-azure-containerapp-live-proof"

    result = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(root,), active_process_pids=lambda _path: []
    )

    assert result == automatic_disk_cleanup.CleanupResult(
        removed=(), skipped=(f"{root}:terraform-root-absent",), errors=()
    )


def test_owned_terraform_provider_cleanup_bounds_and_late_failures(
    tmp_path: Path,
) -> None:
    invalid_limit = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(), max_workspaces=0
    )
    assert invalid_limit.errors == ("terraform-provider-cache:invalid-limit",)

    absent_root = tmp_path / "absent-provider"
    absent_workspace = _owned_terraform_workspace(absent_root, "e" * 24)
    absent_providers = absent_workspace / ".terraform" / "providers"
    automatic_disk_cleanup._remove_tree(absent_providers)
    absent = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(absent_root,), active_process_pids=lambda _path: []
    )
    assert absent.skipped == (
        f"{absent_providers}:terraform-provider-cache-absent",
    )

    raced_root = tmp_path / "late-process"
    raced_workspace = _owned_terraform_workspace(raced_root, "f" * 24)
    process_reads = iter(([], [7331]))
    raced = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(raced_root,), active_process_pids=lambda _path: next(process_reads)
    )
    assert raced.skipped == (f"{raced_workspace}:active-pids=7331",)
    assert (raced_workspace / ".terraform" / "providers").is_dir()

    failed_root = tmp_path / "failed-removal"
    failed_workspace = _owned_terraform_workspace(failed_root, "1" * 24)
    failed_providers = failed_workspace / ".terraform" / "providers"

    def refuse_removal(_path: Path) -> None:
        raise PermissionError("refused")

    failed = automatic_disk_cleanup.clean_owned_terraform_provider_caches(
        roots=(failed_root,),
        active_process_pids=lambda _path: [],
        remove_tree=refuse_removal,
    )
    assert failed.errors == (f"{failed_providers}:removal-failed",)
    assert failed_providers.is_dir()


def test_shared_uv_cache_prunes_only_after_two_idle_checks(tmp_path: Path) -> None:
    cache = tmp_path / "gludd-uv-cache-public-v2"
    cache.mkdir()
    checks = iter(([], [], []))
    pruned: list[Path] = []
    cleaned: list[Path] = []

    result = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: next(checks),
        run_prune=lambda path: pruned.append(path) is None,
        run_clean=lambda path: cleaned.append(path) is None,
        dry_run=False,
    )

    assert pruned == [cache]
    assert cleaned == [cache]
    assert result.removed == (str(cache),)


def test_shared_uv_cache_preserves_active_or_ambiguous_owners(tmp_path: Path) -> None:
    cache = tmp_path / "gludd-uv-cache-public-v2"
    cache.mkdir()
    called = False

    def unexpected_prune(_path: Path) -> bool:
        nonlocal called
        called = True
        return True

    active = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: [321],
        run_prune=unexpected_prune,
        dry_run=False,
    )
    reads = iter(([], [654]))
    raced = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: next(reads),
        run_prune=unexpected_prune,
        dry_run=False,
    )

    assert called is False
    assert active.skipped == (f"{cache}:active-uv-pids=321",)
    assert raced.skipped == (f"{cache}:active-uv-pids=654",)

    final_reads = iter(([], [], [987]))
    pruned: list[Path] = []
    cleaned: list[Path] = []
    final_race = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: next(final_reads),
        run_prune=lambda path: pruned.append(path) is None,
        run_clean=lambda path: cleaned.append(path) is None,
    )
    assert pruned == [cache]
    assert cleaned == []
    assert final_race.skipped == (f"{cache}:active-uv-pids=987",)


def test_unsafe_cache_file_and_removal_failure_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "gludd-worktrees"
    record = _record(root / "finished", "feature/finished")
    unsafe = record.path / ".venv"
    unsafe.write_text("not a directory\n", encoding="utf-8")
    failed_removal = record.path / ".pytest_cache"
    failed_removal.mkdir()

    def refuse_removal(_path: Path) -> None:
        raise PermissionError("denied")

    result = automatic_disk_cleanup.clean_inactive_worktree_caches(
        records=[record],
        approved_roots=(root,),
        protected_paths=frozenset(),
        active_branches=lambda: frozenset(),
        refresh_records=lambda: [record],
        active_process_pids=lambda _path: [],
        remove_tree=refuse_removal,
    )

    assert result.errors == tuple(
        sorted(
            (
                f"{failed_removal}:removal-failed",
                f"{unsafe}:unsafe-cache",
            )
        )
    )


def test_stale_generated_scratch_cleanup_maps_refusals_and_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[bool] = []

    def cleanup_runner(*, dry_run: bool) -> dict[str, list[str]]:
        calls.append(dry_run)
        return {
            "removed": ["/tmp/gludd-test-old.json"],
            "skipped": [
                "/tmp/gludd-test-current.json:recent",
                "/tmp/gludd-test-live.sock:active-socket-pids=7331",
                "/tmp/gludd-test-state.lock:lease-marker",
                "/tmp/gludd-test-unknown.sock:socket-inspection-failed",
                "/tmp/gludd-test-raced.json:identity-changed",
            ],
        }

    result = automatic_disk_cleanup.clean_stale_generated_scratch(
        dry_run=True,
        cleanup_runner=cleanup_runner,
    )

    assert calls == [True]
    assert result.removed == ("/tmp/gludd-test-old.json",)
    assert result.skipped == (
        "/tmp/gludd-test-current.json:recent",
        "/tmp/gludd-test-live.sock:active-socket-pids=7331",
        "/tmp/gludd-test-state.lock:lease-marker",
    )
    assert result.errors == (
        "/tmp/gludd-test-unknown.sock:socket-inspection-failed",
        "/tmp/gludd-test-raced.json:identity-changed",
    )
    output = capsys.readouterr().out
    assert "action=stale-generated-scratch status=starting" in output
    assert "action=stale-generated-scratch status=complete" in output


def test_stale_owned_node_cache_cleanup_is_exact_and_visible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    approved = tmp_path.resolve()
    cache = approved / "gludd-npm-cache-public-v1"
    cache.mkdir()
    payload = cache / "_cacache" / "content.bin"
    payload.parent.mkdir()
    payload.write_bytes(b"regenerable")
    ambiguous = approved / "gludd-npm-cache-public-v1-copy"
    ambiguous.mkdir()
    for path in (payload, payload.parent, cache, ambiguous):
        os.utime(path, (100.0, 100.0))

    result = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache, ambiguous),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )

    assert result.removed == (str(cache),)
    assert result.skipped == (f"{ambiguous}:unapproved-cache-name",)
    assert result.errors == ()
    assert not cache.exists()
    assert ambiguous.is_dir()
    output = capsys.readouterr().out
    assert "action=node-download-cache status=starting candidates=2" in output
    assert "action=node-download-cache status=complete inspected=2 removed=1" in output


def test_stale_playwright_browser_cache_is_owned_and_process_safe(
    tmp_path: Path,
) -> None:
    approved = tmp_path.resolve()
    cache = approved / "gludd-playwright-browsers"
    cache.mkdir()
    browser = cache / "webkit" / "browser"
    browser.parent.mkdir()
    browser.write_bytes(b"regenerable")
    for path in (browser, browser.parent, cache):
        os.utime(path, (100.0, 100.0))

    active = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [7331],
    )

    assert active.skipped == (f"{cache}:active-pids=7331",)
    assert cache.is_dir()

    idle = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )

    assert idle.removed == (str(cache),)
    assert idle.errors == ()
    assert not cache.exists()


def test_pressure_reclaim_removes_only_fresh_owned_playwright_cache(
    tmp_path: Path,
) -> None:
    approved = tmp_path.resolve()
    playwright_cache = approved / "gludd-playwright-browsers"
    npm_cache = approved / "gludd-npm-cache-public-v1"
    for cache in (playwright_cache, npm_cache):
        cache.mkdir()
        (cache / "fresh.bin").write_bytes(b"regenerable")
    external = approved / "external-browser-data"
    external.mkdir()
    sentinel = external / "preserve.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    (playwright_cache / "framework-current").symlink_to(
        external, target_is_directory=True
    )

    process_checks: list[Path] = []

    def idle_process_census(path: Path) -> list[int]:
        process_checks.append(path)
        return []

    result = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(playwright_cache, npm_cache),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=idle_process_census,
        pressure_reclaim=True,
    )

    assert result.removed == (str(playwright_cache),)
    assert result.skipped == (f"{npm_cache}:recent",)
    assert result.errors == ()
    assert process_checks == [playwright_cache, playwright_cache]
    assert not playwright_cache.exists()
    assert npm_cache.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_node_cache_cleanup_refuses_unsafe_fresh_and_unbounded_candidates(
    tmp_path: Path,
) -> None:
    approved = tmp_path.resolve()
    cache = approved / "gludd-npm-cache-public-v1"

    cache.write_text("not a directory\n", encoding="utf-8")
    regular = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )
    assert regular.skipped == (f"{cache}:unsupported-file-type",)
    cache.unlink()

    external = tmp_path / "external-cache"
    external.mkdir()
    cache.symlink_to(external, target_is_directory=True)
    linked = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )
    assert linked.skipped == (f"{cache}:symlink",)
    cache.unlink()

    cache.mkdir()
    fresh = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )
    assert fresh.skipped == (f"{cache}:recent",)
    os.utime(cache, (100.0, 100.0))
    (cache / "one").write_bytes(b"1")
    (cache / "two").write_bytes(b"2")
    for child in cache.iterdir():
        os.utime(child, (100.0, 100.0))
    os.utime(cache, (100.0, 100.0))
    bounded = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        max_entries=1,
        active_process_pids=lambda _path: [],
    )
    assert bounded.skipped == (f"{cache}:entry-limit",)
    assert cache.is_dir()


def test_node_cache_cleanup_refuses_active_and_raced_owners(tmp_path: Path) -> None:
    approved = tmp_path.resolve()
    cache = approved / "gludd-npm-cache-public-v1"
    cache.mkdir()
    os.utime(cache, (100.0, 100.0))

    active = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [7331],
    )
    assert active.skipped == (f"{cache}:active-pids=7331",)

    calls = 0

    def race_after_initial_inspection(path: Path) -> list[int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            (path / "raced").write_text("new cache entry\n", encoding="utf-8")
        return []

    raced = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=race_after_initial_inspection,
    )
    assert raced.skipped == (f"{cache}:identity-changed",)
    assert cache.is_dir()


def test_node_cache_cleanup_additional_fail_closed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = tmp_path.resolve()
    cache = approved / "gludd-npm-cache-public-v1"

    invalid = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(),
        approved_tmp_root=approved,
        min_age_seconds=-1,
        active_process_pids=lambda _path: [],
    )
    assert invalid.errors == ("node-download-cache:invalid-bound",)

    missing_root = tmp_path / "missing-root"
    unavailable = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(),
        approved_tmp_root=missing_root,
        active_process_pids=lambda _path: [],
    )
    assert unavailable.errors == (f"{missing_root}:temp-root-inspection-failed",)

    absent = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        active_process_pids=lambda _path: [],
    )
    assert absent.skipped == (f"{cache}:cache-absent",)

    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    outside = outside_root / cache.name
    outside.mkdir()
    outside_result = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(outside,),
        approved_tmp_root=approved,
        active_process_pids=lambda _path: [],
    )
    assert outside_result.skipped == (f"{outside}:outside-canonical-temp-root",)

    cache.mkdir()
    linked_target = tmp_path / "linked-target"
    linked_target.write_text("preserve\n", encoding="utf-8")
    (cache / "link").symlink_to(linked_target)
    os.utime(cache, (100.0, 100.0))
    unsafe = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
    )
    assert unsafe.skipped == (f"{cache}:unsafe-tree-entry",)
    assert linked_target.read_text(encoding="utf-8") == "preserve\n"
    (cache / "link").unlink()

    def stale_cache() -> None:
        for child in cache.iterdir():
            if child.is_dir():
                automatic_disk_cleanup._remove_tree(child)
            else:
                child.unlink()
        os.utime(cache, (100.0, 100.0))

    stale_cache()

    def fail_process(_path: Path) -> list[int]:
        raise automatic_disk_cleanup.ProcessInspectionError("unavailable")

    initial_failure = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=fail_process,
    )
    assert initial_failure.errors == (f"{cache}:process-inspection-failed",)

    process_reads: list[list[int] | Exception] = [
        [],
        automatic_disk_cleanup.ProcessInspectionError("late"),
    ]

    def fail_process_recheck(_path: Path) -> list[int]:
        value = process_reads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    recheck_failure = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=fail_process_recheck,
    )
    assert recheck_failure.errors == (f"{cache}:process-revalidation-failed",)

    active_reads = iter(([], [8123]))
    late_active = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: next(active_reads),
    )
    assert late_active.skipped == (f"{cache}:active-pids=8123",)

    dry_run = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
        dry_run=True,
    )
    assert dry_run.skipped == (f"{cache}:would remove node download cache",)

    def refuse_removal(_path: Path) -> None:
        raise PermissionError("refused")

    removal_failure = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
        remove_tree=refuse_removal,
    )
    assert removal_failure.errors == (f"{cache}:removal-failed",)

    verification_failure = automatic_disk_cleanup.clean_stale_node_download_caches(
        cache_roots=(cache,),
        approved_tmp_root=approved,
        now_epoch=10_000,
        min_age_seconds=3_600,
        active_process_pids=lambda _path: [],
        remove_tree=lambda _path: None,
    )
    assert verification_failure.errors == (f"{cache}:removal-verification-failed",)


def test_default_node_package_manager_census_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_output = (
        "bad-line\n"
        "not-a-pid npm ci\n"
        "123 /usr/local/bin/npm ci\n"
        "456 /usr/bin/node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci\n"
        "789 /usr/bin/node application.js\n"
        "790 /Users/example/.venv/bin/python -m playwright install webkit\n"
        "791 /tmp/gludd-playwright-browsers/webkit/Playwright.sh\n"
    )
    monkeypatch.setattr(
        automatic_disk_cleanup.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, process_output, ""
        ),
    )
    monkeypatch.setattr(automatic_disk_cleanup.os, "getpid", lambda: 999)

    assert automatic_disk_cleanup._active_node_package_manager_pids(
        Path("/tmp/gludd-npm-cache-public-v1")
    ) == [123, 456]
    assert automatic_disk_cleanup._active_node_package_manager_pids(
        Path("/tmp/gludd-playwright-browsers")
    ) == [123, 456, 790, 791]

    monkeypatch.setattr(
        automatic_disk_cleanup.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    with pytest.raises(automatic_disk_cleanup.ProcessInspectionError):
        automatic_disk_cleanup._active_node_package_manager_pids(
            Path("/tmp/gludd-npm-cache-public-v1")
        )


def test_preflight_is_noop_when_both_thresholds_are_healthy(capsys: pytest.CaptureFixture[str]) -> None:
    cleanup_calls = 0

    def unexpected_cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise AssertionError("healthy preflight must not clean")

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: DiskSnapshot(scratch_mb=10.0, disk_pct=40.0),
        cleanup=unexpected_cleanup,
    )

    assert result == 0
    assert cleanup_calls == 0
    assert "phase=inspect status=healthy" in capsys.readouterr().out


def test_preflight_cleans_then_rechecks_both_thresholds(capsys: pytest.CaptureFixture[str]) -> None:
    snapshots = iter(
        (
            DiskSnapshot(scratch_mb=120.0, disk_pct=91.0),
            DiskSnapshot(scratch_mb=20.0, disk_pct=70.0),
        )
    )
    cleanup_calls = 0

    def cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return automatic_disk_cleanup.CleanupResult(
            removed=("/tmp/gludd-worktrees/done/.venv",), skipped=(), errors=()
        )

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: next(snapshots),
        cleanup=cleanup,
    )

    assert result == 0
    assert cleanup_calls == 1
    output = capsys.readouterr().out
    assert "phase=cleanup status=starting" in output
    assert "phase=recheck status=healthy" in output


def test_preflight_repeats_cleanup_while_usage_improves_until_healthy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshots = iter(
        (
            DiskSnapshot(scratch_mb=995.5, disk_pct=92.0),
            DiskSnapshot(scratch_mb=114.5, disk_pct=92.0),
            DiskSnapshot(scratch_mb=42.0, disk_pct=89.0),
        )
    )
    cleanup_calls = 0

    def cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return automatic_disk_cleanup.CleanupResult(
            removed=(f"/tmp/gludd-worktrees/done-{cleanup_calls}/.venv",),
            skipped=(),
            errors=(),
        )

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: next(snapshots),
        cleanup=cleanup,
    )

    assert result == 0
    assert cleanup_calls == 2
    output = capsys.readouterr().out
    assert "phase=cleanup status=starting pass=1" in output
    assert "phase=recheck status=pressure pass=1" in output
    assert "phase=cleanup status=starting pass=2" in output
    assert "phase=recheck status=healthy pass=2" in output


def test_preflight_stops_fail_closed_when_cleanup_makes_no_measurable_progress(
    capsys: pytest.CaptureFixture[str],
) -> None:
    high = DiskSnapshot(scratch_mb=114.5, disk_pct=92.0)
    cleanup_calls = 0

    def cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return automatic_disk_cleanup.CleanupResult(
            removed=("/tmp/gludd-worktrees/done/.pytest_cache",),
            skipped=(),
            errors=(),
        )

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: high,
        cleanup=cleanup,
    )

    assert result == 1
    assert cleanup_calls == 1
    captured = capsys.readouterr()
    assert "phase=recheck status=failed pass=1 reason=no-progress" in captured.err


def test_preflight_bounds_repetitive_cleanup_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    high = DiskSnapshot(scratch_mb=114.5, disk_pct=92.0)
    detail_count = automatic_disk_cleanup.MAX_CLEANUP_DETAILS_PER_KIND + 5

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: high,
        cleanup=lambda: automatic_disk_cleanup.CleanupResult(
            removed=(),
            skipped=tuple(f"/tmp/gludd-test-{index}:recent" for index in range(detail_count)),
            errors=(),
        ),
    )

    assert result == 1
    output = capsys.readouterr().out
    assert output.count("action=skip pass=1 detail=") == (
        automatic_disk_cleanup.MAX_CLEANUP_DETAILS_PER_KIND
    )
    assert "action=skip-summary pass=1 shown=20 omitted=5" in output


def test_preflight_stops_at_bounded_cleanup_pass_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshots = iter(
        (
            DiskSnapshot(scratch_mb=140.0, disk_pct=94.0),
            DiskSnapshot(scratch_mb=130.0, disk_pct=93.0),
            DiskSnapshot(scratch_mb=120.0, disk_pct=92.0),
        )
    )
    cleanup_calls = 0

    def cleanup() -> automatic_disk_cleanup.CleanupResult:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return automatic_disk_cleanup.CleanupResult((), (), ())

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: next(snapshots),
        cleanup=cleanup,
        max_cleanup_passes=2,
    )

    assert result == 1
    assert cleanup_calls == 2
    assert "phase=recheck status=failed pass=2 reason=pass-limit" in capsys.readouterr().err


@pytest.mark.parametrize(
    "after",
    [
        DiskSnapshot(scratch_mb=101.0, disk_pct=20.0),
        DiskSnapshot(scratch_mb=20.0, disk_pct=91.0),
    ],
)
def test_preflight_fails_closed_when_either_threshold_remains_high(
    after: DiskSnapshot, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshots = iter((after, after))

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: next(snapshots),
        cleanup=lambda: automatic_disk_cleanup.CleanupResult((), (), ()),
    )

    assert result == 1
    assert "phase=recheck status=failed" in capsys.readouterr().err


def test_preflight_fails_closed_on_cleanup_or_inspection_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    high = DiskSnapshot(scratch_mb=120.0, disk_pct=20.0)
    assert (
        automatic_disk_cleanup.run_preflight(
            inspect_usage=lambda: high,
            cleanup=lambda: automatic_disk_cleanup.CleanupResult(
                (), (), ("registry unavailable",)
            ),
        )
        == 1
    )

    def fail_inspection() -> DiskSnapshot:
        raise automatic_disk_cleanup.DiskInspectionError("df unavailable")

    assert automatic_disk_cleanup.run_preflight(inspect_usage=fail_inspection) == 1
    assert "status=failed" in capsys.readouterr().err


def test_preflight_fails_closed_when_cleanup_raises(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_cleanup() -> automatic_disk_cleanup.CleanupResult:
        raise OSError("cleanup unavailable")

    result = automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: DiskSnapshot(scratch_mb=120.0, disk_pct=91.0),
        cleanup=fail_cleanup,
    )

    assert result == 1
    assert "phase=cleanup status=failed pass=1" in capsys.readouterr().err


def test_canonical_inspection_and_worktree_discovery_are_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        automatic_disk_cleanup.check_disk_usage,
        "_gludd_tmp_inspection",
        lambda: (12.5, []),
    )
    monkeypatch.setattr(
        automatic_disk_cleanup.check_disk_usage, "_disk_usage_pct", lambda: 33.0
    )
    main = tmp_path / "main"
    main.mkdir()
    porcelain = f"worktree {main}\nHEAD abc\nbranch refs/heads/development\n"
    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args: subprocess.CompletedProcess(args, 0, porcelain, ""),
    )

    assert automatic_disk_cleanup.inspect_usage() == DiskSnapshot(12.5, 33.0)
    assert automatic_disk_cleanup._registered_worktrees() == [
        WorktreeRecord(main.resolve(), "development", False)
    ]

    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args: subprocess.CompletedProcess(args, 0, "", ""),
    )
    with pytest.raises(
        automatic_disk_cleanup.DiskInspectionError, match="returned no worktrees"
    ):
        automatic_disk_cleanup._registered_worktrees()


def test_git_common_evidence_root_and_integration_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    common = tmp_path / ".git"
    common.mkdir()
    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args, **_kwargs: subprocess.CompletedProcess(
            args, 0, f"{common}\n", ""
        ),
    )
    assert automatic_disk_cleanup._canonical_evidence_archive_root() == (
        common / "gludd-release-evidence"
    )
    with pytest.raises(automatic_disk_cleanup.DiskInspectionError):
        automatic_disk_cleanup._integration_points([])

    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args, **_kwargs: subprocess.CompletedProcess(args, 1, "", "bad"),
    )
    with pytest.raises(
        automatic_disk_cleanup.DiskInspectionError,
        match="common directory is unavailable",
    ):
        automatic_disk_cleanup._canonical_evidence_archive_root()


def test_git_common_evidence_root_rejects_non_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    not_a_directory = tmp_path / "git-common-file"
    not_a_directory.write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe,
        "_git",
        lambda *args, **_kwargs: subprocess.CompletedProcess(
            args, 0, f"{not_a_directory}\n", ""
        ),
    )

    with pytest.raises(
        automatic_disk_cleanup.DiskInspectionError,
        match="common directory is invalid",
    ):
        automatic_disk_cleanup._canonical_evidence_archive_root()


def test_integration_points_skip_unavailable_empty_and_invalid_refs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = _record(tmp_path / "main", "development")

    def one_valid(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("-C", str(main.path)):
            return subprocess.CompletedProcess(args, 0, "main-head\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            if "development" in args[-1]:
                return subprocess.CompletedProcess(args, 1, "", "missing")
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ("show", "-s", "--format=%ct"):
            return subprocess.CompletedProcess(args, 0, "42\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe, "_git", one_valid
    )
    assert automatic_disk_cleanup._integration_points([main]) == (
        automatic_disk_cleanup.IntegrationPoint("main-head", 42),
    )

    def no_valid(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("-C", str(main.path)):
            return subprocess.CompletedProcess(args, 0, "bad-time\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            revision = "negative-time" if "development" in args[-1] else "failed-time"
            return subprocess.CompletedProcess(args, 0, f"{revision}\n", "")
        if args[-1] == "bad-time":
            return subprocess.CompletedProcess(args, 0, "invalid\n", "")
        if args[-1] == "negative-time":
            return subprocess.CompletedProcess(args, 0, "-1\n", "")
        if args[-1] == "failed-time":
            return subprocess.CompletedProcess(args, 1, "7\n", "failed")
        raise AssertionError(args)

    monkeypatch.setattr(
        automatic_disk_cleanup.prune_worktrees_safe, "_git", no_valid
    )
    with pytest.raises(
        automatic_disk_cleanup.DiskInspectionError,
        match="no trusted integration commit",
    ):
        automatic_disk_cleanup._integration_points([main])


def test_default_uv_process_and_prune_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process_output = "bad-line\nnot-a-pid /usr/bin/uv\n123 /usr/bin/uv run\n456 python test.py\n"
    monkeypatch.setattr(
        automatic_disk_cleanup.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, process_output, ""
        ),
    )
    monkeypatch.setattr(automatic_disk_cleanup.os, "getpid", lambda: 999)
    assert automatic_disk_cleanup._active_uv_process_pids() == [123]

    monkeypatch.setattr(automatic_disk_cleanup.shutil, "which", lambda _name: None)
    assert automatic_disk_cleanup._run_uv_cache_prune(tmp_path) is False

    observed: dict[str, object] = {}

    def run(*args, **kwargs):
        observed["args"] = args[0]
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(automatic_disk_cleanup.shutil, "which", lambda _name: "/bin/uv")
    monkeypatch.setattr(automatic_disk_cleanup.subprocess, "run", run)
    assert automatic_disk_cleanup._run_uv_cache_prune(tmp_path) is True
    assert observed["args"] == ["/bin/uv", "cache", "prune"]
    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["UV_CACHE_DIR"] == str(tmp_path)
    assert automatic_disk_cleanup._run_uv_cache_clean(tmp_path) is True
    assert observed["args"] == ["/bin/uv", "cache", "clean"]

    monkeypatch.setattr(
        automatic_disk_cleanup.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    assert automatic_disk_cleanup._run_uv_cache_prune(tmp_path) is False
    assert automatic_disk_cleanup._run_uv_cache_clean(tmp_path) is False
    with pytest.raises(automatic_disk_cleanup.ProcessInspectionError):
        automatic_disk_cleanup._active_uv_process_pids()


def test_shared_uv_cache_additional_fail_closed_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=missing,
        approved_cache_root=missing,
    ).errors == (f"{missing}:uv-cache-inspection-failed",)

    cache = tmp_path / "cache"
    cache.mkdir()
    other_cache = tmp_path / "other-cache"
    other_cache.mkdir()
    unsafe = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=other_cache,
    )
    assert unsafe.errors == (f"{cache}:unsafe-uv-cache",)
    external = tmp_path / "external-cache"
    external.mkdir()
    symlink_cache = tmp_path / "symlink-cache"
    symlink_cache.symlink_to(external, target_is_directory=True)
    symlink_result = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=symlink_cache,
        approved_cache_root=symlink_cache,
    )
    assert symlink_result.errors == (f"{symlink_cache}:unsafe-uv-cache",)
    file_cache = tmp_path / "file-cache"
    file_cache.write_text("not a directory\n", encoding="utf-8")
    file_result = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=file_cache,
        approved_cache_root=file_cache,
    )
    assert file_result.errors == (f"{file_cache}:unsafe-uv-cache",)

    def fail_process() -> list[int]:
        raise automatic_disk_cleanup.ProcessInspectionError("unavailable")

    initial_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=fail_process,
    )
    assert initial_failure.errors == (f"{cache}:uv-process-inspection-failed",)

    recheck_reads = iter(([], automatic_disk_cleanup.ProcessInspectionError("late")))

    def fail_recheck() -> list[int]:
        value = next(recheck_reads)
        if isinstance(value, Exception):
            raise value
        return value

    recheck_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=fail_recheck,
    )
    assert recheck_failure.errors == (f"{cache}:uv-process-revalidation-failed",)
    dry_run = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: [],
        dry_run=True,
    )
    assert dry_run.skipped == (f"{cache}:would prune shared uv cache",)
    prune_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: [],
        run_prune=lambda _path: False,
    )
    assert prune_failure.errors == (f"{cache}:uv-cache-prune-failed",)

    final_reads = iter(
        ([], [], automatic_disk_cleanup.ProcessInspectionError("final"))
    )

    def fail_final_check() -> list[int]:
        value = next(final_reads)
        if isinstance(value, Exception):
            raise value
        return value

    final_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=fail_final_check,
        run_prune=lambda _path: True,
    )
    assert final_failure.errors == (f"{cache}:uv-process-final-check-failed",)
    clean_failure = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=cache,
        approved_cache_root=cache,
        active_uv_pids=lambda: [],
        run_prune=lambda _path: True,
        run_clean=lambda _path: False,
    )
    assert clean_failure.errors == (f"{cache}:uv-cache-clean-failed",)


def test_shared_uv_cache_missing_is_clean_only_for_convergent_cleanup(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"

    result = automatic_disk_cleanup.prune_shared_uv_cache(
        cache_root=missing,
        approved_cache_root=missing,
        missing_is_clean=True,
    )

    assert result.removed == ()
    assert result.skipped == (f"{missing}:uv-cache-absent",)
    assert result.errors == ()


def test_default_cleanup_discovers_and_preserves_git_worktrees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = _record(tmp_path / "main", "development")
    root = tmp_path / "gludd-worktrees"
    finished = _record(root / "finished", "feature/finished")
    cache = finished.path / ".venv"
    cache.mkdir()
    records = [main, finished]
    registry_path = tmp_path / "active.json"
    monkeypatch.setattr(automatic_disk_cleanup, "DEFAULT_TMP_WORKTREE_ROOT", root)
    monkeypatch.setattr(automatic_disk_cleanup, "_registered_worktrees", lambda: records)
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "_integration_points",
        lambda _records: (automatic_disk_cleanup.IntegrationPoint("main-head", 1),),
    )
    evidence_archive = tmp_path / "git-common" / "gludd-release-evidence"
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "_canonical_evidence_archive_root",
        lambda: evidence_archive,
    )
    monkeypatch.setattr(
        automatic_disk_cleanup.workstream_registry,
        "default_registry_path",
        lambda: registry_path,
    )
    monkeypatch.setattr(
        automatic_disk_cleanup.clean_ci_shard_scratch,
        "_active_process_pids",
        lambda _path: [],
    )
    stale_file = tmp_path / "gludd-test-stale.json"
    monkeypatch.setattr(
        automatic_disk_cleanup.clean_ci_shard_scratch,
        "clean_ci_shard_scratch",
        lambda **kwargs: {"removed": [str(stale_file)], "skipped": []},
    )
    uv_cleanup_calls: list[tuple[bool, bool]] = []
    node_cleanup_calls: list[tuple[bool, bool]] = []
    terraform_cleanup_calls: list[bool] = []
    invoking_cleanup_calls: list[dict[str, object]] = []
    invoking_cache = tmp_path / "invoking" / ".pytest_cache"
    main_cache = main.path / automatic_disk_cleanup.TERRAFORM_PLUGIN_CACHE_PATH

    def invoking_cleanup(**kwargs: object) -> automatic_disk_cleanup.CleanupResult:
        invoking_cleanup_calls.append(kwargs)
        removed = main_cache if kwargs["worktree"] == main.path else invoking_cache
        return automatic_disk_cleanup.CleanupResult((str(removed),), (), ())

    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_invoking_worktree_disposable_caches",
        invoking_cleanup,
    )

    def uv_cleanup(
        *, dry_run: bool = False, missing_is_clean: bool = False
    ) -> automatic_disk_cleanup.CleanupResult:
        uv_cleanup_calls.append((dry_run, missing_is_clean))
        return automatic_disk_cleanup.CleanupResult((), (), ())

    monkeypatch.setattr(automatic_disk_cleanup, "prune_shared_uv_cache", uv_cleanup)

    def node_cleanup(
        *, dry_run: bool = False, pressure_reclaim: bool = False
    ) -> automatic_disk_cleanup.CleanupResult:
        node_cleanup_calls.append((dry_run, pressure_reclaim))
        return automatic_disk_cleanup.CleanupResult((), (), ())

    monkeypatch.setattr(
        automatic_disk_cleanup, "clean_stale_node_download_caches", node_cleanup
    )

    def terraform_cleanup(
        *, dry_run: bool = False
    ) -> automatic_disk_cleanup.CleanupResult:
        terraform_cleanup_calls.append(dry_run)
        return automatic_disk_cleanup.CleanupResult((), (), ())

    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_owned_terraform_provider_caches",
        terraform_cleanup,
    )

    result = automatic_disk_cleanup._automatic_cleanup()

    assert result.removed == (str(invoking_cache), str(main_cache), str(stale_file))
    assert any("completion proof required" in item for item in result.skipped)
    assert cache.exists()
    assert finished.path.exists()
    assert main.path.exists()
    assert uv_cleanup_calls == [(False, True)]
    assert node_cleanup_calls == [(False, True)]
    assert terraform_cleanup_calls == [False]
    assert len(invoking_cleanup_calls) == 2
    assert invoking_cleanup_calls[0]["records"] == records
    assert invoking_cleanup_calls[0]["approved_roots"] == (
        main.path / ".claude/worktrees",
        root,
    )
    assert invoking_cleanup_calls[0]["dry_run"] is False
    assert invoking_cleanup_calls[1]["worktree"] == main.path
    assert invoking_cleanup_calls[1]["approved_worktrees"] == (main.path,)
    assert invoking_cleanup_calls[1]["cache_paths"] == (
        automatic_disk_cleanup.TERRAFORM_PLUGIN_CACHE_PATH,
    )
    assert invoking_cleanup_calls[1]["dry_run"] is False


def test_default_cleanup_and_recheck_inspection_fail_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_discovery() -> list[WorktreeRecord]:
        raise automatic_disk_cleanup.DiskInspectionError("git unavailable")

    monkeypatch.setattr(automatic_disk_cleanup, "_registered_worktrees", fail_discovery)
    assert automatic_disk_cleanup._automatic_cleanup().errors == (
        "worktree-discovery:inspection-failed",
    )

    calls = 0

    def fail_recheck() -> DiskSnapshot:
        nonlocal calls
        calls += 1
        if calls == 1:
            return DiskSnapshot(120.0, 20.0)
        raise automatic_disk_cleanup.DiskInspectionError("df unavailable")

    assert (
        automatic_disk_cleanup.run_preflight(
            inspect_usage=fail_recheck,
            cleanup=lambda: automatic_disk_cleanup.CleanupResult(
                (), ("active worktree",), ()
            ),
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "action=skip" in captured.out
    assert "phase=recheck status=failed" in captured.err


def test_default_cleanup_runs_independent_reclaimers_after_discovery_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_discovery() -> list[WorktreeRecord]:
        raise automatic_disk_cleanup.DiskInspectionError("git unavailable")

    stale = tmp_path / "stale-scratch"
    uv_cache = tmp_path / "uv-cache"
    providers = tmp_path / "providers"
    monkeypatch.setattr(automatic_disk_cleanup, "_registered_worktrees", fail_discovery)
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_stale_generated_scratch",
        lambda **_kwargs: automatic_disk_cleanup.CleanupResult((str(stale),), (), ()),
    )
    node_cache = tmp_path / "node-cache"
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_stale_node_download_caches",
        lambda **_kwargs: automatic_disk_cleanup.CleanupResult(
            (str(node_cache),), (), ()
        ),
    )
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "prune_shared_uv_cache",
        lambda **_kwargs: automatic_disk_cleanup.CleanupResult((str(uv_cache),), (), ()),
    )
    monkeypatch.setattr(
        automatic_disk_cleanup,
        "clean_owned_terraform_provider_caches",
        lambda **_kwargs: automatic_disk_cleanup.CleanupResult((str(providers),), (), ()),
    )

    result = automatic_disk_cleanup._automatic_cleanup()

    assert result.removed == (
        str(stale),
        str(node_cache),
        str(uv_cache),
        str(providers),
    )
    assert result.errors == ("worktree-discovery:inspection-failed",)


def test_main_delegates_to_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(automatic_disk_cleanup, "run_preflight", lambda: 7)

    assert automatic_disk_cleanup.main() == 7


def test_main_dry_run_injects_non_mutating_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_modes: list[bool] = []

    def cleanup(
        *,
        dry_run: bool = False,
        receipt_grace_seconds: int = (
            automatic_disk_cleanup.DEFAULT_COMMIT_RECEIPT_GRACE_SECONDS
        ),
    ) -> automatic_disk_cleanup.CleanupResult:
        assert (
            receipt_grace_seconds
            == automatic_disk_cleanup.DEFAULT_COMMIT_RECEIPT_GRACE_SECONDS
        )
        cleanup_modes.append(dry_run)
        return automatic_disk_cleanup.CleanupResult((), (), ())

    def preflight(*, cleanup=None) -> int:
        assert cleanup is not None
        cleanup()
        return 7

    monkeypatch.setattr(automatic_disk_cleanup, "_automatic_cleanup", cleanup)
    monkeypatch.setattr(automatic_disk_cleanup, "run_preflight", preflight)

    assert automatic_disk_cleanup.main(["--dry-run"]) == 7
    assert cleanup_modes == [True]


def test_main_configures_receipt_grace_and_enforces_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_graces: list[int] = []

    def cleanup(
        *, dry_run: bool = False, receipt_grace_seconds: int
    ) -> automatic_disk_cleanup.CleanupResult:
        assert dry_run is False
        observed_graces.append(receipt_grace_seconds)
        return automatic_disk_cleanup.CleanupResult((), (), ())

    def preflight(*, cleanup=None) -> int:
        assert cleanup is not None
        cleanup()
        return 7

    monkeypatch.setattr(automatic_disk_cleanup, "_automatic_cleanup", cleanup)
    monkeypatch.setattr(automatic_disk_cleanup, "run_preflight", preflight)

    assert automatic_disk_cleanup.main(["--receipt-grace-seconds", "3600"]) == 7
    assert observed_graces == [3600]
    with pytest.raises(SystemExit):
        automatic_disk_cleanup.main(["--receipt-grace-seconds", "1799"])
    with pytest.raises(SystemExit):
        automatic_disk_cleanup.main(["--receipt-grace-seconds", "not-an-integer"])


def test_make_and_precommit_gates_run_the_automatic_preflight() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    target = makefile.split("\ndisk-cleanup-preflight:\n", 1)[1].split(
        "\ncheck-disk:", 1
    )[0]
    gate = makefile.split("\ngate:", 1)[1].split("\n\n", 1)[0]
    gate_preflights = makefile.split("GATE_PREFLIGHT_TARGETS :=", 1)[1].split(
        "GATE_PREFLIGHT_STATUS", 1
    )[0]
    gate_fast = makefile.split("\ngate-fast:", 1)[1].split("\n", 1)[0]
    gate_lite = makefile.split("\ngate-lite:", 1)[1].split("\n", 1)[0]

    assert "$(SYSTEM_PYTHON) -m scripts.automatic_disk_cleanup" in target
    assert "DISK_CLEANUP_PREFLIGHT_DRY_RUN" in target
    assert "DISK_CLEANUP_RECEIPT_GRACE_SECONDS" in target
    assert "--receipt-grace-seconds" in target
    assert "--dry-run" in target
    assert "$(UV) run" not in target
    assert "disk-cleanup-preflight" in gate_preflights
    assert "_gate-preflights" in gate
    assert "disk-cleanup-preflight" in gate_fast
    assert "disk-cleanup-preflight" in gate_lite
    assert "entry: make check-disk CHECK_DISK_VALIDATE_ONLY=0" in hooks


def test_feature_document_records_zdd_rollback_and_long_lived_reports() -> None:
    document = (ROOT / "docs" / "automatic-disk-cleanup.md").read_text(encoding="utf-8")

    assert "Zero-downtime" in document
    assert "Rollback" in document
    assert "github.com/astral-sh/uv/issues/11432" in document
    assert "github.com/astral-sh/uv/issues/11694" in document
    assert "github.com/stablyai/orca/issues/10562" in document
    assert "github.com/python/cpython/issues/111246" in document
    assert "github.com/pytest-dev/pytest/discussions/10325" in document
    assert "Eight passes" in document
    assert "lsof" in document


def test_gate_lifecycle_documents_owned_node_cache_reclamation() -> None:
    document = (
        ROOT / "docs" / "features" / "GATE_RESOURCE_LIFECYCLE.md"
    ).read_text(encoding="utf-8")

    assert "gludd-npm-cache-public-v1" in document
    assert "50,000" in document
    assert "zero-byte regular-file lookalikes" in document
    assert "github.com/npm/cli/issues/3176" in document
    assert "github.com/npm/npm/issues/2500" in document
    assert "/tmp/gludd-playwright-browsers" in document
    assert "pressure after repeat idle and identity proofs" in document
    assert "github.com/microsoft/playwright/issues/37354" in document
    assert "github.com/microsoft/playwright/issues/36682" in document
    assert "github.com/microsoft/playwright/issues/5797" in document
    assert "ZDD shape" in document
