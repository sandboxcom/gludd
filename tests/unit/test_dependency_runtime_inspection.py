"""Bounded read-only inspection of registered dependency-profile runtimes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import active_work_status
    import ci_batch_receipts
    import dependency_profiles as owner
    import resource_arbiter
else:
    from scripts import active_work_status, ci_batch_receipts, resource_arbiter
    from scripts import dependency_profiles as owner
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
VERSION = ".".join(map(str, sys.version_info[:3]))


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[owner.ProfileCatalog, Path]:
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="0"\ndependencies=["pytest>=8"]\n'
    )
    (root / "uv.lock").write_text(
        'version=1\n[[package]]\nname="pytest"\nversion="9.1.1"\n'
    )
    (root / "config").mkdir()
    (root / "config/dependency_profiles.toml").write_text(
        'schema-version=1\nmax-lock-lines=2499\nprofiles={}\n[sets.ci]\nprofiles=[]\n'
    )
    monkeypatch.setattr(active_work_status, "_repository_roots", lambda: (root,))
    monkeypatch.setattr(resource_arbiter, "resource_root", lambda selected: tmp_path / "owned")
    environment = tmp_path / "owned-toolchain/ci-shards" / f"python-{sys.version_info.major}.{sys.version_info.minor}"
    (environment / "bin").mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    probe = environment / "bin/python"
    probe.write_text(
        f"#!{sys.executable}\n"
        "import json, os\nfrom pathlib import Path\n"
        "Path(os.environ['PROBE_TRIPWIRE']).write_text('executed')\n"
        "print(os.environ['PROBE_OUTPUT'])\n"
    )
    probe.chmod(0o700)
    monkeypatch.setenv("PROBE_TRIPWIRE", str(tmp_path / "tripwire"))
    monkeypatch.setenv("PROBE_OUTPUT", json.dumps({
        "version": VERSION, "implementation": "CPython",
        "prefix": str(environment), "base_prefix": str(tmp_path / "base"),
        "executable": str(probe), "mismatches": [],
        "plugins": {"inventory_kind": "installed-pytest11-v1"},
        "installed_distributions_sha256": "a" * 64,
    }))
    return owner.load_catalog(root), environment


def inspect(runtime: tuple[owner.ProfileCatalog, Path], *, plan: bool = False) -> dict[str, object]:
    catalog, environment = runtime
    return owner.inspect_runtime(
        catalog, profile_set="ci", environment=environment,
        python=VERSION, validate_only=plan,
    )


def test_registered_runtime_inspection_is_bounded_readonly_and_metadata_only(
    runtime: tuple[owner.ProfileCatalog, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = dict(os.environ)
    catalog, environment = runtime
    snapshots = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    plan = inspect(runtime, plan=True)
    assert plan["state"] == "plan" and not (tmp_path / "tripwire").exists()
    calls: list[tuple[list[str], dict[str, object]]] = []
    native_run = subprocess.run

    def record(
        argv: list[str], *, check: bool, capture_output: bool, text: bool,
        input: str, cwd: Path, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((argv, dict(check=check, capture_output=capture_output, text=text,
                                input=input, cwd=cwd, timeout=timeout)))
        assert text is True
        return native_run(argv, check=check, capture_output=capture_output, text=True,
                          input=input, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", record)
    result = inspect(runtime)
    assert result["state"] == "verified"
    assert result["environment"] == str(environment)
    assert result["python"] == VERSION and result["profile_set"] == "ci"
    metadata = result["runtime"]
    assert isinstance(metadata, dict)
    plugins = metadata["plugins"]
    assert isinstance(plugins, dict)
    assert plugins["inventory_kind"] == "installed-pytest11-v1"
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv[:4] == [str(environment / "bin/python"), "-I", "-B", "-c"]
    assert options["timeout"] == 20 and options["check"] is True
    assert options["cwd"] == catalog.root
    payload = json.loads(str(options["input"]))
    assert payload["requirements"] == ["pytest>=8"]
    assert payload["locked_versions"]["pytest"] == ["9.1.1"]
    assert dict(os.environ) == before
    assert {p: p.read_bytes() for p in snapshots} == snapshots
    assert not list(tmp_path.rglob("__pycache__"))
    assert not list(tmp_path.rglob("*.pyc"))


@pytest.mark.parametrize("kind", [
    "missing", "foreign", "traversal", "symlink", "parent-symlink", "cfg-symlink", "system-site",
    "invalid-version", "unknown-profile",
])
def test_runtime_inspection_refuses_before_executing_unknown_paths(
    runtime: tuple[owner.ProfileCatalog, Path], tmp_path: Path, kind: str,
) -> None:
    catalog, environment = runtime
    python, profile = VERSION, "ci"
    if kind == "missing":
        (environment / "pyvenv.cfg").unlink()
    elif kind == "foreign":
        environment = tmp_path / "foreign"
    elif kind == "traversal":
        environment = environment / ".." / environment.name
    elif kind == "symlink":
        other = environment.with_name("relocated")
        environment.rename(other)
        environment.symlink_to(other, target_is_directory=True)
    elif kind == "parent-symlink":
        parent = environment.parent
        other = parent.with_name("relocated")
        parent.rename(other)
        parent.symlink_to(other, target_is_directory=True)
    elif kind == "cfg-symlink":
        cfg = environment / "pyvenv.cfg"
        copy = environment / "other.cfg"
        cfg.rename(copy)
        cfg.symlink_to(copy)
    elif kind == "system-site":
        (environment / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
    elif kind == "invalid-version":
        python = "3.12"
    else:
        profile = "unknown"
    with pytest.raises(owner.ProfileError):
        owner.inspect_runtime(catalog, profile_set=profile, environment=environment, python=python)
    assert not (tmp_path / "tripwire").exists()


@pytest.mark.parametrize(("key", "value"), [
    ("version", "3.12.0"), ("prefix", "/foreign"), ("base_prefix", "SAME"),
    ("executable", "/foreign/bin/python"), ("mismatches", ["pytest: lock mismatch"]),
    ("plugins", None), ("installed_distributions_sha256", "bad"),
])
def test_runtime_metadata_mismatch_cannot_be_verified(
    runtime: tuple[owner.ProfileCatalog, Path], monkeypatch: pytest.MonkeyPatch, key: str, value: object,
) -> None:
    payload = json.loads(os.environ["PROBE_OUTPUT"])
    payload[key] = str(runtime[1]) if value == "SAME" else value
    monkeypatch.setenv("PROBE_OUTPUT", json.dumps(payload))
    with pytest.raises(owner.ProfileError) as refused:
        inspect(runtime)
    assert "profile_set=\'ci\'" in str(refused.value)
    assert "input_sha256=" in str(refused.value) and "uv.lock" in str(refused.value)


@pytest.mark.parametrize(
    "output", ["not-json", "[]", "{}", "{}\n{}", "x" * 131073],
    ids=["text", "array", "empty", "multi", "oversized"],
)
def test_malformed_probe_output_refuses(
    runtime: tuple[owner.ProfileCatalog, Path], monkeypatch: pytest.MonkeyPatch, output: str,
) -> None:
    monkeypatch.setenv("PROBE_OUTPUT", output)
    with pytest.raises(owner.ProfileError):
        inspect(runtime)


@pytest.mark.parametrize("failure", [
    OSError("unavailable"), subprocess.TimeoutExpired("probe", 20),
    subprocess.CalledProcessError(17, "probe"),
])
def test_unavailable_or_timed_out_probe_has_no_fallback(
    runtime: tuple[owner.ProfileCatalog, Path], monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise failure
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(owner.ProfileError):
        inspect(runtime)


def test_profile_change_during_probe_refuses(
    runtime: tuple[owner.ProfileCatalog, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_run = subprocess.run
    def change(
        argv: list[str], *, check: bool, capture_output: bool, text: bool,
        input: str, cwd: Path, timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert text is True
        result = native_run(argv, check=check, capture_output=capture_output, text=True,
                            input=input, cwd=cwd, timeout=timeout)
        (runtime[0].root / "uv.lock").write_text("changed")
        return result
    monkeypatch.setattr(subprocess, "run", change)
    with pytest.raises(owner.ProfileError, match="changed"):
        inspect(runtime)


def test_inspection_target_is_registered_safe_and_transports_literal_values(
    tmp_path: Path,
) -> None:
    target = "inspect-dependency-runtime"
    composed = compose_makefile(ROOT / "Makefile")
    assert target + ":" in composed
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text())
    row = next(row for row in contract["targets"] if row["name"] == target)
    assert row["make_variables"] == [
        "DEPENDENCY_RUNTIME_ENVIRONMENT", "DEPENDENCY_RUNTIME_PYTHON",
        "DEPENDENCY_RUNTIME_SET", "DEPENDENCY_RUNTIME_VALIDATE_ONLY",
    ]
    assert row["behavior"].endswith(
        "DEPENDENCY_RUNTIME_SET=ci DEPENDENCY_RUNTIME_VALIDATE_ONLY=1"
    )
    tripwire = tmp_path / "literal-must-not-execute"
    for name in row["make_variables"]:
        result = subprocess.run(
            ["make", "--no-print-directory", target, "UV=echo", f"{name}=$(shell touch {tripwire})"],
            cwd=ROOT, capture_output=True, text=True, timeout=5, check=False,
        )
        assert result.returncode != 0
        assert not tripwire.exists()

@pytest.mark.parametrize("kind", ["matching", "profile", "lock", "absent", "inactive-marker"])
def test_native_metadata_helpers_never_load_entrypoints(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    import importlib.metadata
    import io

    versions = {
        distribution.metadata["Name"].lower().replace("_", "-"): [distribution.version]
        for distribution in importlib.metadata.distributions()
    }
    requirement = "pytest>=8"
    if kind == "profile":
        requirement = "pytest>=999"
    elif kind == "lock":
        versions["pytest"] = ["0"]
    elif kind == "absent":
        requirement = "gludd-inspection-absent-fixture>=1"
    elif kind == "inactive-marker":
        requirement = 'gludd-inspection-absent-fixture>=1; python_version < "1"'
    def refuse_load(point: importlib.metadata.EntryPoint) -> None:
        raise AssertionError("metadata inspection must not load plugins")
    monkeypatch.setattr(importlib.metadata.EntryPoint, "load", refuse_load)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "requirements": [requirement], "locked_versions": versions,
    })))
    result = owner.runtime_metadata()
    assert result["version"] == VERSION and result["prefix"] == sys.prefix
    mismatches = result["mismatches"]
    assert isinstance(mismatches, list)
    assert bool(mismatches) is (kind in {"profile", "lock", "absent"})
    assert len(str(result["installed_distributions_sha256"])) == 64


def test_inspection_cli_requires_exact_inputs_and_reports_missing_runtime(
    runtime: tuple[owner.ProfileCatalog, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    catalog, environment = runtime
    args = ["inspect", "--root", str(catalog.root)]
    assert owner.main(args) == 1
    assert owner.main([*args, "--set", "ci", "--python", VERSION,
                       "--environment", str(environment), "--validate-only"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "plan"
    assert owner.main([*args, "--set", "ci", "--python", VERSION,
                       "--environment", str(environment)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "verified"

def test_inspection_parent_also_disables_bytecode_writes() -> None:
    result = subprocess.run(
        ["make", "--no-print-directory", "inspect-dependency-runtime", "UV=echo",
         "DEPENDENCY_RUNTIME_ENVIRONMENT=", "DEPENDENCY_RUNTIME_PYTHON=3.12.15",
         "DEPENDENCY_RUNTIME_SET=ci", "DEPENDENCY_RUNTIME_VALIDATE_ONLY=1"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "run python -B scripts/dependency_profiles.py inspect" in result.stdout

@pytest.mark.parametrize("text", [
    "include-system-site-packages = false\nInclude-System-Site-Packages = true\n",
    "include-system-site-packages = false\ninclude-system-site-packages = true\n",
    "include-system-site-packages = true\ninclude-system-site-packages = false\n",
    "include-system-site-packages = false\ninclude-system-site-packages = false\n",
], ids=["mixed-case-last-true", "last-true", "last-false", "duplicate-false"])
def test_effective_runtime_config_refuses_duplicate_normalized_site_keys(
    runtime: tuple[owner.ProfileCatalog, Path], tmp_path: Path, text: str,
) -> None:
    (runtime[1] / "pyvenv.cfg").write_text(text)
    with pytest.raises(owner.ProfileError):
        inspect(runtime)
    assert not (tmp_path / "tripwire").exists()


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling"])
def test_effective_runtime_config_refuses_executable_directory_shadow(
    runtime: tuple[owner.ProfileCatalog, Path], tmp_path: Path, kind: str,
) -> None:
    shadow = runtime[1] / "bin/pyvenv.cfg"
    if kind == "file":
        shadow.write_text("include-system-site-packages = true\n")
    else:
        target = tmp_path / "shadow.cfg"
        if kind == "symlink":
            target.write_text("include-system-site-packages = true\n")
        shadow.symlink_to(target)
    with pytest.raises(owner.ProfileError):
        inspect(runtime)
    assert not (tmp_path / "tripwire").exists()


@pytest.mark.parametrize("kind", ["manifest", "lock", "project", "cfg"])
def test_runtime_input_parse_and_hash_use_identical_frozen_bytes(
    runtime: tuple[owner.ProfileCatalog, Path], monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    catalog, environment = runtime
    target = {
        "manifest": catalog.manifest, "lock": catalog.root / "uv.lock",
        "project": catalog.root / "pyproject.toml", "cfg": environment / "pyvenv.cfg",
    }[kind]
    before = target.read_text()
    replacements = {
        "manifest": before + "\n# mutation after catalog selection\n",
        "lock": before.replace('version="9.1.1"', 'version="0"'),
        "project": before + "\n[project.optional-dependencies]\ninvalid=[]\n",
        "cfg": "include-system-site-packages = true\n",
    }
    native_hash = ci_batch_receipts.file_sha256
    changed = False

    def change_before_first_hash(path: Path) -> str:
        nonlocal changed
        if not changed and (path == target or kind == "cfg"):
            target.write_text(replacements[kind])
            changed = True
        return native_hash(path)

    monkeypatch.setattr(ci_batch_receipts, "file_sha256", change_before_first_hash)
    with pytest.raises(owner.ProfileError, match="changed"):
        inspect(runtime)
    assert changed


def test_runtime_inspection_reparses_current_manifest_before_using_catalog_selection(
    runtime: tuple[owner.ProfileCatalog, Path], tmp_path: Path,
) -> None:
    runtime[0].manifest.write_text(
        'schema-version=1\nmax-lock-lines=2499\nprofiles={}\n[sets.ci]\nprofiles=["missing"]\n'
    )
    with pytest.raises(owner.ProfileError, match="unknown profiles"):
        inspect(runtime)
    assert not (tmp_path / "tripwire").exists()


def test_effective_runtime_config_still_allows_native_interpreter_symlinks(
    runtime: tuple[owner.ProfileCatalog, Path],
) -> None:
    executable = runtime[1] / "bin/python"
    actual = executable.with_name("python-native")
    executable.rename(actual)
    executable.symlink_to(actual)
    assert inspect(runtime)["state"] == "verified"
