"""Cache policy uses bounded native owners without prohibited home access."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
import scripts.cache_resource_manager as cache_manager
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ("CACHE_RESOURCE_ROOT", "CACHE_RESOURCE_LIMIT", "CACHE_RESOURCE_CANDIDATE",
          "CACHE_RESOURCE_VALIDATE_ONLY")
PRIVATE = tuple("GLUDD_" + name for name in PUBLIC)
RECURSION = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS"}


@dataclass(frozen=True)
class CachePolicyFixture:
    """Keep every native child inside its synthetic home and temporary roots."""

    home: Path
    root: Path
    child: Path
    environment: dict[str, str] = field(repr=False)
    uv: str


def _fixture(tmp_path: Path) -> CachePolicyFixture:
    home = tmp_path / "fixture-home"
    root = home / ".cache"
    child = root / "gludd fixture"
    child.mkdir(parents=True)
    (child / "payload.bin").write_bytes(b"x" * 4096)
    process_tmp = tmp_path / "process-tmp"
    process_tmp.mkdir()
    excluded = RECURSION | set(PUBLIC) | set(PRIVATE)
    environment = {key: value for key, value in os.environ.items() if key not in excluded}
    environment.update(HOME=str(home), TMPDIR=str(process_tmp))
    uv = tmp_path / "uv-tripwire.py"
    uv.write_text(
        "from pathlib import Path\n"
        f"Path({str(tmp_path / 'UV_TRIPWIRE')!r}).write_text('unexpected uv')\n"
        "raise SystemExit(97)\n", encoding="utf-8",
    )
    return CachePolicyFixture(home, root, child, environment, shlex.join([sys.executable, str(uv)]))


def _values(fixture: CachePolicyFixture) -> dict[str, str]:
    return {"CACHE_RESOURCE_ROOT": str(fixture.root), "CACHE_RESOURCE_LIMIT": "20",
            "CACHE_RESOURCE_CANDIDATE": str(fixture.child), "CACHE_RESOURCE_VALIDATE_ONLY": "1"}


def _make(
    tmp_path: Path, fixture: CachePolicyFixture, cache_policy_goal: str,
    values: dict[str, str], *, cache_policy_environment_overrides: bool = False,
    cache_policy_private_origin: str = "", cache_policy_specific_root: Path | None = None,
    cache_policy_mixed: bool = False, cache_policy_system_python: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = dict(fixture.environment)
    command = ["make", "--no-print-directory"]
    if cache_policy_mixed:
        # Native Make otherwise appends a redundant-goal notice to its stdout.
        command.append("--silent")
    if cache_policy_environment_overrides:
        command.append("-e")
    if cache_policy_specific_root:
        probe = tmp_path / "specific.mk"
        probe.write_text(
            f"include {ROOT / 'Makefile'}\n"
            f"{cache_policy_goal}: override CACHE_RESOURCE_ROOT := {cache_policy_specific_root}\n"
            f"{cache_policy_goal}: override CACHE_RESOURCE_LIMIT := 1\n", encoding="utf-8",
        )
        command.extend(["-f", str(probe)])
    elif cache_policy_system_python:
        # Production pins system Python; inject a child only after that real pin.
        probe = tmp_path / "status.mk"
        probe.write_text(
            f"include {ROOT / 'Makefile'}\n"
            f"override SYSTEM_PYTHON := {cache_policy_system_python}\n", encoding="utf-8",
        )
        command.extend(["-f", str(probe)])
    if cache_policy_private_origin:
        payload = "$(shell touch " + str(tmp_path / "PRIVATE_TRIPWIRE") + ")"
        if cache_policy_private_origin == "environment":
            environment.update(dict.fromkeys(PRIVATE, payload))
        else:
            command.extend(f"{key}={payload}" for key in PRIVATE)
    command.extend([
        cache_policy_goal, "SYSTEM_PYTHON=" + (cache_policy_system_python or sys.executable),
        "UV=" + fixture.uv,
        *(f"{key}={value}" for key, value in values.items()),
    ])
    if cache_policy_mixed:
        command.append("cache-resource-inventory")
    return subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True,
        check=False, timeout=15,
    )


def _rows(result: subprocess.CompletedProcess[str]) -> list[dict[str, object]]:
    try:
        return [cast(dict[str, object], json.loads(line)) for line in result.stdout.splitlines()]
    except json.JSONDecodeError as error:
        pytest.fail(f"Native cache stdout={result.stdout!r}; stderr={result.stderr!r}; {error}")


@pytest.mark.parametrize("cache_policy_mode", ["inventory", "plan", "apply"])
@pytest.mark.parametrize("cache_policy_root", ["library-caches", "containers"])
def test_prohibited_home_roots_refuse_before_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_policy_mode: str,
    cache_policy_root: str,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setenv("HOME", str(fixture.home))
    suffix = ("Library", "Caches") if cache_policy_root == "library-caches" else (".local", "share", "containers")
    root = fixture.home.joinpath(*suffix)
    child = root / "keep"
    child.mkdir(parents=True)
    payload = child / "retained.txt"
    payload.write_text("retained", encoding="utf-8")

    def refuse_measurement(_path: Path, *, timeout: float = 30.0) -> int:
        raise AssertionError("prohibited root reached native measurement")

    monkeypatch.setattr(cache_manager, "_allocated_bytes", refuse_measurement)
    with pytest.raises(cache_manager.CacheResourceError, match="root is not allowlisted"):
        if cache_policy_mode == "inventory":
            cache_manager.inventory_cache_children(root, limit=20)
        else:
            cache_manager.remove_cache_child(root, child, apply=cache_policy_mode == "apply")
    assert payload.read_text(encoding="utf-8") == "retained"


@pytest.mark.parametrize("cache_policy_apply", [False, True])
def test_gate_evidence_root_has_no_generic_cache_removal_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_policy_apply: bool,
) -> None:
    root = tmp_path / ".gate-logs"
    retained = root / "observed" / "failed-run.json"
    retained.parent.mkdir(parents=True)
    retained.write_text('{"state":"failed"}', encoding="utf-8")
    monkeypatch.setattr(cache_manager, "_project_gate_logs_root", lambda: root)
    with pytest.raises(cache_manager.CacheResourceError, match="root is not allowlisted for removal"):
        cache_manager.remove_cache_child(root, retained.parent, apply=cache_policy_apply)
    assert retained.read_text(encoding="utf-8") == '{"state":"failed"}'
    assert cache_manager.inventory_cache_children(root, limit=20)[0].path == retained.parent


@pytest.mark.parametrize("cache_policy_environment_overrides", [False, True])
@pytest.mark.parametrize("cache_policy_goal", ["cache-resource-inventory", "cache-disk", "disk-user-caches"])
def test_default_and_compatibility_inventory_use_one_bounded_native_owner(
    tmp_path: Path, cache_policy_environment_overrides: bool, cache_policy_goal: str,
) -> None:
    fixture = _fixture(tmp_path)
    values = {"CACHE_RESOURCE_CANDIDATE": str(fixture.child)}
    result = _make(
        tmp_path, fixture, cache_policy_goal, values,
        cache_policy_environment_overrides=cache_policy_environment_overrides,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = _rows(result)
    assert rows[0]["path"] == str(fixture.child)
    allocated_bytes = rows[0]["allocated_bytes"]
    assert isinstance(allocated_bytes, int)
    assert allocated_bytes > 0
    assert rows[-1] == {"entries": 1, "status": "complete"}
    assert result.stderr.count("cache inventory start") == 1
    assert "max_seconds=30 max_entries=1000" in result.stderr
    assert not (tmp_path / "UV_TRIPWIRE").exists()
    assert (fixture.child / "payload.bin").is_file()


@pytest.mark.parametrize("cache_policy_environment_overrides", [False, True])
@pytest.mark.parametrize("cache_policy_field", PUBLIC)
@pytest.mark.parametrize("cache_policy_payload", ["make-function", "shell-substitution", "backticks"])
def test_cache_inputs_remain_literal_before_validation_and_native_io(
    tmp_path: Path, cache_policy_environment_overrides: bool, cache_policy_field: str,
    cache_policy_payload: str,
) -> None:
    fixture = _fixture(tmp_path)
    marker = tmp_path / "PUBLIC_TRIPWIRE"
    payload = {
        "make-function": "$(shell touch " + str(marker) + ")",
        "shell-substitution": "$(touch " + str(marker) + ")",
        "backticks": "`touch " + str(marker) + "`",
    }[cache_policy_payload]
    values = {**_values(fixture), cache_policy_field: payload}
    goal = "cache-resource-remove" if cache_policy_field == "CACHE_RESOURCE_CANDIDATE" else "cache-resource-inventory"
    result = _make(
        tmp_path, fixture, goal, values,
        cache_policy_environment_overrides=cache_policy_environment_overrides,
    )
    assert not marker.exists(), "cache path or flag executed diagnostic data"
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (tmp_path / "UV_TRIPWIRE").exists()
    assert (fixture.child / "payload.bin").is_file()


@pytest.mark.parametrize("cache_policy_environment_overrides", [False, True])
@pytest.mark.parametrize("cache_policy_private_origin", ["environment", "command-line"])
def test_private_cache_carriers_cannot_override_public_plan_inputs(
    tmp_path: Path, cache_policy_environment_overrides: bool, cache_policy_private_origin: str,
) -> None:
    fixture = _fixture(tmp_path)
    result = _make(
        tmp_path, fixture, "cache-resource-remove", _values(fixture),
        cache_policy_environment_overrides=cache_policy_environment_overrides,
        cache_policy_private_origin=cache_policy_private_origin,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _rows(result) == [{"candidate": str(fixture.child), "removed": False, "status": "complete"}]
    assert not (tmp_path / "PRIVATE_TRIPWIRE").exists()
    assert not (tmp_path / "UV_TRIPWIRE").exists()
    assert (fixture.child / "payload.bin").is_file()


@pytest.mark.parametrize("cache_policy_goal", ["cache-clean", "clean-gh-run-logs", "clean-all-caches"])
def test_retired_bulk_cleanup_has_no_executable_or_phony_entrypoint(
    tmp_path: Path, cache_policy_goal: str,
) -> None:
    # Never invoke the old destructive recipe, even with a synthetic home.
    makefile = compose_makefile(ROOT / "Makefile")
    assert f"\n{cache_policy_goal}:" not in makefile
    fixture = _fixture(tmp_path)
    result = _make(tmp_path, fixture, cache_policy_goal, _values(fixture))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "No rule to make target" in result.stderr
    assert not (tmp_path / "UV_TRIPWIRE").exists()
    assert (fixture.child / "payload.bin").is_file()


@pytest.mark.parametrize("cache_policy_environment_overrides", [False, True])
@pytest.mark.parametrize("cache_policy_goal", ["cache-disk", "disk-user-caches"])
@pytest.mark.parametrize("cache_policy_mixed", [False, True])
def test_inventory_alias_preserves_late_native_target_context(
    tmp_path: Path, cache_policy_environment_overrides: bool, cache_policy_goal: str,
    cache_policy_mixed: bool,
) -> None:
    fixture = _fixture(tmp_path)
    specific = fixture.home / "tmp"
    child = specific / "gludd-specific"
    child.mkdir(parents=True)
    (child / "payload.bin").write_bytes(b"s" * 4096)
    result = _make(
        tmp_path, fixture, cache_policy_goal, _values(fixture),
        cache_policy_environment_overrides=cache_policy_environment_overrides,
        cache_policy_specific_root=specific, cache_policy_mixed=cache_policy_mixed,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = _rows(result)
    assert rows[0]["path"] == str(child)
    assert rows[-1] == {"entries": 1, "status": "complete"}
    assert result.stderr.count("cache inventory start") == 1
    assert not (tmp_path / "UV_TRIPWIRE").exists()


def test_fixture_representation_never_includes_its_environment(tmp_path: Path) -> None:
    fixture = CachePolicyFixture(
        tmp_path, tmp_path, tmp_path,
        {"PRIVATE_FIXTURE_ONLY": "fixture-only-sensitive-value"}, "fixture-uv",
    )
    assert "fixture-only-sensitive-value" not in repr(fixture)
    assert "PRIVATE_FIXTURE_ONLY" not in repr(fixture)


@pytest.mark.parametrize("cache_policy_apply", [False, True])
@pytest.mark.parametrize("cache_policy_managed_name", [
    "uv", "UV", "gh", "opencode", "huggingface", "pip", "pre-commit",
    "claude", "gludd", "general-ludd",
])
def test_managed_cache_namespace_requires_its_lifecycle_before_plan_or_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    cache_policy_apply: bool, cache_policy_managed_name: str,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setenv("HOME", str(fixture.home))
    managed = fixture.root / cache_policy_managed_name
    managed.mkdir()
    retained = managed / "retained.bin"
    retained.write_bytes(b"managed fixture remains intact")
    with pytest.raises(
        cache_manager.CacheResourceError,
        match="managed cache child requires its lifecycle owner",
    ):
        cache_manager.remove_cache_child(fixture.root, managed, apply=cache_policy_apply)
    assert retained.read_bytes() == b"managed fixture remains intact"
    assert (fixture.child / "payload.bin").read_bytes() == b"x" * 4096


@pytest.mark.parametrize("cache_policy_apply", [False, True])
def test_managed_name_guard_preserves_exact_unknown_prefix_child_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_policy_apply: bool,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setenv("HOME", str(fixture.home))
    unknown = fixture.root / "uv-extra"
    unknown.mkdir()
    payload = unknown / "disposable.bin"
    payload.write_bytes(b"explicit disposable fixture")
    result = cache_manager.remove_cache_child(fixture.root, unknown, apply=cache_policy_apply)
    assert result is cache_policy_apply
    if cache_policy_apply:
        assert not unknown.exists()
    else:
        assert payload.read_bytes() == b"explicit disposable fixture"
    assert (fixture.child / "payload.bin").read_bytes() == b"x" * 4096


@pytest.mark.parametrize("cache_policy_apply", [False, True])
def test_managed_cache_cli_reports_lifecycle_refusal_without_complete_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    cache_policy_apply: bool,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setenv("HOME", str(fixture.home))
    managed = fixture.root / "uv"
    managed.mkdir()
    retained = managed / "retained.bin"
    retained.write_bytes(b"managed CLI fixture")
    arguments = ["remove", "--root", str(fixture.root), "--candidate", str(managed)]
    if cache_policy_apply:
        arguments.append("--apply")
    assert cache_manager.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "managed cache child requires its lifecycle owner: uv" in captured.err
    assert "native uv cache" in captured.err
    assert retained.read_bytes() == b"managed CLI fixture"
    assert (fixture.child / "payload.bin").read_bytes() == b"x" * 4096


@pytest.mark.parametrize("cache_policy_environment_overrides", [False, True])
def test_native_make_apply_removes_only_the_explicit_unknown_child(
    tmp_path: Path, cache_policy_environment_overrides: bool,
) -> None:
    fixture = _fixture(tmp_path)
    sibling = fixture.root / "uv"
    sibling.mkdir()
    retained = sibling / "retained.bin"
    retained.write_bytes(b"managed sibling remains intact")
    values = {**_values(fixture), "CACHE_RESOURCE_VALIDATE_ONLY": "0"}
    result = _make(
        tmp_path, fixture, "cache-resource-remove", values,
        cache_policy_environment_overrides=cache_policy_environment_overrides,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _rows(result) == [{"candidate": str(fixture.child), "removed": True, "status": "complete"}]
    assert not fixture.child.exists()
    assert retained.read_bytes() == b"managed sibling remains intact"
    assert not (tmp_path / "UV_TRIPWIRE").exists()


@pytest.mark.parametrize("cache_policy_environment_overrides", [False, True])
@pytest.mark.parametrize("cache_policy_validate", ["0", "1"])
def test_native_make_managed_refusal_preserves_both_modes_and_payload(
    tmp_path: Path, cache_policy_environment_overrides: bool, cache_policy_validate: str,
) -> None:
    fixture = _fixture(tmp_path)
    managed = fixture.root / "uv"
    managed.mkdir()
    retained = managed / "retained.bin"
    retained.write_bytes(b"native managed fixture remains intact")
    values = {
        **_values(fixture), "CACHE_RESOURCE_CANDIDATE": str(managed),
        "CACHE_RESOURCE_VALIDATE_ONLY": cache_policy_validate,
    }
    result = _make(
        tmp_path, fixture, "cache-resource-remove", values,
        cache_policy_environment_overrides=cache_policy_environment_overrides,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert "managed cache child requires its lifecycle owner: uv" in result.stderr
    assert "native uv cache" in result.stderr
    assert "Error 2" in result.stderr
    assert retained.read_bytes() == b"native managed fixture remains intact"
    assert (fixture.child / "payload.bin").read_bytes() == b"x" * 4096
    assert not (tmp_path / "UV_TRIPWIRE").exists()


@pytest.mark.parametrize("cache_policy_goal", ["cache-resource-inventory", "cache-resource-remove"])
@pytest.mark.parametrize("cache_policy_child_status", [7, 124, 143])
def test_native_make_keeps_child_failure_status_and_stderr(
    tmp_path: Path, cache_policy_goal: str, cache_policy_child_status: int,
) -> None:
    fixture = _fixture(tmp_path)
    child = tmp_path / "status-child.py"
    child.write_text(
        "import sys\n"
        "print('native cache child failed', file=sys.stderr, flush=True)\n"
        f"raise SystemExit({cache_policy_child_status})\n",
        encoding="utf-8",
    )
    result = _make(
        tmp_path, fixture, cache_policy_goal, _values(fixture),
        cache_policy_system_python=shlex.join([sys.executable, str(child)]),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert "native cache child failed" in result.stderr
    assert f"Error {cache_policy_child_status}" in result.stderr
    assert (fixture.child / "payload.bin").read_bytes() == b"x" * 4096
    assert not (tmp_path / "UV_TRIPWIRE").exists()


def _shared_uv_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CachePolicyFixture:
    """Substitute only the private root after binding the real production owner."""
    fixture = _fixture(tmp_path)
    root = tmp_path / "shared-uv" / "gludd-uv-cache-public-v2"
    child = root / "archive-v0"
    child.mkdir(parents=True)
    payload = child / "payload.bin"
    payload.write_bytes(b"shared UV fixture remains intact")
    payload.chmod(0o640)
    monkeypatch.setenv("HOME", str(fixture.home))
    monkeypatch.setattr(cache_manager, "_SHARED_UV_CACHE_INVENTORY_ROOT", root, raising=False)
    return CachePolicyFixture(fixture.home, root, child, fixture.environment, fixture.uv)


def _assert_shared_uv_payload(payload: Path) -> None:
    assert payload.read_bytes() == b"shared UV fixture remains intact"
    assert payload.stat().st_mode & 0o777 == 0o640


def _refuse_shared_uv_measurement(path: Path, *, timeout: float = 30.0) -> int:
    raise AssertionError(f"unadmitted shared UV root reached measurement: {path}, {timeout}")


def _assert_shared_uv_constructor_keeps_symlink_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the real constant construction with only its parent confined."""
    parent = tmp_path / "native-cache-parent"
    parent.mkdir()
    target = tmp_path / "native-cache-outside-target"
    target.mkdir()
    retained = target / "keep.bin"
    retained.write_bytes(b"constructor target remains intact")
    retained.chmod(0o640)
    leaf = parent / "gludd-uv-cache-public-v2"
    leaf.symlink_to(target, target_is_directory=True)

    def confined_path(value: str) -> Path:
        assert value == "/tmp", "constructor requested an unexpected path"
        return parent

    pathlib_proxy = ModuleType("pathlib")
    vars(pathlib_proxy)["Path"] = confined_path
    spec = importlib.util.spec_from_file_location(
        "shared_uv_constructor_owner", ROOT / "scripts/cache_resource_manager.py",
    )
    assert spec is not None and spec.loader is not None
    owner = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as isolated:
        isolated.setitem(sys.modules, spec.name, owner)
        isolated.setitem(sys.modules, "pathlib", pathlib_proxy)
        spec.loader.exec_module(owner)
    observed = getattr(owner, "_SHARED_UV_CACHE_INVENTORY_ROOT", None)
    assert observed == leaf
    assert observed != target
    assert leaf.is_symlink()
    assert retained.read_bytes() == b"constructor target remains intact"
    assert retained.stat().st_mode & 0o777 == 0o640


def test_shared_uv_inventory_root_matches_native_default_without_resolving_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.environment.pop("GLUDD_UV_CACHE_DIR", None)
    result = _make(tmp_path, fixture, "uv-cache-path", _values(fixture))
    assert result.returncode == 0, result.stdout + result.stderr
    selected = Path(result.stdout.strip())
    assert str(selected) == "/tmp/gludd-uv-cache-public-v2"
    expected = selected.parent.resolve(strict=False) / selected.name
    assert getattr(cache_manager, "_SHARED_UV_CACHE_INVENTORY_ROOT", None) == expected
    assert expected not in cache_manager._removable_roots()
    assert not (tmp_path / "UV_TRIPWIRE").exists()
    _assert_shared_uv_constructor_keeps_symlink_leaf(tmp_path, monkeypatch)


def test_shared_uv_inventory_reports_complete_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _shared_uv_fixture(tmp_path, monkeypatch)
    assert cache_manager.main(["inventory", "--root", str(fixture.root), "--limit", "100"]) == 0
    captured = capsys.readouterr()
    rows = [cast(dict[str, object], json.loads(line)) for line in captured.out.splitlines()]
    assert len(rows) == 2
    assert rows[0]["path"] == str(fixture.child)
    allocated = rows[0]["allocated_bytes"]
    assert isinstance(allocated, int) and allocated > 0
    assert rows[0]["status"] == "measured" and rows[0]["error"] is None
    assert rows[1] == {"entries": 1, "status": "complete"}
    assert "max_seconds=30 max_entries=1000" in captured.err
    assert "complete inspected=1 errors=0" in captured.err
    _assert_shared_uv_payload(fixture.child / "payload.bin")


@pytest.mark.parametrize("shared_uv_escape", ["parent", "sibling", "descendant", "traversal", "override"])
def test_shared_uv_inventory_refuses_every_other_root_before_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    shared_uv_escape: str,
) -> None:
    fixture = _shared_uv_fixture(tmp_path, monkeypatch)
    roots = {
        "parent": fixture.root.parent,
        "sibling": fixture.root.with_name("gludd-uv-cache-public-v2-other"),
        "descendant": fixture.child,
        "traversal": fixture.root / "..",
        "override": tmp_path / "selected-arbitrary-cache",
    }
    selected = roots[shared_uv_escape]
    selected.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GLUDD_UV_CACHE_DIR", str(selected))
    monkeypatch.setattr(cache_manager, "_allocated_bytes", _refuse_shared_uv_measurement)
    assert cache_manager.main(["inventory", "--root", str(selected), "--limit", "100"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "root is not allowlisted" in captured.err
    _assert_shared_uv_payload(fixture.child / "payload.bin")


@pytest.mark.parametrize("shared_uv_spelling", ["leaf", "target"])
def test_shared_uv_inventory_never_admits_a_symlink_or_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    shared_uv_spelling: str,
) -> None:
    fixture = _shared_uv_fixture(tmp_path, monkeypatch)
    original = tmp_path / "original-shared-cache"
    fixture.root.rename(original)
    target = tmp_path / "otherwise-prohibited-target"
    target.mkdir()
    retained = target / "keep.bin"
    retained.write_bytes(b"outside target remains intact")
    retained.chmod(0o640)
    fixture.root.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(cache_manager, "_allocated_bytes", _refuse_shared_uv_measurement)
    selected = fixture.root if shared_uv_spelling == "leaf" else target
    assert cache_manager.main(["inventory", "--root", str(selected), "--limit", "100"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    refusal = "root must not be a symlink" if shared_uv_spelling == "leaf" else "root is not allowlisted"
    assert refusal in captured.err
    assert retained.read_bytes() == b"outside target remains intact"
    assert retained.stat().st_mode & 0o777 == 0o640
    _assert_shared_uv_payload(original / fixture.child.name / "payload.bin")


@pytest.mark.parametrize("shared_uv_apply", [False, True])
def test_shared_uv_cache_has_no_generic_removal_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    shared_uv_apply: bool,
) -> None:
    fixture = _shared_uv_fixture(tmp_path, monkeypatch)
    arguments = ["remove", "--root", str(fixture.root), "--candidate", str(fixture.child)]
    if shared_uv_apply:
        arguments.append("--apply")
    assert cache_manager.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "root is not allowlisted for removal" in captured.err
    _assert_shared_uv_payload(fixture.child / "payload.bin")


@pytest.mark.parametrize("shared_uv_unavailable", ["absent", "file"])
def test_shared_uv_inventory_unavailable_root_stays_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    shared_uv_unavailable: str,
) -> None:
    fixture = _shared_uv_fixture(tmp_path, monkeypatch)
    original = tmp_path / "retained-shared-cache"
    fixture.root.rename(original)
    if shared_uv_unavailable == "file":
        fixture.root.write_text("not a cache directory", encoding="utf-8")
    monkeypatch.setattr(cache_manager, "_allocated_bytes", _refuse_shared_uv_measurement)
    assert cache_manager.main(["inventory", "--root", str(fixture.root), "--limit", "100"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "root is not a directory" in captured.err
    _assert_shared_uv_payload(original / fixture.child.name / "payload.bin")


def test_shared_uv_inventory_empty_existing_root_is_distinct_from_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _shared_uv_fixture(tmp_path, monkeypatch)
    original = tmp_path / "retained-shared-payload"
    fixture.child.rename(original)
    assert cache_manager.main(["inventory", "--root", str(fixture.root), "--limit", "100"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"entries": 0, "status": "complete"}
    assert "complete inspected=0 errors=0" in captured.err
    _assert_shared_uv_payload(original / "payload.bin")


@pytest.mark.parametrize("cache_policy_environment_overrides", [False, True])
@pytest.mark.parametrize("cache_policy_goal", ["cache-resource-inventory", "cache-disk", "disk-user-caches"])
def test_native_make_shared_uv_inventory_uses_real_owner_and_preserves_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_policy_environment_overrides: bool,
    cache_policy_goal: str,
) -> None:
    fixture = _shared_uv_fixture(tmp_path, monkeypatch)
    bridge = tmp_path / "shared-uv-binding.py"
    bridge.write_text(
        "import importlib.util\nimport sys\nfrom pathlib import Path\n"
        f"assert Path(sys.argv[1]).resolve() == Path({str(ROOT / 'scripts/cache_resource_manager.py')!r})\n"
        "spec = importlib.util.spec_from_file_location('shared_uv_native_owner', sys.argv[1])\n"
        "assert spec is not None and spec.loader is not None\n"
        "owner = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = owner\n"
        "spec.loader.exec_module(owner)\n"
        f"owner._SHARED_UV_CACHE_INVENTORY_ROOT = Path({str(fixture.root)!r})\n"
        "raise SystemExit(owner.main(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    result = _make(
        tmp_path, fixture, cache_policy_goal, _values(fixture),
        cache_policy_environment_overrides=cache_policy_environment_overrides,
        cache_policy_system_python=shlex.join([sys.executable, str(bridge)]),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = _rows(result)
    assert rows[0]["path"] == str(fixture.child)
    assert rows[-1] == {"entries": 1, "status": "complete"}
    assert result.stderr.count("cache inventory start") == 1
    assert "max_seconds=30 max_entries=1000" in result.stderr
    assert not (tmp_path / "UV_TRIPWIRE").exists()
    _assert_shared_uv_payload(fixture.child / "payload.bin")
