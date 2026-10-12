"""Unit tests for the beta4 Ansible runtime artifact tooling."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tomllib
from importlib import import_module, metadata
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.request import Request

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from scripts import ansible_runtime_artifacts as artifacts
from scripts.makefile_layout import compose_makefile

PINNED_IMAGE = "registry.example/gludd-ee:beta4@sha256:" + "b" * 64
builder_introspect = import_module("ansible_builder._target_scripts.introspect")


@pytest.mark.parametrize("mode", ["1", "0", "invalid", "missing"])
def test_requirement_resolution_make_target_is_bounded_safe_and_registered(
    tmp_path: Path, mode: str,
) -> None:
    collection_root = tmp_path / 'collections" unavailable'
    output = tmp_path / 'resolved" requirements.txt'
    output.write_text("previous owned output\n", encoding="utf-8")
    snapshots = {path: path.read_bytes() for path in [
        artifacts.DEFINITION, artifacts.LOCK, *artifacts.INPUTS.values(),
    ]}
    result = subprocess.run([
        "make", "--no-print-directory", "resolve-ansible-runtime-requirements",
        f"ANSIBLE_EE_COLLECTION_ROOT={'' if mode == 'missing' else collection_root}",
        f"ANSIBLE_EE_RESOLUTION_OUTPUT={output}",
        f"ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY={'1' if mode == 'missing' else mode}",
    ], cwd=artifacts.ROOT, capture_output=True, text=True, timeout=30, check=False)
    if mode == "1":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ANSIBLE_EE_REQUIREMENTS_RESOLVE_PLAN" in result.stdout
        assert str(collection_root) in result.stdout and str(output) in result.stdout
    else:
        assert result.returncode != 0
        if mode == "invalid":
            assert "ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY must be 0 or 1" in result.stdout
        elif mode == "missing":
            assert "ANSIBLE_EE_COLLECTION_ROOT is required" in result.stdout
        else:
            assert "ANSIBLE_EE_REQUIREMENTS_RESOLVE_END rc=0" not in result.stdout
    assert output.read_text(encoding="utf-8") == "previous owned output\n"
    assert not collection_root.exists()
    assert all(path.read_bytes() == before for path, before in snapshots.items())
    contract = json.loads((artifacts.ROOT / "config/make_target_contract.json").read_text())
    entry = next(item for item in contract["targets"] if item["name"] == "resolve-ansible-runtime-requirements")
    assert entry["make_variables"] == [
        "ANSIBLE_EE_COLLECTION_ROOT", "ANSIBLE_EE_RESOLUTION_OUTPUT", "ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY",
    ]
    assert "ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY=1" in entry["behavior"]

# Exact release input, not installed SDKs or a replacement dependency resolver:
# https://github.com/ansible-collections/azure/blob/v3.21.0/requirements.txt
_AZURE_COLLECTION_REQUIREMENTS = """aiohttp>=3.13.0
azure-appconfiguration==1.8.0
azure-cli-core==2.75.0
azure-common==1.1.28
azure-containerregistry==1.2.0
azure-core==1.38.0
azure-eventhub==5.15.0
azure-identity==1.25.2
azure-iot-hub==2.6.1; platform_machine == "x86_64" and python_version < "3.14"
azure-keyvault==4.2.0
azure-keyvault-securitydomain==1.0.0b1
azure-mgmt-apimanagement==4.0.1
azure-mgmt-appconfiguration==5.0.0
azure-mgmt-authorization==4.0.0
azure-mgmt-automation==1.1.0b4
azure-mgmt-batch==17.3.0
azure-mgmt-cdn==13.1.1
azure-mgmt-cognitiveservices==13.7.0
azure-mgmt-compute==33.0.0
azure-mgmt-containerinstance==10.1.0
azure-mgmt-containerregistry==10.3.0
azure-mgmt-containerservice==32.1.0
azure-mgmt-core==1.6.0
azure-mgmt-cosmosdb==10.0.0b3
azure-mgmt-datafactory==9.0.0
azure-mgmt-databricks==2.0.0
azure-mgmt-devtestlabs==10.0.0b2
azure-mgmt-dns==8.1.0
azure-mgmt-eventhub==11.1.0
azure-mgmt-hdinsight==9.1.0b1
azure-mgmt-iothub==3.0.0
azure-mgmt-keyvault==14.0.1
azure-mgmt-loganalytics==13.0.0b7
azure-mgmt-hybridcompute==9.0.0
azure-mgmt-managedservices==7.0.0b2
azure-mgmt-managementgroups==1.1.0b2
azure-mgmt-marketplaceordering==1.2.0b2
azure-mgmt-monitor==6.0.2
azure-mgmt-mysqlflexibleservers==1.0.0
azure-mgmt-network==28.0.0
azure-mgmt-notificationhubs==8.1.0b1
azure-mgmt-nspkg==3.0.2
azure-mgmt-postgresqlflexibleservers==2.0.0
azure-mgmt-privatedns==1.2.0
azure-mgmt-rdbms==10.2.0b17
azure-mgmt-recoveryservices==3.0.0
azure-mgmt-recoveryservicesbackup==9.1.0
azure-mgmt-redis==14.4.0
azure-mgmt-resource==23.2.0
azure-mgmt-resourcegraph==8.1.0b3
azure-mgmt-resourcehealth==1.0.0b6
azure-mgmt-search==9.2.0b2
azure-mgmt-servicebus==8.2.1
azure-mgmt-sql==4.0.0b19
azure-mgmt-storage==25.0.0
azure-mgmt-trafficmanager==1.1.0
azure-mgmt-web==7.3.1
azure-mgmt-eventgrid==10.4.0
azure-ai-ml==1.31.0
azure-nspkg==3.0.2
azure-servicebus==7.14.2
azure-storage-blob==12.23.0b1
azure-storage-file-share==12.24.0
msgraph-sdk==1.6.0
netaddr>=1.3.0
oras>=0.2.38
packaging>=25.0
pandas>=2.3.3
requests>=2.32.5
xmltodict>=1.0.2
"""

# Primary release metadata, including the failure's Linux-only requirement:
# https://pypi.org/pypi/azure-identity/1.26.0/json
# https://github.com/Azure/azure-cli/blob/azure-cli-2.75.0/src/azure-cli-core/setup.py
# https://github.com/Azure/azure-cli/blob/azure-cli-2.91.0/src/azure-cli-core/setup.py
_AZURE_CLI_TRANSITIVE_REQUIREMENTS = {
    "2.75.0": ("msal==1.33.0b1; sys_platform != 'win32'",),
    "2.91.0": ("azure-core~=1.39.0", "msal==1.39.0; sys_platform != 'win32'"),
}
_AZURE_IDENTITY_TRANSITIVE_REQUIREMENTS = ("azure-core>=1.31.0", "msal>=1.39.0")


@pytest.fixture
def azure_collection_fixture(tmp_path: Path) -> Path:
    """Stage the release's actual EE metadata and requirements without installs."""
    root = tmp_path / "collection-fixture"
    collection = root / "ansible_collections" / "azure" / "azcollection"
    (collection / "meta").mkdir(parents=True)
    (collection / "meta" / "execution-environment.yml").write_text(
        "dependencies:\n  python: requirements.txt\nversion: 1\n",
        encoding="utf-8",
    )
    (collection / "MANIFEST.json").write_text(
        json.dumps(
            {"collection_info": {"namespace": "azure", "name": "azcollection", "version": "3.21.0"}}
        ),
        encoding="utf-8",
    )
    (collection / "requirements.txt").write_text(
        _AZURE_COLLECTION_REQUIREMENTS, encoding="utf-8"
    )
    return root


@pytest.fixture
def complete_collection_fixture(azure_collection_fixture: Path) -> Path:
    """Supply every required manifest for focused assembly tests, without installs."""
    azure_manifest = azure_collection_fixture / "ansible_collections" / "azure" / "azcollection" / "MANIFEST.json"
    azure_metadata = json.loads(azure_manifest.read_text(encoding="utf-8"))
    azure_metadata["collection_info"]["dependencies"] = {}
    azure_manifest.write_text(json.dumps(azure_metadata), encoding="utf-8")
    collections = [
        ("cloud", "terraform", "4.0.0"),
        *(("general_ludd", name, release) for name, release in artifacts._COLLECTION_VERSIONS),
    ]
    for namespace, name, release in collections:
        collection = azure_collection_fixture / "ansible_collections" / namespace / name
        collection.mkdir(parents=True)
        (collection / "MANIFEST.json").write_text(
            json.dumps({"collection_info": {
                "namespace": namespace, "name": name, "version": release, "dependencies": {},
            }}),
            encoding="utf-8",
        )
    return azure_collection_fixture


def _builder_python_requirements(lines: list[str]) -> list[Requirement]:
    """Parse Builder's annotations with its own comment handling and packaging."""
    return [
        Requirement(line)
        for line in builder_introspect.strip_comments({"combined": lines})["combined"]
    ]


class _RegistryResponse:
    def __init__(self, digest: str | None) -> None:
        self.headers = {} if digest is None else {"Docker-Content-Digest": digest}

    def __enter__(self) -> _RegistryResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_resolve_base_image_digest_uses_official_manifest_api() -> None:
    digest = "sha256:" + "c" * 64
    calls: list[tuple[Request, int]] = []

    def opener(request: Request, *, timeout: int) -> _RegistryResponse:
        calls.append((request, timeout))
        return _RegistryResponse(digest)

    assert artifacts.resolve_base_image_digest(opener=opener) == (
        f"{artifacts.BASE_IMAGE_TAG}@{digest}"
    )
    request, timeout = calls[0]
    assert request.full_url == artifacts.BASE_IMAGE_MANIFEST_URL
    assert request.get_method() == "HEAD"
    accept = request.get_header("Accept")
    assert accept is not None
    assert "manifest.list.v2+json" in accept
    assert timeout == 30


@pytest.mark.parametrize("digest", [None, "sha256:short", "sha256:" + "G" * 64])
def test_resolve_base_image_digest_rejects_missing_or_invalid_header(
    digest: str | None,
) -> None:
    with pytest.raises(RuntimeError, match="Docker-Content-Digest"):
        artifacts.resolve_base_image_digest(
            opener=lambda *_args, **_kwargs: _RegistryResponse(digest)
        )


def test_refresh_base_image_updates_definition_and_lock_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_image = f"{artifacts.BASE_IMAGE_TAG}@sha256:" + "a" * 64
    new_image = f"{artifacts.BASE_IMAGE_TAG}@sha256:" + "b" * 64
    definition = tmp_path / "execution-environment.yml"
    lock = tmp_path / "runtime-lock.json"
    definition.write_text(
        f"version: 3\nimages:\n  base_image:\n    name: {old_image}\n",
        encoding="utf-8",
    )
    lock.write_text(
        json.dumps({"schema_version": 1, "base_image": old_image, "inputs": {}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "DEFINITION", definition)
    monkeypatch.setattr(artifacts, "LOCK", lock)
    monkeypatch.setattr(artifacts, "resolve_base_image_digest", lambda: new_image)
    monkeypatch.setattr(
        artifacts,
        "expected_input_hashes",
        lambda: {"definition": "sha256:" + "d" * 64},
    )

    assert artifacts.refresh_base_image() == new_image

    assert new_image in definition.read_text(encoding="utf-8")
    refreshed = json.loads(lock.read_text(encoding="utf-8"))
    assert refreshed["base_image"] == new_image
    assert refreshed["inputs"] == {"definition": "sha256:" + "d" * 64}


def test_check_configured_base_image_verifies_the_exact_pinned_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "sha256:" + "e" * 64
    image = f"{artifacts.BASE_IMAGE_TAG}@{digest}"
    definition = tmp_path / "execution-environment.yml"
    definition.write_text(
        f"images:\n  base_image:\n    name: {image}\n",
        encoding="utf-8",
    )
    requests: list[Request] = []

    def opener(request: Request, *, timeout: int) -> _RegistryResponse:
        assert timeout == 30
        requests.append(request)
        return _RegistryResponse(digest)

    monkeypatch.setattr(artifacts, "DEFINITION", definition)

    assert artifacts.check_configured_base_image(opener=opener) == image
    assert requests[0].full_url.endswith(f"/manifests/{digest}")


def test_check_configured_base_image_rejects_a_registry_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "sha256:" + "e" * 64
    definition = tmp_path / "execution-environment.yml"
    definition.write_text(
        f"images:\n  base_image:\n    name: {artifacts.BASE_IMAGE_TAG}@{digest}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "DEFINITION", definition)

    with pytest.raises(RuntimeError, match="identity mismatch"):
        artifacts.check_configured_base_image(
            opener=lambda *_args, **_kwargs: _RegistryResponse("sha256:" + "f" * 64)
        )


def test_tracked_runtime_artifacts_validate() -> None:
    assert artifacts.validate_files() == []


def test_artifact_tests_import_their_own_worktree_implementation() -> None:
    """A shared interpreter must still exercise this checkout's artifact owner."""
    assert Path(__file__).resolve().parents[2] == artifacts.ROOT


def test_controller_azure_cli_has_one_explicit_owner(
    tmp_path: Path,
    azure_collection_fixture: Path,
) -> None:
    """The obsolete inherited CLI cannot constrain the owned identity's MSAL."""
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    excluded = definition["dependencies"]["exclude"]["python"]
    assert "azure-cli-core" in excluded
    exclusion = tmp_path / "exclude-requirements.txt"
    exclusion.write_text("\n".join(excluded) + "\n", encoding="utf-8")
    raw = builder_introspect.process(
        data_dir=str(azure_collection_fixture),
        user_pip=str(artifacts.INPUTS["python"]),
        exclude_pip=str(exclusion),
    )
    merged = builder_introspect.filter_requirements(
        raw["python"], exclude=raw["python"].pop("exclude")
    )
    cli = [
        requirement
        for requirement in _builder_python_requirements(merged)
        if canonicalize_name(requirement.name) == "azure-cli-core"
    ]
    assert len(cli) == 1
    assert not cli[0].specifier.contains("2.75.0")
    user = _builder_python_requirements(raw["python"]["user"])
    assert cli == [
        requirement
        for requirement in user
        if canonicalize_name(requirement.name) == "azure-cli-core"
    ]
    assert merged == [
        f"{line}  # from collection azure.azcollection"
        for line in _AZURE_COLLECTION_REQUIREMENTS.splitlines()
        if canonicalize_name(Requirement(line).name) not in excluded
    ] + [f"{line}  # from collection user" for line in raw["python"]["user"]]
    assert artifacts.AZURE_IDENTITY_REQUIREMENT == "azure-identity==1.26.0"


def test_controller_cli_and_identity_share_supported_linux_transitives(
    tmp_path: Path,
    azure_collection_fixture: Path,
) -> None:
    """Reproduce both MSAL and Azure Core conflicts from actual release metadata."""
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    exclusion = tmp_path / "exclude-requirements.txt"
    exclusion.write_text(
        "\n".join(definition["dependencies"]["exclude"]["python"]) + "\n",
        encoding="utf-8",
    )
    raw = builder_introspect.process(
        data_dir=str(azure_collection_fixture),
        user_pip=str(artifacts.INPUTS["python"]),
        exclude_pip=str(exclusion),
    )
    merged = _builder_python_requirements(builder_introspect.filter_requirements(
        raw["python"], exclude=raw["python"].pop("exclude")
    ))
    cli = [item for item in merged if canonicalize_name(item.name) == "azure-cli-core"]
    assert len(cli) == 1
    cli_pin = list(cli[0].specifier)
    assert len(cli_pin) == 1 and cli_pin[0].operator == "=="
    assert cli_pin[0].version in _AZURE_CLI_TRANSITIVE_REQUIREMENTS
    transitives = [
        Requirement(line)
        for line in (
            *_AZURE_IDENTITY_TRANSITIVE_REQUIREMENTS,
            *_AZURE_CLI_TRANSITIVE_REQUIREMENTS[cli_pin[0].version],
        )
    ]
    for package, candidate in (("msal", "1.39.0"), ("azure-core", "1.39.0")):
        applicable = [
            item for item in [*merged, *transitives]
            if canonicalize_name(item.name) == package
            and (item.marker is None or item.marker.evaluate({"sys_platform": "linux"}))
        ]
        assert applicable
        assert all(item.specifier.contains(candidate) for item in applicable), applicable


def test_resolve_requirements_plan_never_reads_collections_or_runs_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline planning must never be mistaken for transitive resolution proof."""
    monkeypatch.setattr(shutil, "which", lambda _name: pytest.fail("tools must not run"))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("tools must not run"))
    monkeypatch.setattr(metadata, "version", lambda _name: pytest.fail("package metadata must not be read"))
    monkeypatch.setattr(artifacts, "find_spec", lambda _name: pytest.fail("tool metadata must not be read"))
    monkeypatch.setattr(
        artifacts, "_staged_collection_metadata",
        lambda _root: pytest.fail("collection metadata must not be read"),
    )
    output = tmp_path / "resolved.txt"
    assert artifacts.resolve_requirements(tmp_path / "absent", output, True) == 0
    assert not output.exists()


@pytest.mark.parametrize(
    ("uv_output", "builder_version", "probe_status", "message"),
    [
        ("uv 0.12.18\n", "3.1.1", 0, "uv required=0.12.19 observed=0.12.18"),
        ("uv 0.12.19 (release)\n", "3.1.2", 0, "ansible-builder required=3.1.1 observed=3.1.2"),
        ("unknown 0.12.19\n", "3.1.1", 0, "invalid uv version response"),
        ("", "3.1.1", 0, "invalid uv version response"),
        ("uv 0.12.19\n", "3.1.1", 7, "uv version probe failed: rc=7"),
    ],
)
def test_real_resolution_rejects_unmatched_or_unobservable_tool_identities(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    uv_output: str,
    builder_version: str,
    probe_status: int,
    message: str,
) -> None:
    """A successful graph merge cannot authorize tools different from the frozen EE."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(metadata, "version", lambda name: builder_version if name == "ansible-builder" else "")
    monkeypatch.setattr(artifacts, "import_module", lambda _name: pytest.fail("introspection must not run"))

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        assert command == ["/bin/uv", "--version"]
        assert kwargs["timeout"] == 5
        assert kwargs["capture_output"] is True and kwargs["text"] is True
        assert kwargs["cwd"] == artifacts.ROOT and kwargs["check"] is False
        return SimpleNamespace(returncode=probe_status, stdout=uv_output)

    monkeypatch.setattr(subprocess, "run", fake_run)
    output = tmp_path / "resolved.txt"
    assert artifacts.resolve_requirements(complete_collection_fixture, output, False) == 2
    assert message in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("error", [OSError("missing uv executable"), subprocess.TimeoutExpired("uv --version", 5)])
def test_real_resolution_reports_bounded_version_probe_errors(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: OSError | subprocess.TimeoutExpired,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(artifacts, "import_module", lambda _name: pytest.fail("introspection must not run"))

    def failed_probe(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(subprocess, "run", failed_probe)
    assert artifacts.resolve_requirements(complete_collection_fixture, tmp_path / "resolved.txt", False) == 2
    assert "uv version probe failed" in capsys.readouterr().err


def test_real_resolution_rejects_unavailable_builder_distribution_metadata(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def missing_metadata(_name: str) -> str:
        raise metadata.PackageNotFoundError("ansible-builder")

    monkeypatch.setattr(metadata, "version", missing_metadata)
    monkeypatch.setattr(artifacts, "import_module", lambda _name: pytest.fail("introspection must not run"))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="uv 0.12.19\n",
    ))
    assert artifacts.resolve_requirements(complete_collection_fixture, tmp_path / "resolved.txt", False) == 2
    assert "ansible-builder distribution metadata is unavailable" in capsys.readouterr().err


@pytest.mark.parametrize("changed_source", ["definition", "controller-lock"])
def test_real_resolution_derives_tool_identities_from_the_frozen_inputs(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changed_source: str,
) -> None:
    """Read existing source pins; an independently hard-coded expected version can drift."""
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    if changed_source == "definition":
        definition = tmp_path / "execution-environment.yml"
        definition.write_text(
            artifacts.DEFINITION.read_text(encoding="utf-8").replace("uv==0.12.19", "uv==0.12.20"),
            encoding="utf-8",
        )
        monkeypatch.setattr(artifacts, "DEFINITION", definition)
        expected = "uv required=0.12.20 observed=0.12.19"
    else:
        profile = tmp_path / "pyproject.toml"
        profile.write_text(artifacts.CONTROLLER_PROFILE.read_text(encoding="utf-8"), encoding="utf-8")
        profile.with_name("uv.lock").write_text(
            '[[package]]\nname = "ansible-builder"\nversion = "3.1.2"\n', encoding="utf-8",
        )
        monkeypatch.setattr(artifacts, "CONTROLLER_PROFILE", profile)
        expected = "ansible-builder required=3.1.2 observed=3.1.1"
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(metadata, "version", lambda _name: "3.1.1")
    monkeypatch.setattr(artifacts, "import_module", lambda _name: pytest.fail("introspection must not run"))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="uv 0.12.19\n",
    ))
    assert artifacts.resolve_requirements(complete_collection_fixture, tmp_path / "resolved.txt", False) == 2
    assert expected in capsys.readouterr().err


def test_resolve_requirements_rejects_partial_collection_inputs_before_uv(
    tmp_path: Path,
    azure_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An Azure-only fixture is not the complete execution environment's graph."""
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("uv must not run"))
    assert artifacts.resolve_requirements(azure_collection_fixture, tmp_path / "resolved.txt", False) == 1
    assert "missing staged collection manifest: cloud.terraform" in capsys.readouterr().err


def test_resolve_requirements_uses_real_builder_assembly_and_bounded_uv_compile(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise mature introspection and pass the complete graph to uv's resolver."""
    output = tmp_path / "resolved.txt"
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    calls: list[list[str]] = []
    version_probes: list[list[str]] = []

    def fake_run(
        command: list[str], *, cwd: Path, check: bool,
        env: dict[str, str] | None = None, capture_output: bool = False,
        text: bool = False, timeout: int | None = None,
    ) -> SimpleNamespace:
        assert cwd == artifacts.ROOT and not check
        if command == ["/bin/uv", "--version"]:
            assert capture_output and text and timeout == 5
            version_probes.append(command)
            return SimpleNamespace(returncode=0, stdout="uv 0.12.19 (release)\n")
        assert env is not None
        assert command[:2] == [sys.executable, str(artifacts.ROOT / "scripts" / "stream_command.py")]
        assert command[command.index("--heartbeat-secs") + 1] == "15"
        assert command[command.index("--quiet-secs") + 1] == "60"
        assert command[command.index("--max-secs") + 1] == "180"
        compile_command = command[command.index("--") + 1:]
        assert compile_command[:3] == ["/bin/uv", "pip", "compile"]
        assert compile_command[compile_command.index("--python-version") + 1] == "3.11"
        assert compile_command[compile_command.index("--python-platform") + 1] == "x86_64-manylinux_2_28"
        assert "--no-python-downloads" in compile_command
        assert "install" not in compile_command and "--no-deps" not in compile_command
        assert "--no-build-isolation" not in compile_command and "--override" not in compile_command
        assert env["UV_CONCURRENT_BUILDS"] == "1"
        assert env["UV_HTTP_TIMEOUT"] == "30" and env["UV_HTTP_RETRIES"] == "0"
        assert env["UV_PYTHON_DOWNLOADS"] == "never"
        assert env["UV_LINK_MODE"] == "copy"
        configuration = Path(compile_command[compile_command.index("--config-file") + 1])
        assert tomllib.loads(configuration.read_text(encoding="utf-8")) == {
            "pip": {"extra-build-dependencies": {"searxng": ["pyyaml==6.0.2"]}}
        }
        combined = _builder_python_requirements(Path(compile_command[3]).read_text(encoding="utf-8").splitlines())
        requirements = [str(item) for item in combined]
        assert "ansible-core==2.19.12" in requirements
        assert "ansible-runner==2.4.3" in requirements
        assert "azure-identity==1.26.0" in requirements
        assert "azure-cli-core==2.91.0" in requirements
        assert "azure-core==1.39.0" in requirements
        assert "azure-cli-core==2.75.0" not in requirements
        assert "azure-core==1.38.0" not in requirements
        excluded = {"azure-identity", "azure-cli-core", "azure-core"}
        for line in _AZURE_COLLECTION_REQUIREMENTS.splitlines():
            requirement = Requirement(line)
            if canonicalize_name(requirement.name) not in excluded:
                assert requirement in combined
        for line in artifacts.INPUTS["python"].read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                assert Requirement(line) in combined
        destination = Path(compile_command[compile_command.index("--output-file") + 1])
        assert destination != output
        assert destination.parent == configuration.parent
        destination.write_text("msal==1.39.0\n", encoding="utf-8")
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert artifacts.resolve_requirements(complete_collection_fixture, output, False) == 0
    assert output.read_text(encoding="utf-8") == "msal==1.39.0\n"
    assert len(calls) == 1
    assert version_probes == [["/bin/uv", "--version"]]
    observed = capsys.readouterr().out
    assert "ANSIBLE_EE_RESOLVER_TOOLS uv=0.12.19 ansible_builder=3.1.1" in observed
    assert "ANSIBLE_EE_REQUIREMENTS_RESOLVE_END rc=0" in observed


def test_resolve_requirements_rejects_invalid_artifacts_before_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: ["stale inputs"])
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("tools must not run"))
    assert artifacts.resolve_requirements(tmp_path, tmp_path / "resolved.txt", False) == 1


@pytest.mark.parametrize(
    ("available", "expected_message"),
    [({"uv"}, "ansible-builder is unavailable"), ({"ansible_builder"}, "uv is unavailable")],
)
def test_resolve_requirements_reports_missing_tools(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    available: set[str],
    expected_message: str,
) -> None:
    monkeypatch.setattr(artifacts, "find_spec", lambda name: object() if name in available else None)
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}" if name in available else None)
    assert artifacts.resolve_requirements(complete_collection_fixture, tmp_path / "resolved.txt", False) == 2
    assert expected_message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("not json", "invalid staged collection manifest"),
        ("{}", "invalid staged collection manifest"),
        (json.dumps({"collection_info": {
            "namespace": "azure", "name": "azcollection", "version": "3.20.0", "dependencies": {},
        }}), "staged collection version mismatch"),
        (json.dumps({"collection_info": {
            "namespace": "wrong", "name": "azcollection", "version": "3.21.0", "dependencies": {},
        }}), "staged collection identity mismatch"),
        (json.dumps({"collection_info": {
            "namespace": "azure", "name": "azcollection", "version": "3.21.0",
            "dependencies": {"missing.transitive": ">=1.0.0"},
        }}), "missing staged collection manifest: missing.transitive"),
    ],
)
def test_resolve_requirements_rejects_drifted_or_incomplete_staged_metadata(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    replacement: str,
    message: str,
) -> None:
    manifest = complete_collection_fixture / "ansible_collections" / "azure" / "azcollection" / "MANIFEST.json"
    manifest.write_text(replacement, encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("uv must not run"))
    assert artifacts.resolve_requirements(complete_collection_fixture, tmp_path / "resolved.txt", False) == 1
    assert message in capsys.readouterr().err


def test_resolve_requirements_rejects_missing_declared_python_metadata(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    metadata = (
        complete_collection_fixture / "ansible_collections" / "azure" / "azcollection"
        / "meta" / "execution-environment.yml"
    )
    metadata.write_text("dependencies:\n  python: absent.txt\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("uv must not run"))
    assert artifacts.resolve_requirements(complete_collection_fixture, tmp_path / "resolved.txt", False) == 1
    assert "missing declared collection Python requirements: azure.azcollection" in capsys.readouterr().err


def test_resolve_requirements_checks_additional_transitive_collection_metadata(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every staged dependency is checked, including collections outside the roster."""
    additional = complete_collection_fixture / "ansible_collections" / "community" / "helper"
    additional.mkdir(parents=True)
    (additional / "MANIFEST.json").write_text(json.dumps({"collection_info": {
        "namespace": "community", "name": "helper", "version": "1.0.0",
        "dependencies": {"missing.transitive": ">=1.0.0"},
    }}), encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("uv must not run"))
    assert artifacts.resolve_requirements(complete_collection_fixture, tmp_path / "resolved.txt", False) == 1
    assert "missing staged collection manifest: missing.transitive" in capsys.readouterr().err


def test_resolve_requirements_rejects_transitive_collection_version_mismatch(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = complete_collection_fixture / "ansible_collections" / "cloud" / "terraform" / "MANIFEST.json"
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    metadata["collection_info"]["dependencies"] = {"azure.azcollection": ">=4.0.0"}
    manifest.write_text(json.dumps(metadata), encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("uv must not run"))
    assert artifacts.resolve_requirements(complete_collection_fixture, tmp_path / "resolved.txt", False) == 1
    assert "staged collection dependency mismatch: cloud.terraform -> azure.azcollection" in capsys.readouterr().err


@pytest.mark.parametrize("return_code", [1, 124])
def test_resolve_requirements_retains_previous_output_on_failure_or_timeout(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
    return_code: int,
) -> None:
    output = tmp_path / "resolved.txt"
    output.write_text("previous proof\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda command, **_kwargs: SimpleNamespace(
        returncode=0 if command[-1] == "--version" else return_code,
        stdout="uv 0.12.19\n",
    ))
    assert artifacts.resolve_requirements(complete_collection_fixture, output, False) == return_code
    assert output.read_text(encoding="utf-8") == "previous proof\n"


def test_resolve_requirements_rejects_empty_resolver_output(
    tmp_path: Path,
    complete_collection_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="uv 0.12.19\n",
    ))
    assert artifacts.resolve_requirements(complete_collection_fixture, tmp_path / "resolved.txt", False) == 1


def test_builder_introspection_reproduces_the_unexcluded_azure_sdk_conflict(
    azure_collection_fixture: Path,
) -> None:
    """Builder does not reconcile incompatible collection and explicit user pins."""
    assert version("ansible-builder") == "3.1.1"
    collection = azure_collection_fixture / "ansible_collections" / "azure" / "azcollection"
    collection_python, collection_system = builder_introspect.process_collection(str(collection))
    assert collection_python == _AZURE_COLLECTION_REQUIREMENTS.splitlines()
    assert collection_system == []
    raw = builder_introspect.process(
        data_dir=str(azure_collection_fixture),
        user_pip=str(artifacts.INPUTS["python"]),
    )
    assert raw["python"]["azure.azcollection"] == collection_python
    merged = builder_introspect.filter_requirements(raw["python"])
    assert "azure-identity==1.25.2  # from collection azure.azcollection" in merged
    assert "azure-identity==1.26.0  # from collection user" in merged
    identity = [
        requirement
        for requirement in _builder_python_requirements(merged)
        if canonicalize_name(requirement.name) == "azure-identity"
    ]
    assert [str(requirement.specifier) for requirement in identity] == ["==1.25.2", "==1.26.0"]
    for candidate in ("1.25.2", "1.26.0"):
        assert not all(requirement.specifier.contains(candidate) for requirement in identity)


def test_controller_azure_identity_has_one_explicit_owner(
    tmp_path: Path,
    azure_collection_fixture: Path,
) -> None:
    """Keep one identity owner while excluding only explicitly owned Azure pins."""
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    assert definition["dependencies"].get("exclude") == {
        "python": ["azure-identity", "azure-cli-core", "azure-core"]
    }
    exclusion = tmp_path / "exclude-requirements.txt"
    exclusion.write_text(
        "\n".join(definition["dependencies"]["exclude"]["python"]) + "\n",
        encoding="utf-8",
    )
    raw = builder_introspect.process(
        data_dir=str(azure_collection_fixture),
        user_pip=str(artifacts.INPUTS["python"]),
        exclude_pip=str(exclusion),
    )
    assert raw["python"]["exclude"] == ["azure-identity", "azure-cli-core", "azure-core"]
    user_requirements = raw["python"]["user"]
    merged = builder_introspect.filter_requirements(
        raw["python"], exclude=raw["python"].pop("exclude")
    )
    expected_collection = [
        f"{line}  # from collection azure.azcollection"
        for line in _AZURE_COLLECTION_REQUIREMENTS.splitlines()
        if canonicalize_name(Requirement(line).name) not in {"azure-identity", "azure-cli-core", "azure-core"}
    ]
    assert merged == expected_collection + [
        f"{line}  # from collection user" for line in user_requirements
    ]
    identity = [
        requirement
        for requirement in _builder_python_requirements(merged)
        if canonicalize_name(requirement.name) == "azure-identity"
    ]
    assert len(identity) == 1
    assert str(identity[0].specifier) == "==1.26.0"
    assert identity[0].specifier.contains("1.26.0")
    assert not identity[0].specifier.contains("1.25.2")
    assert f"{artifacts.SEARXNG_REQUIREMENT}  # from collection user" in merged
    controller = tomllib.loads(artifacts.CONTROLLER_PROFILE.read_text(encoding="utf-8"))
    assert [
        line
        for line in controller["project"]["dependencies"]
        if canonicalize_name(Requirement(line).name) == "azure-identity"
    ] == ["azure-identity==1.26.0"]


@pytest.mark.parametrize(
    "exclusion",
    [
        None,
        {"python": []},
        {"python": ["azure-identity", "azure-core"]},
        {"python": ["~azure.*"]},
        {"all_from_collections": ["azure.azcollection"]},
    ],
)
def test_runtime_validation_rejects_missing_or_broadened_sdk_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exclusion: dict[str, list[str]] | None,
) -> None:
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    if exclusion is None:
        definition["dependencies"].pop("exclude", None)
    else:
        definition["dependencies"]["exclude"] = exclusion
    changed_definition = tmp_path / "execution-environment.yml"
    changed_definition.write_text(yaml.safe_dump(definition), encoding="utf-8")
    inputs = dict(artifacts.INPUTS)
    inputs["definition"] = changed_definition
    monkeypatch.setattr(artifacts, "DEFINITION", changed_definition)
    monkeypatch.setattr(artifacts, "INPUTS", inputs)
    lock = json.loads(artifacts.LOCK.read_text(encoding="utf-8"))
    lock["inputs"] = artifacts.expected_input_hashes()
    changed_lock = tmp_path / "runtime-lock.json"
    changed_lock.write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setattr(artifacts, "LOCK", changed_lock)

    assert artifacts.validate_files() == [
        "execution environment dependencies must name the locked inputs and controller interpreter"
    ]


def test_runtime_validation_rejects_changing_the_owned_azure_identity_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_requirements = artifacts.INPUTS["python"].read_text(encoding="utf-8")
    assert "azure-identity==1.26.0" in user_requirements
    changed_requirements = tmp_path / "requirements.txt"
    changed_requirements.write_text(
        user_requirements.replace("azure-identity==1.26.0", "azure-identity==1.25.2"),
        encoding="utf-8",
    )
    inputs = dict(artifacts.INPUTS)
    inputs["python"] = changed_requirements
    monkeypatch.setattr(artifacts, "INPUTS", inputs)
    lock = json.loads(artifacts.LOCK.read_text(encoding="utf-8"))
    lock["inputs"] = artifacts.expected_input_hashes()
    changed_lock = tmp_path / "runtime-lock.json"
    changed_lock.write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setattr(artifacts, "LOCK", changed_lock)

    assert artifacts.validate_files() == [
        "controller Python requirements must contain only pinned Azure Identity, "
        "CLI Core, Azure Core, Azure Monitor, "
        "Frictionless, jsonpointer, openapi-core, SignXML, and official SearXNG"
    ]


def test_controller_base_installs_git_before_vcs_python_dependencies() -> None:
    """The source-pinned SearXNG build must not depend on host Git."""
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    base_steps = definition["additional_build_steps"].get("append_base", [])
    assert "RUN $PKGMGR install -y git-core && $PKGMGR clean all" in base_steps
    assert any("git+https://" in line for line in (
        artifacts.CONFIG_ROOT / "requirements.txt"
    ).read_text(encoding="utf-8").splitlines())


def test_controller_base_pins_current_pip_before_python_installation() -> None:
    """Repair the observed outdated installer, without suppressing notices."""
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    base_steps = definition["additional_build_steps"].get("append_base", [])
    assert "RUN $PYCMD -m pip install --no-cache-dir --upgrade pip==26.2.1" in base_steps


def test_controller_augments_only_searxng_isolated_metadata_dependencies() -> None:
    """Runtime PyYAML cannot satisfy the source package's isolated metadata import."""
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    base_steps = definition["additional_build_steps"].get("append_base", [])
    assert "RUN $PYCMD -m pip install --no-cache-dir uv==0.12.19" in base_steps
    config_steps = [step for step in base_steps if "[pip.extra-build-dependencies]" in step]
    assert len(config_steps) == 1
    tokens = shlex.split(config_steps[0].removeprefix("RUN "))
    printf_index = tokens.index("printf")
    redirect_index = tokens.index(">")
    assert tokens[printf_index + 1] == "%s\\n"
    assert tokens[redirect_index + 1] == "/etc/gludd/ansible-ee-uv.toml"
    configuration = tomllib.loads("\n".join(tokens[printf_index + 2:redirect_index]))
    assert configuration == {
        "pip": {"extra-build-dependencies": {"searxng": ["pyyaml==6.0.2"]}}
    }
    rendered_steps = "\n".join(
        step for steps in definition["additional_build_steps"].values() for step in steps
    )
    assert "--no-build-isolation" not in rendered_steps
    assert "NO_BUILD_ISOLATION" not in rendered_steps
    assert "--no-deps" not in rendered_steps


def test_generated_controller_build_preserves_installer_and_interpreter_boundaries(
    tmp_path: Path,
) -> None:
    """Exercise Builder's real generation, without a container or dependency install."""
    staged_config = tmp_path / "config" / "ansible"
    staged_config.mkdir(parents=True)
    for input_path in artifacts.INPUTS.values():
        shutil.copy2(input_path, staged_config / input_path.name)
    for _source, artifact in artifacts.COLLECTION_ARTIFACTS:
        staged_artifact = tmp_path / "dist" / "collections" / artifact.name
        staged_artifact.parent.mkdir(parents=True, exist_ok=True)
        staged_artifact.write_bytes(b"generation-only collection fixture")
    context = tmp_path / "context"
    generated = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-m",
            "ansible_builder",
            "create",
            "--file",
            str(staged_config / artifacts.DEFINITION.name),
            "--context",
            str(context),
            "--output-filename",
            "Dockerfile",
        ],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    rendered = (context / "Dockerfile").read_text(encoding="utf-8")
    base, downstream = rendered.split("FROM base AS galaxy", 1)
    builder, final = downstream.split("FROM base AS final", 1)
    assert 'ENV PIPCMD="/usr/bin/python3.11 -m uv pip"' in base
    assert "ENV UV_CONFIG_FILE=/etc/gludd/ansible-ee-uv.toml" in base
    assert "ENV UV_PYTHON=/usr/bin/python3.11 UV_SYSTEM_PYTHON=true" in base
    assert "ENV UV_PYTHON=/tmp/venv/bin/python UV_SYSTEM_PYTHON=false" in builder
    assert builder.index("ENV UV_PYTHON=/tmp/venv/bin/python") < builder.index(
        "RUN /output/scripts/assemble"
    )
    assert "/tmp/venv/bin/python" not in final
    assert "RUN /output/scripts/install-from-bindep" in final
    scripts = context / "_build" / "scripts"
    for script_name in ("assemble", "install-from-bindep"):
        script = (scripts / script_name).read_text(encoding="utf-8")
        assert 'PIPCMD="${PIPCMD:=$PYCMD -m pip}"' in script
        assert "$PIPCMD install" in script
    assert (context / "_build" / "requirements.txt").read_text(encoding="utf-8") == (
        artifacts.INPUTS["python"].read_text(encoding="utf-8")
    )
    assert not (context / "_build" / "setup.py").exists()


def test_generated_controller_build_wires_the_narrow_sdk_exclusion(
    tmp_path: Path,
    azure_collection_fixture: Path,
) -> None:
    """Execute the generated introspection script locally, never its installers."""
    staged_config = tmp_path / "config" / "ansible"
    staged_config.mkdir(parents=True)
    for input_path in artifacts.INPUTS.values():
        shutil.copy2(input_path, staged_config / input_path.name)
    for _source, artifact in artifacts.COLLECTION_ARTIFACTS:
        staged_artifact = tmp_path / "dist" / "collections" / artifact.name
        staged_artifact.parent.mkdir(parents=True, exist_ok=True)
        staged_artifact.write_bytes(b"generation-only collection fixture")
    context = tmp_path / "context"
    generated = subprocess.run(
        [
            sys.executable, "-W", "error", "-m", "ansible_builder", "create",
            "--file", str(staged_config / artifacts.DEFINITION.name),
            "--context", str(context), "--output-filename", "Dockerfile",
        ],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    rendered = (context / "Dockerfile").read_text(encoding="utf-8")
    build_inputs = context / "_build"
    assert (build_inputs / "exclude-requirements.txt").read_text(encoding="utf-8").splitlines() == [
        "azure-identity", "azure-cli-core", "azure-core"
    ]
    assert (build_inputs / "requirements.txt").read_bytes() == artifacts.INPUTS["python"].read_bytes()
    assert "COPY _build/exclude-requirements.txt exclude-requirements.txt" in rendered
    assert "COPY _build/requirements.txt requirements.txt" in rendered
    introspection = [
        line for line in rendered.splitlines()
        if line.startswith("RUN $PYCMD /output/scripts/introspect.py introspect")
    ]
    assert len(introspection) == 1
    assert rendered.index(introspection[0]) < rendered.index("RUN /output/scripts/assemble")
    tokens = shlex.split(introspection[0].removeprefix("RUN "))
    assert tokens[:3] == ["$PYCMD", "/output/scripts/introspect.py", "introspect"]
    assert "--user-pip=requirements.txt" in tokens
    assert "--exclude-pip-reqs=exclude-requirements.txt" in tokens
    for forbidden in (
        "--exclude-collection-reqs", "--no-deps", "--no-build-isolation",
        "PIP_NO_DEPS", "UV_NO_BUILD_ISOLATION", "--skip-pip-check",
    ):
        assert forbidden not in rendered
    scripts = build_inputs / "scripts"
    assemble = (scripts / "assemble").read_text(encoding="utf-8")
    assert (
        "$PIPCMD install $CONSTRAINTS $PIP_OPTS --cache-dir=/output/wheels "
        "-r /tmp/src/requirements.txt"
    ) in assemble
    assert "--no-deps" not in assemble
    assert "--no-build-isolation" not in assemble
    builder_source = builder_introspect.__file__
    assert builder_source is not None
    assert (scripts / "introspect.py").read_bytes() == Path(builder_source).read_bytes()
    combined_python = tmp_path / "combined-requirements.txt"
    path_options = {
        "--user-pip": build_inputs / "requirements.txt",
        "--exclude-pip-reqs": build_inputs / "exclude-requirements.txt",
        "--user-bindep": build_inputs / "bindep.txt",
        "--write-pip": combined_python,
        "--write-bindep": tmp_path / "combined-bindep.txt",
    }
    command = [
        sys.executable, "-W", "error", str(scripts / "introspect.py"),
        "introspect", str(azure_collection_fixture),
    ]
    for token in tokens[3:]:
        option, separator, _value = token.partition("=")
        assert separator == "=" and option in path_options
        command.append(f"{option}={path_options[option]}")
    introspected = subprocess.run(
        command, cwd=tmp_path, capture_output=True, encoding="utf-8",
        check=False, timeout=30,
    )
    assert introspected.returncode == 0, introspected.stdout + introspected.stderr
    merged = combined_python.read_text(encoding="utf-8").splitlines()
    user = builder_introspect.pip_file_data(str(artifacts.INPUTS["python"]))
    assert merged == [
        f"{line}  # from collection azure.azcollection"
        for line in _AZURE_COLLECTION_REQUIREMENTS.splitlines()
        if canonicalize_name(Requirement(line).name) not in {"azure-identity", "azure-cli-core", "azure-core"}
    ] + [f"{line}  # from collection user" for line in user]


def test_runtime_stages_native_collections_dependencies_and_pinned_opentofu() -> None:
    requirements = yaml.safe_load(
        (artifacts.CONFIG_ROOT / "requirements.yml").read_text(encoding="utf-8")
    )["collections"]
    collection_versions = {
        item["name"]: item.get("version")
        for item in requirements
        if item.get("type") != "file"
    }
    local_artifacts = {
        item["name"] for item in requirements if item.get("type") == "file"
    }
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    build_steps = "\n".join(definition["additional_build_steps"]["append_final"])

    assert collection_versions == {
        "azure.azcollection": "3.21.0",
        "cloud.terraform": "4.0.0",
    }
    assert "collections/general_ludd-azure-0.2.0.tar.gz" in local_artifacts
    assert "collections/general_ludd-ai_ml-0.2.0.tar.gz" in local_artifacts
    assert "collections/general_ludd-infrastructure-0.2.0.tar.gz" in local_artifacts
    assert "collections/general_ludd-travel-0.1.0.tar.gz" in local_artifacts
    assert "collections/general_ludd-xml-0.1.0.tar.gz" in local_artifacts
    assert len(artifacts.COLLECTION_ARTIFACTS) == 8
    assert any(source.name == "ai_ml" for source, _artifact in artifacts.COLLECTION_ARTIFACTS)
    assert any(source.name == "azure" for source, _artifact in artifacts.COLLECTION_ARTIFACTS)
    assert any(
        source.name == "infrastructure"
        for source, _artifact in artifacts.COLLECTION_ARTIFACTS
    )
    assert any(source.name == "travel" for source, _artifact in artifacts.COLLECTION_ARTIFACTS)
    assert any(source.name == "xml" for source, _artifact in artifacts.COLLECTION_ARTIFACTS)
    python_requirements = (
        artifacts.CONFIG_ROOT / "requirements.txt"
    ).read_text(encoding="utf-8")
    assert (
        "searxng @ git+https://github.com/searxng/searxng.git@"
        "7b4612e86250389dc9d5ee67e4cc2cd64d06602a"
    ) in python_requirements
    assert "frictionless==5.19.1" in python_requirements
    assert "jsonpointer==3.2.0" in python_requirements
    assert "openapi-core==0.23.1" in python_requirements
    assert "signxml==5.1.0" in python_requirements
    assert "azure-identity==1.26.0" in python_requirements
    assert "azure-monitor-query==2.0.0" in python_requirements
    assert "azure.identity" in artifacts.EXPECTED_CONTROLLER_IMPORTS
    assert "azure.monitor.query" in artifacts.EXPECTED_CONTROLLER_IMPORTS
    assert "frictionless" in artifacts.EXPECTED_CONTROLLER_IMPORTS
    assert "jsonpointer" in artifacts.EXPECTED_CONTROLLER_IMPORTS
    assert "openapi_core" in artifacts.EXPECTED_CONTROLLER_IMPORTS
    assert "searx.webapp" in artifacts.EXPECTED_CONTROLLER_IMPORTS
    assert "signxml" in artifacts.EXPECTED_CONTROLLER_IMPORTS
    assert "tofu_1.12.6_linux_amd64.zip" in build_steps
    assert "tofu_1.12.6_linux_arm64.zip" in build_steps
    assert "5dc43da4f750f33873dc25e94587128709e819e544b7be9016b255316153c3a8" in build_steps
    assert "e573979ba68a17fe7b881752051a694a7efcd970e39521f6a25775197861ed4d" in build_steps
    assert "sha256sum --check --strict" in build_steps
    assert "github.com/opentofu/opentofu/releases/download/v1.12.6" in build_steps
    assert "/usr/local/bin/tofu" in build_steps
    assert "releases.hashicorp.com" not in build_steps
    assert "/usr/local/bin/terraform" not in build_steps
    workflow = (artifacts.ROOT / ".github" / "workflows" / "build.yml").read_text(
        encoding="utf-8"
    )
    assert '"${EE_TAG}" /usr/local/bin/tofu version' in workflow


def test_runtime_validation_rejects_hashicorp_terraform_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    definition["additional_build_steps"]["append_final"].append(
        "RUN curl https://releases.hashicorp.com && "
        "install terraform /usr/local/bin/terraform"
    )
    incompatible = tmp_path / "execution-environment.yml"
    incompatible.write_text(yaml.safe_dump(definition), encoding="utf-8")
    inputs = dict(artifacts.INPUTS)
    inputs["definition"] = incompatible
    monkeypatch.setattr(artifacts, "DEFINITION", incompatible)
    monkeypatch.setattr(artifacts, "INPUTS", inputs)

    assert (
        "execution environment must not install HashiCorp Terraform"
        in artifacts.validate_files()
    )


def test_validate_reports_missing_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.yml"
    monkeypatch.setattr(artifacts, "INPUTS", {"definition": missing})
    assert artifacts.validate_files() == [f"missing runtime artifact: {missing}"]


def test_validate_reports_dependency_leaks_and_missing_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_project = {
        "project": {
            "dependencies": ["ansible-core==2", "ansible-runner==2", "ansible-builder==3"],
        }
    }
    controller_project: dict[str, dict[str, list[str]]] = {"project": {"dependencies": []}}
    projects = iter((root_project, controller_project))
    monkeypatch.setattr(tomllib, "loads", lambda _text: next(projects))
    errors = artifacts.validate_files()
    assert {error for error in errors if "dependency" in error} == {
        "core dependency leak: ansible-core",
        "core dependency leak: ansible-runner",
        "core dependency leak: ansible-builder",
        "missing optional controller dependency: ansible-core",
        "missing optional controller dependency: ansible-runner",
        "missing optional controller dependency: azure-identity",
        "missing optional controller dependency: azure-monitor-query",
        "missing optional controller dependency: jsonpointer",
        "missing optional controller dependency: openapi-core",
        "missing optional controller dependency: signxml",
    }


def test_validate_reports_definition_lock_and_managed_host_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "runtime-lock.json"
    managed = tmp_path / "managed.json"
    lock.write_text(
        json.dumps(
            {
                "schema_version": 9,
                "release": "old",
                "base_image": "different",
                "inputs": {},
            }
        ),
        encoding="utf-8",
    )
    managed.write_text(
        json.dumps(
            {
                "ambient_interpreters_allowed": True,
                "interpreter_variable": "python",
                "requirements": "ambient",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "LOCK", lock)
    monkeypatch.setattr(artifacts, "MANAGED", managed)
    monkeypatch.setattr(
        yaml,
        "safe_load",
        lambda _text: {"version": 2, "images": {"base_image": {"name": "latest"}}, "dependencies": {}},
    )
    errors = artifacts.validate_files()
    assert "execution environment base image is not digest-pinned" in errors
    assert "execution environment definition must use schema version 3" in errors
    assert "execution environment dependencies must name the locked inputs and controller interpreter" in errors
    assert "runtime lock schema/release mismatch" in errors
    assert "runtime lock base image differs from execution environment definition" in errors
    assert "runtime lock input hashes are stale; run make update-ansible-runtime-lock" in errors
    assert "managed-host manifest must reject ambient interpreters" in errors
    assert "managed-host manifest must select ansible_python_interpreter" in errors
    assert "managed-host requirements must be an explicit list" in errors


def test_expected_hashes_are_named_and_content_addressed() -> None:
    hashes = artifacts.expected_input_hashes()
    assert set(hashes) == {"galaxy", "python", "system", "definition"}
    assert all(value.startswith("sha256:") and len(value) == 71 for value in hashes.values())


def test_write_lock_refreshes_input_hashes_and_base_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "runtime-lock.json"
    definition = tmp_path / "execution-environment.yml"
    image = "registry.example/ee@sha256:" + "d" * 64
    lock.write_text(
        '{"schema_version": 1, "release": "0.1.0-beta.4", '
        '"base_image": "registry.example/old@sha256:' + "a" * 64 + '", "inputs": {}}\n',
        encoding="utf-8",
    )
    definition.write_text(
        yaml.safe_dump({"images": {"base_image": {"name": image}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "LOCK", lock)
    monkeypatch.setattr(artifacts, "DEFINITION", definition)
    monkeypatch.setattr(artifacts, "expected_input_hashes", lambda: {"definition": "sha256:" + "c" * 64})
    artifacts.write_lock()
    data = json.loads(lock.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["base_image"] == image
    assert data["inputs"] == {"definition": "sha256:" + "c" * 64}


def test_write_lock_rejects_mutable_or_missing_base_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "runtime-lock.json"
    definition = tmp_path / "execution-environment.yml"
    lock.write_text('{"schema_version": 1, "inputs": {}}\n', encoding="utf-8")
    definition.write_text("images:\n  base_image:\n    name: example/ee:latest\n", encoding="utf-8")
    monkeypatch.setattr(artifacts, "LOCK", lock)
    monkeypatch.setattr(artifacts, "DEFINITION", definition)

    with pytest.raises(ValueError, match="digest-pinned"):
        artifacts.write_lock()


def test_dependency_name_parser_handles_markers_extras_and_bounds() -> None:
    assert artifacts._dependency_names(
        ["ansible-core>=2.19,<2.20; python_version < '3.12'", "package[extra]==1.0"]
    ) == {"ansible-core", "package"}


def test_build_validate_only_never_invokes_external_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda _name: pytest.fail("which must not run"))
    assert artifacts.build_environment("podman", "gludd-ee:beta4", tmp_path / "context", True) == 0


def test_build_fails_before_mutation_when_artifacts_are_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: ["stale lock"])
    assert artifacts.build_environment("podman", "gludd-ee:beta4", tmp_path / "context", False) == 1
    assert "stale lock" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("available", "expected", "message"),
    [
        ({"podman"}, 2, "ansible-builder is unavailable"),
        ({"ansible-builder"}, 2, "container runtime is unavailable"),
    ],
)
def test_build_reports_missing_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    available: set[str],
    expected: int,
    message: str,
) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(
        artifacts,
        "find_spec",
        lambda _name: object() if "ansible-builder" in available else None,
    )
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}" if name in available else None)
    assert artifacts.build_environment("podman", "gludd-ee:beta4", tmp_path / "context", False) == expected
    assert message in capsys.readouterr().err


def test_build_stops_before_collection_when_pinned_base_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(artifacts, "find_spec", lambda _name: object())
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        artifacts,
        "check_configured_base_image",
        MagicMock(side_effect=RuntimeError("manifest unknown")),
    )
    monkeypatch.setattr(
        artifacts,
        "_build_collection_artifacts",
        lambda: pytest.fail("collection builds must not start for a dead base image"),
    )

    assert artifacts.build_environment("podman", "gludd-ee:beta4", tmp_path, False) == 1
    error = capsys.readouterr().err
    assert "manifest unknown" in error
    assert "make refresh-ansible-base-image" in error


def test_build_streams_ansible_builder_with_bounded_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(artifacts, "find_spec", lambda _name: object())
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(artifacts, "check_configured_base_image", lambda: PINNED_IMAGE)
    collection_artifacts = tuple(
        (tmp_path / f"source-{index}", tmp_path / "dist" / f"collection-{index}.tar.gz")
        for index in range(3)
    )
    monkeypatch.setattr(artifacts, "COLLECTION_ARTIFACTS", collection_artifacts)

    def fake_run(command: list[str], *, cwd: Path | None = None, check: bool = False) -> SimpleNamespace:
        assert check is False
        calls.append((command, cwd))
        if command[:3] == ["ansible-galaxy", "collection", "build"]:
            index = len(calls) - 1
            collection_artifacts[index][1].write_bytes(b"collection")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    context = tmp_path / "gludd-ee"
    assert artifacts.build_environment("podman", "gludd-ee:beta4", context, False) == 0
    assert [call[0][:3] for call in calls[:3]] == [
        ["ansible-galaxy", "collection", "build"],
        ["ansible-galaxy", "collection", "build"],
        ["ansible-galaxy", "collection", "build"],
    ]
    assert calls[3][0][:4] == [sys.executable, "-m", "ansible_builder", "build"]
    assert calls[3][1] == artifacts.ROOT
    assert context.is_dir()


def test_build_fails_closed_when_collection_artifact_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "dist" / "collection.tar.gz"
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(artifacts, "check_configured_base_image", lambda: PINNED_IMAGE)
    monkeypatch.setattr(artifacts, "COLLECTION_ARTIFACTS", ((source, output),))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))

    assert artifacts.build_environment("podman", "gludd-ee:beta4", tmp_path / "context", False) == 1
    assert f"collection artifact missing or empty: {output}" in capsys.readouterr().err


@pytest.mark.parametrize("image", ["latest", "repo@sha256:short", "repo@sha256:" + "G" * 64])
def test_verify_rejects_non_digest_image(image: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert artifacts.verify_environment("podman", image, True) == 2
    assert "digest-pinned" in capsys.readouterr().err


def test_verify_validate_only_accepts_digest_without_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    assert artifacts.verify_environment("podman", PINNED_IMAGE, True) == 0


def test_verify_fails_closed_on_invalid_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: ["bad managed manifest"])
    assert artifacts.verify_environment("podman", PINNED_IMAGE, True) == 1


def test_verify_reports_missing_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert artifacts.verify_environment("podman", PINNED_IMAGE, False) == 2


def test_verify_stops_when_image_inspect_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=7))
    assert artifacts.verify_environment("podman", PINNED_IMAGE, False) == 7


def test_verify_inspects_and_smokes_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(command: list[str], *, check: bool = False) -> SimpleNamespace:
        assert check is False
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert artifacts.verify_environment("podman", PINNED_IMAGE, False) == 0
    assert calls[0] == ["podman", "image", "inspect", PINNED_IMAGE]
    assert "--network=none" in calls[1]
    assert calls[1][-2:] == ["/usr/local/bin/tofu", "version"]
    assert "--network=none" in calls[2]
    assert "ansible_runner" in calls[2][-1]


def test_verify_stops_when_opentofu_smoke_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    return_codes = iter((0, 23))
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(command: list[str], *, check: bool = False) -> SimpleNamespace:
        assert check is False
        calls.append(command)
        return SimpleNamespace(returncode=next(return_codes))

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert artifacts.verify_environment("podman", PINNED_IMAGE, False) == 23
    assert len(calls) == 2
    assert calls[-1][-2:] == ["/usr/local/bin/tofu", "version"]


def test_cli_validate_and_validate_only_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, "validate_files", lambda: [])
    assert artifacts.main(["validate"]) == 0
    assert artifacts.main(["build", "--validate-only", "--context", "/tmp/gludd-ee-test"]) == 0
    assert artifacts.main(["verify", "--validate-only", "--image", PINNED_IMAGE]) == 0


def test_cli_write_lock_and_validation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    write = MagicMock()
    monkeypatch.setattr(artifacts, "write_lock", write)
    assert artifacts.main(["write-lock"]) == 0
    write.assert_called_once_with()
    monkeypatch.setattr(artifacts, "validate_files", lambda: ["broken"])
    assert artifacts.main(["validate"]) == 1


def test_cli_refreshes_and_checks_the_base_image(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshed = MagicMock(return_value=PINNED_IMAGE)
    checked = MagicMock(return_value=PINNED_IMAGE)
    monkeypatch.setattr(artifacts, "refresh_base_image", refreshed)
    monkeypatch.setattr(artifacts, "check_configured_base_image", checked)

    assert artifacts.main(["refresh-base-image"]) == 0
    assert artifacts.main(["check-base-image"]) == 0
    refreshed.assert_called_once_with()
    checked.assert_called_once_with()


def test_base_image_refresh_make_target_is_safe_and_contract_registered() -> None:
    makefile = compose_makefile(artifacts.ROOT / "Makefile")
    target = makefile.split("refresh-ansible-base-image:", 1)[1].split("\n\n", 1)[0]
    assert "ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY" in target
    assert "refresh-base-image" in target
    assert "scripts/ansible_runtime_artifacts.py" in target

    payload = json.loads(
        (artifacts.ROOT / "config" / "make_target_contract.json").read_text(
            encoding="utf-8"
        )
    )
    contracts = {item["name"]: item for item in payload["targets"]}
    assert contracts["refresh-ansible-base-image"] == {
        "name": "refresh-ansible-base-image",
        "make_variables": ["ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY"],
        "behavior": (
            "make refresh-ansible-base-image "
            "ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY=1"
        ),
    }
    check_target = makefile.split("check-ansible-base-image:", 1)[1].split(
        "\n\n", 1
    )[0]
    assert "ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY" in check_target
    assert "check-base-image" in check_target
    assert contracts["check-ansible-base-image"] == {
        "name": "check-ansible-base-image",
        "make_variables": ["ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY"],
        "behavior": (
            "make check-ansible-base-image "
            "ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY=1"
        ),
    }
