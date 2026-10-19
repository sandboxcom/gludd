"""Bounded native UV-phase diagnostics preserve every cleanup authority."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import pytest
from scripts import automatic_disk_cleanup
from scripts.automatic_disk_cleanup import DiskSnapshot


@pytest.mark.parametrize("uv_state", ["skipped", "refused", "complete", "overflow"])
def test_default_cleanup_reports_uv_phase_before_aggregate_detail_truncation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str], uv_state: str,
) -> None:
    def fail_discovery() -> NoReturn:
        raise automatic_disk_cleanup.DiskInspectionError("fixture unavailable")

    empty = automatic_disk_cleanup.CleanupResult((), (), ())
    scratch = automatic_disk_cleanup.CleanupResult(
        (), tuple(f"scratch-skip-{index}" for index in range(409)),
        tuple(f"scratch-error-{index}" for index in range(409)),
    )
    monkeypatch.setattr(automatic_disk_cleanup, "_registered_worktrees", fail_discovery)
    monkeypatch.setattr(automatic_disk_cleanup, "clean_stale_generated_scratch", lambda **_kw: scratch)
    monkeypatch.setattr(automatic_disk_cleanup, "clean_stale_node_download_caches", lambda **_kw: empty)
    monkeypatch.setattr(automatic_disk_cleanup, "clean_owned_terraform_provider_caches", lambda **_kw: empty)
    cache = tmp_path / "uv-cache"
    cache.mkdir()
    keep = cache / "keep.bin"
    keep.write_bytes(b"preserved")
    native_prune = automatic_disk_cleanup.prune_shared_uv_cache
    uv_calls: list[tuple[bool, bool]] = []
    inspections: list[str] = []
    operations: list[str] = []
    uv_results: list[automatic_disk_cleanup.CleanupResult] = []

    def active_pids() -> list[int]:
        inspections.append("inspect")
        if uv_state == "refused":
            raise automatic_disk_cleanup.ProcessInspectionError("fixture unavailable")
        return [21629, 21636] if uv_state == "skipped" else []

    def run_prune(_path: Path) -> bool:
        operations.append("prune")
        return True

    def run_clean(_path: Path) -> bool:
        operations.append("clean")
        return True

    def uv_cleanup(
        *, dry_run: bool = False, missing_is_clean: bool = False,
    ) -> automatic_disk_cleanup.CleanupResult:
        uv_calls.append((dry_run, missing_is_clean))
        result = (
            automatic_disk_cleanup.CleanupResult(
                (), tuple(f"uv-skip-{index}" for index in range(25)),
                tuple(f"uv-error-{index}" for index in range(25)),
            )
            if uv_state == "overflow"
            else native_prune(
                cache_root=cache, approved_cache_root=cache, active_uv_pids=active_pids,
                run_prune=run_prune, run_clean=run_clean,
                dry_run=dry_run, missing_is_clean=missing_is_clean,
            )
        )
        uv_results.append(result)
        return result

    monkeypatch.setattr(automatic_disk_cleanup, "prune_shared_uv_cache", uv_cleanup)
    combined: list[automatic_disk_cleanup.CleanupResult] = []

    def cleanup() -> automatic_disk_cleanup.CleanupResult:
        result = automatic_disk_cleanup._automatic_cleanup()
        combined.append(result)
        return result

    assert automatic_disk_cleanup.run_preflight(
        inspect_usage=lambda: DiskSnapshot(120.0, 96.0), cleanup=cleanup,
    ) == 1
    captured = capsys.readouterr()
    uv_result = uv_results[0]
    lines = [
        line for line in (captured.out + captured.err).splitlines()
        if line.startswith("phase=cleanup action=shared-uv-cache ")
    ]
    assert len(lines) == 1
    line = lines[0]
    expected_status = "refused" if uv_result.errors else "skipped" if uv_result.skipped else "complete"
    assert f"status={expected_status} " in line
    assert f"removed={len(uv_result.removed)} skipped={len(uv_result.skipped)} errors={len(uv_result.errors)}" in line
    details = json.loads(line.partition(" details=")[2])
    assert details == {
        "skipped": list(uv_result.skipped[:20]), "errors": list(uv_result.errors[:20]),
    }
    stream = captured.err if uv_result.errors else captured.out
    aggregate_action = "refuse" if uv_result.errors else "skip"
    assert stream.index(line) < stream.index(f"phase=cleanup action={aggregate_action} pass=1 ")
    assert captured.out.count("action=skip pass=1 detail=") == 20
    assert captured.err.count("action=refuse pass=1 detail=") == 20
    assert "action=skip-summary pass=1 shown=20 " in captured.out
    assert "action=refuse-summary pass=1 shown=20 " in captured.err
    assert combined[0].removed == uv_result.removed
    assert combined[0].skipped == scratch.skipped + uv_result.skipped
    assert combined[0].errors == (*scratch.errors, "worktree-discovery:inspection-failed", *uv_result.errors)
    assert uv_calls == [(False, True)]
    assert len(inspections) == (0 if uv_state == "overflow" else 3 if uv_state == "complete" else 1)
    assert operations == (["prune", "clean"] if uv_state == "complete" else [])
    assert keep.read_bytes() == b"preserved"
    if uv_state == "skipped":
        assert f"{cache}:active-uv-pids=21629,21636" in details["skipped"]
    elif uv_state == "refused":
        assert f"{cache}:uv-process-inspection-failed" in details["errors"]
