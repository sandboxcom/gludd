"""Contracts for independent, bounded uv dependency profile projects."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from scripts import dependency_profiles
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "config" / "dependency_profiles.toml"
LEGACY_REQUIREMENTS = (
    ROOT / "tests/fixtures/dependency_profiles/legacy_requirements.toml"
)

EXPECTED_PROFILES = {
    "agent-runtime",
    "ansible-controller",
    "aws",
    "azure",
    "benchmark",
    "cuda-inference",
    "cuda-inference-mcp",
    "dev-ansible",
    "dev-build",
    "dev-quality",
    "dev-science",
    "dev-security",
    "dev-test",
    "decision-codification",
    "gcp",
    "game-e2e",
    "hindsight",
    "local-inference",
    "mysql",
    "networking",
    "observability",
    "platform-sandbox",
    "presentation-test",
    "vmware",
    "xml-security",
}

EXPECTED_INSTALL_SETS = {
    "audit-runtime",
    "build",
    "build-azure",
    "ci",
    "ci-azure",
    "ci-game-e2e",
    "ci-local-inference",
    "core",
    "development",
    "ansible-controller",
    "aws",
    "azure",
    "benchmark",
    "cuda-inference",
    "decision-codification",
    "e2e-all",
    "game-e2e",
    "gcp",
    "hindsight",
    "local-inference",
    "mysql",
    "networking",
    "observability",
    "platform-sandbox",
    "presentation-test",
    "sandbox",
    "sbom",
    "vmware",
    "xml-security",
}


def _load(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_root_project_is_core_only_and_profile_catalog_is_complete() -> None:
    root_project = _load(ROOT / "pyproject.toml")
    project = root_project["project"]
    assert isinstance(project, dict)
    assert "optional-dependencies" not in project
    assert "dependency-groups" not in root_project

    catalog = _load(CATALOG_PATH)
    assert catalog["schema-version"] == 1
    assert catalog["max-lock-lines"] == 2499
    profiles = catalog["profiles"]
    install_sets = catalog["sets"]
    assert isinstance(profiles, dict)
    assert isinstance(install_sets, dict)
    assert set(profiles) == EXPECTED_PROFILES
    assert set(install_sets) == EXPECTED_INSTALL_SETS


def test_root_lock_enforces_current_transitive_security_floors() -> None:
    root_project = _load(ROOT / "pyproject.toml")

    assert root_project["tool"]["uv"]["constraint-dependencies"] == [
        "anyio>=4.14.2",
        "mako>=1.4.2",
        "urllib3>=2.8.0",
    ]
    for profile in ("agent-runtime", "dev-test"):
        profile_project = _load(
            ROOT / "requirements/profiles" / profile / "pyproject.toml"
        )
        assert profile_project["tool"]["uv"]["constraint-dependencies"] == [
            "anyio>=4.14.2,<4.15",
        ]


def test_cuda_profile_uses_current_auditable_runtime_versions() -> None:
    cuda_config = _load(ROOT / "requirements/profiles/cuda-inference/pyproject.toml")
    cuda_project = cuda_config["project"]

    assert cuda_project["dependencies"] == [
        "vllm==0.30.0",
        "torch==2.13.0",
    ]
    assert cuda_config["tool"]["uv"]["exclude-dependencies"] == ["mcp"]
    assert "override-dependencies" not in cuda_config["tool"]["uv"]
    cuda_mcp = _load(
        ROOT / "requirements/profiles/cuda-inference-mcp/pyproject.toml"
    )
    assert cuda_mcp["project"]["dependencies"] == ["mcp==2.3.0"]

    catalog = _load(CATALOG_PATH)
    assert catalog["sets"]["cuda-inference"]["profiles"] == [
        "agent-runtime",
        "cuda-inference",
        "cuda-inference-mcp",
    ]
    assert catalog["profiles"]["cuda-inference"]["audit-ignore"] == [
        "GHSA-h35f-9h28-mq5c",
    ]
    deptry = _load(ROOT / "config/deptry_profiles.toml")
    assert deptry["tool"]["deptry"]["package_module_name_map"]["mcp"] == "mcp"
    assert "mcp" in deptry["tool"]["deptry"]["per_rule_ignores"]["DEP002"]


def test_make_commands_never_implicitly_resync_a_composed_profile_environment() -> None:
    makefile = compose_makefile(ROOT / "Makefile")

    assert "override UV_NO_SYNC := 1" in makefile
    assert "export UV_NO_SYNC" in makefile
    assert (
        '$(UV) run --no-sync python -c "from general_ludd import __version__'
        in makefile
    )
    test_count = makefile.split("\ntest-count:\n", 1)[1].split("\ntest-nodeids:\n", 1)[0]
    assert "$(UV) run --no-sync python scripts/stream_command.py" in test_count
    assert "$(UV) run --no-sync python -m pytest" in test_count


def test_profile_projects_are_independent_and_locks_are_bounded() -> None:
    catalog = _load(CATALOG_PATH)
    profiles = catalog["profiles"]
    assert isinstance(profiles, dict)

    for name, metadata in profiles.items():
        assert isinstance(name, str)
        assert isinstance(metadata, dict)
        project_dir = ROOT / str(metadata["project"])
        pyproject_path = project_dir / "pyproject.toml"
        lock_path = project_dir / "uv.lock"
        pyproject = _load(pyproject_path)

        assert pyproject["project"]["name"] == f"gludd-profile-{name}"
        assert pyproject["tool"]["uv"]["package"] is False
        assert "workspace" not in pyproject["tool"]["uv"]
        assert "general-ludd-agent" not in "\n".join(pyproject["project"]["dependencies"])
        assert len(lock_path.read_text(encoding="utf-8").splitlines()) <= 2499

    security_uv = _load(
        ROOT / "requirements/profiles/dev-security/pyproject.toml"
    )["tool"]["uv"]
    assert security_uv["constraint-dependencies"] == ["chardet>=7.6.0,<8"]


def test_install_sets_reference_only_known_profiles_and_preserve_legacy_names() -> None:
    catalog = _load(CATALOG_PATH)
    profiles = catalog["profiles"]
    install_sets = catalog["sets"]
    assert isinstance(profiles, dict)
    assert isinstance(install_sets, dict)

    for name, metadata in install_sets.items():
        assert isinstance(name, str)
        assert isinstance(metadata, dict)
        selected = metadata["profiles"]
        assert isinstance(selected, list)
        assert len(selected) == len(set(selected))
        assert set(selected) <= set(profiles)

    assert install_sets["sandbox"]["profiles"] == ["agent-runtime"]
    assert install_sets["e2e-all"]["profiles"] == ["agent-runtime", "game-e2e", "azure"]
    assert install_sets["development"]["profiles"] == [
        "agent-runtime",
        "ansible-controller",
        "dev-test",
        "dev-quality",
        "dev-security",
        "dev-build",
        "dev-ansible",
        "dev-science",
    ]
    assert install_sets["ci"]["profiles"] == [
        *install_sets["development"]["profiles"],
        "presentation-test",
    ]


def test_lock_plan_checks_root_and_every_independent_project() -> None:
    catalog = dependency_profiles.load_catalog(ROOT, CATALOG_PATH)
    commands = dependency_profiles.lock_commands(catalog, profile_set=None, check=True)

    assert commands[0].argv == ("uv", "lock", "--check", "--project", str(ROOT))
    assert len(commands) == len(EXPECTED_PROFILES) + 1
    assert {Path(command.argv[-1]).name for command in commands[1:]} == EXPECTED_PROFILES


def test_sync_plan_uses_one_staged_environment_and_locked_inexact_profiles(
    tmp_path: Path,
) -> None:
    catalog = dependency_profiles.load_catalog(ROOT, CATALOG_PATH)
    environment = tmp_path / ".venv"
    staging = tmp_path / ".venv.profile-stage"
    commands = dependency_profiles.sync_commands(
        catalog,
        profile_set="azure",
        environment=environment,
        staging_environment=staging,
        python="3.12",
    )

    assert commands[0].argv == (
        "uv",
        "venv",
        "--relocatable",
        str(staging),
        "--python",
        "3.12",
    )
    assert commands[1].argv == (
        "uv",
        "sync",
        "--project",
        str(ROOT),
        "--locked",
        "--python",
        "3.12",
    )
    assert commands[2].argv == (
        "uv",
        "sync",
        "--project",
        str(ROOT / "requirements/profiles/agent-runtime"),
        "--locked",
        "--inexact",
        "--python",
        "3.12",
    )
    assert commands[3].argv == (
        "uv",
        "sync",
        "--project",
        str(ROOT / "requirements/profiles/azure"),
        "--locked",
        "--inexact",
        "--python",
        "3.12",
    )
    assert commands[0].environment == {}
    assert all(
        command.environment["UV_PROJECT_ENVIRONMENT"] == str(staging)
        for command in commands[1:]
    )
    assert all(
        command.environment["VIRTUAL_ENV"] == str(staging)
        for command in commands[1:]
    )


def test_dependency_only_sync_skips_installing_the_root_project(tmp_path: Path) -> None:
    catalog = dependency_profiles.load_catalog(ROOT, CATALOG_PATH)
    commands = dependency_profiles.sync_commands(
        catalog,
        profile_set="core",
        environment=tmp_path / ".venv",
        staging_environment=tmp_path / ".venv.profile-stage",
        python="3.12",
        install_project=False,
    )

    assert "--no-install-project" in commands[1].argv
    assert all("--no-install-project" not in command.argv for command in commands[2:])


def test_audit_plan_visits_every_locked_project_in_the_selected_set() -> None:
    catalog = dependency_profiles.load_catalog(ROOT, CATALOG_PATH)
    commands = dependency_profiles.audit_commands(
        catalog,
        profile_set="azure",
        ignored_vulnerabilities=("CVE-2025-69872", "PYSEC-2026-3552"),
    )

    assert [Path(command.argv[command.argv.index("--project") + 1]) for command in commands] == [
        ROOT,
        ROOT / "requirements/profiles/agent-runtime",
        ROOT / "requirements/profiles/azure",
    ]
    assert all(
        command.argv[:5] == ("uv", "audit", "--preview-features", "audit", "--locked")
        for command in commands
    )
    assert all(command.argv.count("--ignore") == 2 for command in commands)


def test_audit_plan_scopes_catalog_adjudications_to_their_own_project() -> None:
    catalog = dependency_profiles.load_catalog(ROOT, CATALOG_PATH)
    commands = dependency_profiles.audit_commands(
        catalog,
        profile_set="audit-runtime",
    )
    by_project = {
        Path(command.argv[command.argv.index("--project") + 1]): command.argv
        for command in commands
    }

    assert "CVE-2025-69872" in by_project[ROOT]
    benchmark = ROOT / "requirements/profiles/benchmark"
    assert "GHSA-8mgp-746c-j5xp" in by_project[benchmark]
    assert "GHSA-g4r7-86gm-pgqc" in by_project[benchmark]
    assert "CVE-2025-69872" not in by_project[benchmark]
    assert "CVE-2025-69872" in by_project[
        ROOT / "requirements/profiles/local-inference"
    ]
    assert all(
        "GHSA-8mgp-746c-j5xp" not in argv
        for project, argv in by_project.items()
        if project != benchmark
    )


def test_cli_audit_requires_a_set_and_executes_the_locked_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _temporary_catalog(tmp_path)
    argv = ["audit", "--root", str(tmp_path), "--manifest", str(catalog.manifest)]

    assert dependency_profiles.main(argv) == 1

    calls: list[dependency_profiles.Command] = []
    monkeypatch.setattr(
        dependency_profiles,
        "execute_commands",
        lambda commands: calls.extend(commands),
    )
    assert dependency_profiles.main([*argv, "--set", "example"]) == 0
    assert calls[0].argv[:5] == (
        "uv",
        "audit",
        "--preview-features",
        "audit",
        "--locked",
    )


def test_ci_container_sbom_and_audit_consumers_name_locked_profile_sets() -> None:
    build_workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    molecule_workflow = (ROOT / ".github/workflows/molecule.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    containerfile = (ROOT / "Containerfile").read_text(encoding="utf-8")
    makefile = compose_makefile(ROOT / "Makefile")

    assert "uv sync" not in build_workflow
    assert "uv sync" not in molecule_workflow
    assert "--set ci" in build_workflow
    assert "--set build-azure" in build_workflow
    assert "--set ci-game-e2e" in build_workflow
    assert "--set ci" in molecule_workflow
    for container in (dockerfile, containerfile):
        assert "scripts/dependency_profiles.py sync" in container
        assert "--set core" in container
        assert "--no-install-project" in container
        assert "uv sync" not in container
    assert "DEPENDENCY_PROFILE_SET=sbom" in makefile
    assert "DEPENDENCY_PROFILE_ENVIRONMENT=.venv-sbom" in makefile
    assert "cyclonedx-py environment .venv-sbom" in makefile
    assert "scripts/dependency_profiles.py audit --set audit-runtime" in makefile


def test_unknown_profile_set_fails_closed() -> None:
    catalog = dependency_profiles.load_catalog(ROOT, CATALOG_PATH)
    with pytest.raises(dependency_profiles.ProfileError, match="unknown profile set"):
        dependency_profiles.sync_commands(
            catalog,
            profile_set="typo",
            environment=ROOT / ".venv",
            staging_environment=ROOT / ".venv.profile-stage",
            python=None,
        )


def test_rendered_requirements_are_deduplicated_profile_set_inputs() -> None:
    catalog = dependency_profiles.load_catalog(ROOT, CATALOG_PATH)
    rendered = dependency_profiles.render_requirements(catalog, "e2e-all")
    requirements = rendered.splitlines()

    assert len(requirements) == len(set(requirements))
    assert "fastapi>=0.115.0" in requirements
    assert "langgraph>=0.2.0" in requirements
    assert "pygame>=2.5.0" in requirements
    assert "azure-identity>=1.16.0" in requirements
    assert rendered.endswith("\n")


def test_write_requirements_atomically_replaces_the_destination(tmp_path: Path) -> None:
    catalog = dependency_profiles.load_catalog(ROOT, CATALOG_PATH)
    output = tmp_path / "generated/requirements.txt"
    output.parent.mkdir()
    output.write_text("stale\n", encoding="utf-8")

    dependency_profiles.write_requirements(catalog, "core", output)

    assert output.read_text(encoding="utf-8") == dependency_profiles.render_requirements(
        catalog, "core"
    )
    assert not list(output.parent.glob(f".{output.name}.*"))


def test_cli_export_requires_complete_arguments_and_writes_requirements(
    tmp_path: Path,
) -> None:
    catalog = _temporary_catalog(tmp_path)
    output = tmp_path / "export.txt"

    assert dependency_profiles.main(
        ["export", "--root", str(tmp_path), "--manifest", str(catalog.manifest)]
    ) == 1
    assert dependency_profiles.main(
        [
            "export",
            "--root",
            str(tmp_path),
            "--manifest",
            str(catalog.manifest),
            "--set",
            "example",
            "--output",
            str(output),
        ]
    ) == 0
    assert output.read_text(encoding="utf-8") == "example>=1\n"


def _requirements_for_set(catalog: dict[str, object], name: str) -> list[str]:
    root = _load(ROOT / "pyproject.toml")["project"]
    assert isinstance(root, dict)
    requirements = list(root["dependencies"])
    install_sets = catalog["sets"]
    profiles = catalog["profiles"]
    assert isinstance(install_sets, dict)
    assert isinstance(profiles, dict)
    selected = install_sets[name]["profiles"]
    partitioned_names: set[str] = set()
    for profile_name in selected:
        project = _load(ROOT / profiles[profile_name]["project"] / "pyproject.toml")
        requirements.extend(project["project"]["dependencies"])
        uv = project.get("tool", {}).get("uv", {})
        for excluded in uv.get("exclude-dependencies", []):
            assert isinstance(excluded, str)
            partitioned_names.add(canonicalize_name(Requirement(excluded).name))

    direct_names = {
        canonicalize_name(Requirement(requirement).name) for requirement in requirements
    }
    assert partitioned_names <= direct_names
    return [
        requirement
        for requirement in requirements
        if canonicalize_name(Requirement(requirement).name) not in partitioned_names
    ]


def _assert_requirement_parity(expected: list[str], actual: list[str]) -> None:
    expected_by_name: dict[str, list[Requirement]] = {}
    actual_by_name: dict[str, list[Requirement]] = {}
    for raw in expected:
        requirement = Requirement(raw)
        expected_by_name.setdefault(canonicalize_name(requirement.name), []).append(requirement)
    for raw in actual:
        requirement = Requirement(raw)
        actual_by_name.setdefault(canonicalize_name(requirement.name), []).append(requirement)

    # The immutable fixture is a migration floor: every historical direct
    # requirement must remain represented, while feature-owned profiles may
    # add new direct runtime dependencies after the split.
    assert set(expected_by_name) <= set(actual_by_name)
    for name, expected_requirements in expected_by_name.items():
        actual_text = {str(requirement) for requirement in actual_by_name[name]}
        for expected_requirement in expected_requirements:
            if str(expected_requirement) in actual_text:
                continue
            exact_versions = {
                spec.version
                for requirement in actual_by_name[name]
                for spec in requirement.specifier
                if spec.operator == "==" and "*" not in spec.version
            }
            assert exact_versions
            assert any(
                expected_requirement.specifier.contains(version, prereleases=True)
                for version in exact_versions
            )


def test_profile_sets_preserve_every_pre_split_direct_requirement() -> None:
    legacy = _load(LEGACY_REQUIREMENTS)
    catalog = _load(CATALOG_PATH)
    legacy_root = legacy["root"]["dependencies"]
    optional = legacy["optional"]

    for name, extra_requirements in optional.items():
        _assert_requirement_parity(
            [*legacy_root, *extra_requirements],
            _requirements_for_set(catalog, name),
        )

    _assert_requirement_parity(
        [*legacy_root, *legacy["development"]["dependencies"]],
        _requirements_for_set(catalog, "development"),
    )


def _temporary_catalog(tmp_path: Path) -> dependency_profiles.ProfileCatalog:
    profile = tmp_path / "requirements/profiles/example"
    profile.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "root"\nversion = "0"\ndependencies = []\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text('version = 1\nrequires-python = ">=3.11"\n', encoding="utf-8")
    (profile / "pyproject.toml").write_text(
        """[project]
name = "gludd-profile-example"
version = "0"
requires-python = ">=3.11"
dependencies = ["example>=1"]

[tool.uv]
package = false
""",
        encoding="utf-8",
    )
    (profile / "uv.lock").write_text('version = 1\nrequires-python = ">=3.11"\n', encoding="utf-8")
    manifest = tmp_path / "config/dependency_profiles.toml"
    manifest.parent.mkdir()
    manifest.write_text(
        """schema-version = 1
max-lock-lines = 2499

[profiles.example]
project = "requirements/profiles/example"

[sets.example]
profiles = ["example"]
""",
        encoding="utf-8",
    )
    return dependency_profiles.load_catalog(tmp_path, manifest)


def test_locked_package_version_reads_the_exact_profile_lock(tmp_path: Path) -> None:
    catalog = _temporary_catalog(tmp_path)
    profile_lock = tmp_path / "requirements/profiles/example/uv.lock"
    profile_lock.write_text(
        '''version = 1
requires-python = ">=3.11"

[[package]]
name = "example"
version = "6.22.3"
''',
        encoding="utf-8",
    )

    assert dependency_profiles.locked_package_version(
        catalog,
        profile="example",
        package="example",
    ) == "6.22.3"


def test_locked_package_version_cli_prints_only_the_locked_value(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = _temporary_catalog(tmp_path)
    profile_lock = tmp_path / "requirements/profiles/example/uv.lock"
    profile_lock.write_text(
        '''version = 1
requires-python = ">=3.11"
[[package]]
name = "example"
version = "6.22.3"
''',
        encoding="utf-8",
    )

    assert dependency_profiles.main(
        [
            "locked-version",
            "--root",
            str(tmp_path),
            "--manifest",
            str(catalog.manifest),
            "--profile",
            "example",
            "--package",
            "example",
        ]
    ) == 0
    assert capsys.readouterr().out == "6.22.3\n"


@pytest.mark.parametrize(
    ("lock_body", "message"),
    [
        (
            'version = 1\nrequires-python = ">=3.11"\n',
            "does not lock package 'example'",
        ),
        (
            '''version = 1
requires-python = ">=3.11"
[[package]]
name = "example"
version = "1.0"
[[package]]
name = "example"
version = "2.0"
''',
            "must lock package 'example' exactly once",
        ),
        ("not valid toml = [", "cannot load lock"),
    ],
)
def test_locked_package_version_fails_closed_on_ambiguous_or_invalid_locks(
    tmp_path: Path,
    lock_body: str,
    message: str,
) -> None:
    catalog = _temporary_catalog(tmp_path)
    profile_lock = tmp_path / "requirements/profiles/example/uv.lock"
    profile_lock.write_text(lock_body, encoding="utf-8")

    with pytest.raises(dependency_profiles.ProfileError, match=message):
        dependency_profiles.locked_package_version(
            catalog,
            profile="example",
            package="example",
        )


def test_atomic_sync_failure_preserves_predecessor_environment(tmp_path: Path) -> None:
    catalog = _temporary_catalog(tmp_path)
    environment = tmp_path / ".venv"
    environment.mkdir()
    (environment / "predecessor").write_text("intact", encoding="utf-8")

    def fail_on_profile(command: dependency_profiles.Command) -> None:
        if command.argv[1] == "sync":
            staging = Path(command.environment["UV_PROJECT_ENVIRONMENT"])
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "candidate").write_text("partial", encoding="utf-8")
        if command.argv[1] == "sync" and "profiles/example" in command.argv[3]:
            raise subprocess.CalledProcessError(1, command.argv)

    with pytest.raises(subprocess.CalledProcessError):
        dependency_profiles.sync_profile_set(
            catalog,
            profile_set="example",
            environment=environment,
            python="3.12",
            run=fail_on_profile,
        )

    assert (environment / "predecessor").read_text(encoding="utf-8") == "intact"
    assert not list(tmp_path.glob("..venv.profile-stage-*"))


def test_atomic_sync_promotes_only_after_locked_sync_and_pip_check(tmp_path: Path) -> None:
    catalog = _temporary_catalog(tmp_path)
    environment = tmp_path / ".venv"
    environment.mkdir()
    (environment / "predecessor").write_text("old", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def succeed(command: dependency_profiles.Command) -> None:
        calls.append(command.argv)
        if command.argv[1] == "sync":
            staging = Path(command.environment["UV_PROJECT_ENVIRONMENT"])
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "candidate").write_text("complete", encoding="utf-8")

    dependency_profiles.sync_profile_set(
        catalog,
        profile_set="example",
        environment=environment,
        python="3.12",
        run=succeed,
    )

    assert (environment / "candidate").read_text(encoding="utf-8") == "complete"
    assert not (environment / "predecessor").exists()
    assert calls[-1][1:3] == ("pip", "check")


def test_validation_rejects_an_oversized_or_missing_profile_lock(tmp_path: Path) -> None:
    catalog = _temporary_catalog(tmp_path)
    profile_lock = tmp_path / "requirements/profiles/example/uv.lock"
    profile_lock.write_text("\n".join("entry" for _ in range(2500)), encoding="utf-8")
    with pytest.raises(dependency_profiles.ProfileError, match="2500 lines"):
        dependency_profiles.validate_catalog(catalog)

    profile_lock.unlink()
    with pytest.raises(dependency_profiles.ProfileError, match="missing lock"):
        dependency_profiles.validate_catalog(catalog)


def test_validation_without_locks_allows_lock_generation_bootstrap(tmp_path: Path) -> None:
    catalog = _temporary_catalog(tmp_path)
    (tmp_path / "requirements/profiles/example/uv.lock").unlink()

    dependency_profiles.validate_catalog(catalog, require_locks=False)


def test_catalog_rejects_unknown_profile_references(tmp_path: Path) -> None:
    manifest = tmp_path / "catalog.toml"
    manifest.write_text(
        """schema-version = 1
max-lock-lines = 2499
[profiles.example]
project = "profile"
[sets.broken]
profiles = ["missing"]
""",
        encoding="utf-8",
    )
    with pytest.raises(dependency_profiles.ProfileError, match="unknown profiles: missing"):
        dependency_profiles.load_catalog(tmp_path, manifest)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("schema-version = 2\nmax-lock-lines = 10\nprofiles = {}\nsets = {}\n", "schema-version"),
        ("schema-version = 1\nmax-lock-lines = 2500\nprofiles = {}\nsets = {}\n", "max-lock-lines"),
        ("schema-version = 1\nmax-lock-lines = 10\nprofiles = []\nsets = {}\n", "profiles must"),
        (
            """schema-version = 1
max-lock-lines = 10
[profiles.bad]
project = "../escape"
[sets.core]
profiles = []
""",
            "escapes",
        ),
        (
            """schema-version = 1
max-lock-lines = 10
[profiles.bad]
project = "profile"
[sets.core]
profiles = "bad"
""",
            "string list",
        ),
        (
            """schema-version = 1
max-lock-lines = 10
[profiles.bad]
project = "profile"
[sets.core]
profiles = ["bad", "bad"]
""",
            "duplicates",
        ),
    ],
)
def test_catalog_rejects_malformed_contracts(tmp_path: Path, body: str, message: str) -> None:
    manifest = tmp_path / "catalog.toml"
    manifest.write_text(body, encoding="utf-8")
    with pytest.raises(dependency_profiles.ProfileError, match=message):
        dependency_profiles.load_catalog(tmp_path, manifest)


def test_catalog_reports_missing_or_invalid_toml(tmp_path: Path) -> None:
    with pytest.raises(dependency_profiles.ProfileError, match="cannot load"):
        dependency_profiles.load_catalog(tmp_path, tmp_path / "missing.toml")
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[", encoding="utf-8")
    with pytest.raises(dependency_profiles.ProfileError, match="cannot load"):
        dependency_profiles.load_catalog(tmp_path, invalid)


@pytest.mark.parametrize(
    ("audit_ignore", "message"),
    [
        ('audit-ignore = "CVE-1"\n', "non-empty string list"),
        ('audit-ignore = ["CVE-1", "CVE-1"]\n', "contains duplicates"),
    ],
)
def test_catalog_rejects_invalid_audit_adjudications(
    tmp_path: Path,
    audit_ignore: str,
    message: str,
) -> None:
    manifest = tmp_path / "catalog.toml"
    manifest.write_text(
        audit_ignore
        + """schema-version = 1
max-lock-lines = 10
profiles = {}
sets = {}
""",
        encoding="utf-8",
    )

    with pytest.raises(dependency_profiles.ProfileError, match=message):
        dependency_profiles.load_catalog(tmp_path, manifest)


def test_validation_rejects_non_independent_profile_shapes(tmp_path: Path) -> None:
    catalog = _temporary_catalog(tmp_path)
    root_pyproject = tmp_path / "pyproject.toml"
    root_pyproject.write_text(
        '[project]\nname="root"\nversion="0"\ndependencies=[]\n[project.optional-dependencies]\nx=[]\n',
        encoding="utf-8",
    )
    with pytest.raises(dependency_profiles.ProfileError, match="deployable core"):
        dependency_profiles.validate_catalog(catalog)

    root_pyproject.write_text(
        '[project]\nname="root"\nversion="0"\ndependencies=[]\n',
        encoding="utf-8",
    )
    profile_pyproject = tmp_path / "requirements/profiles/example/pyproject.toml"
    profile_pyproject.write_text(
        """[project]
name = "bad"
version = "0"
dependencies = ["general-ludd-agent"]
[tool.uv]
package = true
workspace = true
""",
        encoding="utf-8",
    )
    with pytest.raises(dependency_profiles.ProfileError, match="package=false"):
        dependency_profiles.validate_catalog(catalog)

    profile_pyproject.write_text(
        profile_pyproject.read_text(encoding="utf-8").replace("package = true", "package = false"),
        encoding="utf-8",
    )
    with pytest.raises(dependency_profiles.ProfileError, match="workspace"):
        dependency_profiles.validate_catalog(catalog)

    profile_pyproject.write_text(
        profile_pyproject.read_text(encoding="utf-8").replace("workspace = true\n", ""),
        encoding="utf-8",
    )
    with pytest.raises(dependency_profiles.ProfileError, match="root project"):
        dependency_profiles.validate_catalog(catalog)


def test_command_executor_merges_environment_and_checks_return_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(argv: tuple[str, ...], *, check: bool, env: dict[str, str]) -> None:
        observed.update(argv=argv, check=check, env=env)

    monkeypatch.setattr(dependency_profiles.subprocess, "run", fake_run)
    dependency_profiles.execute_commands(
        (dependency_profiles.Command(("uv", "lock"), {"PROFILE_TEST": "yes"}),)
    )
    assert observed["argv"] == ("uv", "lock")
    assert observed["check"] is True
    assert observed["env"]["PROFILE_TEST"] == "yes"
    assert observed["env"]["UV_NO_SYNC"] == "0"


def test_validate_only_sync_never_promotes(tmp_path: Path) -> None:
    catalog = _temporary_catalog(tmp_path)
    environment = tmp_path / ".venv"
    environment.mkdir()
    (environment / "predecessor").write_text("old", encoding="utf-8")
    calls: list[dependency_profiles.Command] = []

    dependency_profiles.sync_profile_set(
        catalog,
        profile_set="example",
        environment=environment,
        python=None,
        validate_only=True,
        run=calls.append,
    )

    assert (environment / "predecessor").exists()
    assert all("--dry-run" in command.argv for command in calls if command.argv[1] == "sync")
    assert not any(command.argv[1:3] == ("pip", "check") for command in calls)


def test_promotion_rejects_stale_backup_and_rolls_back_rename_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / ".venv"
    staging = tmp_path / "staging"
    target.mkdir()
    staging.mkdir()
    backup = target.with_name(f".{target.name}.profile-backup-{dependency_profiles.os.getpid()}")
    backup.mkdir()
    with pytest.raises(dependency_profiles.ProfileError, match="stale profile backup"):
        dependency_profiles._promote_environment(staging, target)
    backup.rmdir()

    original_rename = Path.rename

    def fail_candidate_rename(path: Path, destination: Path) -> Path:
        if path == staging:
            raise OSError("simulated promotion failure")
        return original_rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_candidate_rename)
    with pytest.raises(OSError, match="promotion failure"):
        dependency_profiles._promote_environment(staging, target)
    assert target.exists()


def test_promotion_without_a_predecessor_moves_only_the_candidate(tmp_path: Path) -> None:
    target = tmp_path / "environment"
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "candidate").write_text("complete", encoding="utf-8")

    dependency_profiles._promote_environment(staging, target)

    assert (target / "candidate").read_text(encoding="utf-8") == "complete"
    assert not staging.exists()


def test_environment_python_uses_the_windows_virtualenv_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as scoped:
        scoped.setattr(dependency_profiles.os, "name", "nt")
        selected = dependency_profiles._environment_python(tmp_path)

    assert selected == tmp_path / "Scripts/python.exe"


@pytest.mark.parametrize("action", ["check", "lock"])
def test_cli_check_and_lock_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    catalog = _temporary_catalog(tmp_path)
    calls: list[dependency_profiles.Command] = []
    monkeypatch.setattr(dependency_profiles, "execute_commands", lambda commands: calls.extend(commands))
    assert dependency_profiles.main(
        [action, "--root", str(tmp_path), "--manifest", str(catalog.manifest)]
    ) == 0
    assert calls
    assert ("--check" in calls[0].argv) is (action == "check")


def test_cli_sync_requires_a_set_and_forwards_validated_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _temporary_catalog(tmp_path)
    assert dependency_profiles.main(
        ["sync", "--root", str(tmp_path), "--manifest", str(catalog.manifest)]
    ) == 1

    observed: dict[str, object] = {}

    def fake_sync(
        selected: dependency_profiles.ProfileCatalog,
        **kwargs: object,
    ) -> None:
        observed.update(catalog=selected, **kwargs)

    monkeypatch.setattr(dependency_profiles, "sync_profile_set", fake_sync)
    assert dependency_profiles.main(
        [
            "sync",
            "--root",
            str(tmp_path),
            "--manifest",
            str(catalog.manifest),
            "--set",
            "example",
            "--environment",
            ".profile-env",
            "--python",
            "3.12",
            "--validate-only",
        ]
    ) == 0
    assert observed["profile_set"] == "example"
    assert observed["environment"] == tmp_path / ".profile-env"
    assert observed["python"] == "3.12"
    assert observed["validate_only"] is True
