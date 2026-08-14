from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean_worktree_venvs.py"
MAKEFILE = ROOT / "Makefile"


def _load_module() -> ModuleType:
    assert SCRIPT.exists(), "tracked worktree-venv cleaner is required"
    spec = importlib.util.spec_from_file_location(
        "clean_worktree_venvs_under_test", SCRIPT
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registered(*paths: Path) -> Callable[[Path], set[Path]]:
    registered = {path.resolve() for path in paths}
    return lambda _root: set(registered)


def _make_venv(worktree: Path) -> Path:
    venv = worktree / ".venv"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = test\n")
    return venv


def test_invoking_worktree_venv_is_preserved_while_inactive_peer_is_reclaimed(
    tmp_path: Path,
) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    inactive = worktree_root / "inactive"
    invoking_venv = _make_venv(invoking)
    inactive_venv = _make_venv(inactive)

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        registered_worktree_paths=_registered(invoking, inactive),
        active_process_pids=lambda _path: [],
    )

    assert invoking_venv.is_dir()
    assert not inactive_venv.exists()
    assert result["removed"] == [str(inactive_venv)]
    assert result["skipped"] == [f"{invoking_venv}:invoking-worktree"]
    assert result["errors"] == []


def test_nested_invocation_path_still_protects_own_venv(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    nested = invoking / "tests" / "unit"
    nested.mkdir(parents=True)
    invoking_venv = _make_venv(invoking)

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=nested,
        registered_worktree_paths=_registered(invoking),
        active_process_pids=lambda _path: [],
    )

    assert invoking_venv.is_dir()
    assert result["removed"] == []
    assert result["skipped"] == [f"{invoking_venv}:invoking-worktree"]


def test_active_registered_peer_is_preserved(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    active = worktree_root / "active"
    _make_venv(invoking)
    active_venv = _make_venv(active)

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        registered_worktree_paths=_registered(invoking, active),
        active_process_pids=lambda path: [321] if path == active.resolve() else [],
    )

    assert active_venv.is_dir()
    assert f"{active_venv}:active-pids=321" in result["skipped"]
    assert result["removed"] == []


def test_process_inspection_failure_refuses_removal(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    peer = worktree_root / "peer"
    _make_venv(invoking)
    peer_venv = _make_venv(peer)

    def fail_inspection(_path: Path) -> list[int]:
        raise module.ProcessInspectionError("ps unavailable")

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        registered_worktree_paths=_registered(invoking, peer),
        active_process_pids=fail_inspection,
    )

    assert peer_venv.is_dir()
    assert result["removed"] == []
    assert result["errors"] == [f"{peer_venv}:process-inspection-failed"]


def test_registry_failure_is_fail_closed(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    peer = worktree_root / "peer"
    _make_venv(invoking)
    peer_venv = _make_venv(peer)

    def fail_registry(_root: Path) -> set[Path]:
        raise module.WorktreeRegistryError("git unavailable")

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        registered_worktree_paths=fail_registry,
        active_process_pids=lambda _path: [],
    )

    assert peer_venv.is_dir()
    assert result["removed"] == []
    assert result["errors"] == [f"{worktree_root}:registry-failed"]


def test_out_of_namespace_registration_is_fail_closed(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    worktree_root.mkdir()
    invoking = worktree_root / "invoking"
    _make_venv(invoking)
    escaped = tmp_path / "escaped"
    _make_venv(escaped)

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        registered_worktree_paths=_registered(escaped),
        active_process_pids=lambda _path: [],
    )

    assert (escaped / ".venv").is_dir()
    assert result["removed"] == []
    assert result["errors"] == [f"{worktree_root}:registry-failed"]


def test_unavailable_invoking_identity_refuses_all_cleanup(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    peer = worktree_root / "peer"
    peer_venv = _make_venv(peer)
    missing_invoker = worktree_root / "missing"

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=missing_invoker,
        registered_worktree_paths=_registered(peer),
        active_process_pids=lambda _path: [],
    )

    assert peer_venv.is_dir()
    assert result["removed"] == []
    assert result["errors"] == [f"{missing_invoker}:invoker-unavailable"]


def test_unsafe_and_absent_venv_entries_are_never_removed(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    linked = worktree_root / "linked"
    not_directory = worktree_root / "not-directory"
    absent = worktree_root / "absent"
    _make_venv(invoking)
    linked.mkdir(parents=True)
    linked_target = tmp_path / "linked-target"
    linked_target.mkdir()
    (linked / ".venv").symlink_to(linked_target, target_is_directory=True)
    not_directory.mkdir(parents=True)
    (not_directory / ".venv").write_text("not a directory\n")
    absent.mkdir(parents=True)

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        registered_worktree_paths=_registered(
            invoking, linked, not_directory, absent
        ),
        active_process_pids=lambda _path: [],
    )

    assert (linked / ".venv").is_symlink()
    assert (not_directory / ".venv").is_file()
    assert result["removed"] == []
    assert result["errors"] == [
        f"{linked / '.venv'}:unsafe-venv",
        f"{not_directory / '.venv'}:unsafe-venv",
    ]


def test_registration_is_refreshed_immediately_before_removal(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    peer = worktree_root / "peer"
    _make_venv(invoking)
    peer_venv = _make_venv(peer)
    calls = 0

    def changing_registry(_root: Path) -> set[Path]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {invoking.resolve(), peer.resolve()}
        return {invoking.resolve()}

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        registered_worktree_paths=changing_registry,
        active_process_pids=lambda _path: [],
    )

    assert peer_venv.is_dir()
    assert result["removed"] == []
    assert result["errors"] == [f"{peer_venv}:registration-changed"]


def test_registry_refresh_failure_refuses_removal(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    peer = worktree_root / "peer"
    _make_venv(invoking)
    peer_venv = _make_venv(peer)
    calls = 0

    def failing_refresh(_root: Path) -> set[Path]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {invoking.resolve(), peer.resolve()}
        raise module.WorktreeRegistryError("registry refresh failed")

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        registered_worktree_paths=failing_refresh,
        active_process_pids=lambda _path: [],
    )

    assert peer_venv.is_dir()
    assert result["removed"] == []
    assert result["errors"] == [f"{peer_venv}:registry-failed"]


def test_removal_failure_is_reported_and_non_destructive(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    peer = worktree_root / "peer"
    _make_venv(invoking)
    peer_venv = _make_venv(peer)

    def fail_removal(_path: Path) -> None:
        raise OSError("read-only filesystem")

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        registered_worktree_paths=_registered(invoking, peer),
        active_process_pids=lambda _path: [],
        remove_tree=fail_removal,
    )

    assert peer_venv.is_dir()
    assert result["removed"] == []
    assert result["errors"] == [f"{peer_venv}:removal-failed"]


def test_dry_run_reports_eligible_venv_without_removing_it(tmp_path: Path) -> None:
    module = _load_module()
    worktree_root = tmp_path / "gludd-worktrees"
    invoking = worktree_root / "invoking"
    peer = worktree_root / "peer"
    _make_venv(invoking)
    peer_venv = _make_venv(peer)

    result = module.clean_worktree_venvs(
        worktree_roots=(worktree_root,),
        invoking_path=invoking,
        dry_run=True,
        registered_worktree_paths=_registered(invoking, peer),
        active_process_pids=lambda _path: [],
    )

    assert peer_venv.is_dir()
    assert result["eligible"] == [str(peer_venv)]
    assert result["removed"] == []


def test_main_surfaces_refusals_and_returns_nonzero_on_safety_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    monkeypatch.setattr(
        module,
        "clean_worktree_venvs",
        lambda **_kwargs: {
            "eligible": [],
            "removed": [],
            "skipped": ["/tmp/active/.venv:active-pids=7"],
            "errors": ["/tmp/peer/.venv:registration-changed"],
        },
    )

    assert module.main([]) == 1
    output = capsys.readouterr().out
    assert "skipped /tmp/active/.venv:active-pids=7" in output
    assert "error /tmp/peer/.venv:registration-changed" in output
    assert "removed=0 skipped=1 errors=1" in output


def test_main_reports_successful_dry_run_actions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    monkeypatch.setattr(
        module,
        "clean_worktree_venvs",
        lambda **_kwargs: {
            "eligible": ["/tmp/eligible/.venv"],
            "removed": ["/tmp/removed/.venv"],
            "skipped": [],
            "errors": [],
        },
    )

    assert module.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "eligible /tmp/eligible/.venv" in output
    assert "removed /tmp/removed/.venv" in output
    assert "eligible=1 removed=1 skipped=0 errors=0" in output


def test_make_target_is_validate_first_and_has_no_blanket_find_deletion() -> None:
    makefile = MAKEFILE.read_text()
    section = makefile.split("\nclean-worktree-venvs:\n", 1)[1].split(
        "\nclean-worktree-caches:", 1
    )[0]

    assert "CLEAN_WORKTREE_VENVS_VALIDATE_ONLY" in makefile
    assert "tests/unit/test_clean_worktree_venvs.py" in section
    assert "$(SYSTEM_PYTHON) -m scripts.clean_worktree_venvs" in section
    assert "find" not in section
    assert "rm -rf" not in section
