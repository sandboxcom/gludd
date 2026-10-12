"""Unit tests for the beta4 Ansible runtime artifact tooling."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tomllib
from importlib import import_module
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
    """Exclude only the collection declaration, retaining all explicit user pins."""
    definition = yaml.safe_load(artifacts.DEFINITION.read_text(encoding="utf-8"))
    assert definition["dependencies"].get("exclude") == {"python": ["azure-identity"]}
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
    assert raw["python"]["exclude"] == ["azure-identity"]
    user_requirements = raw["python"]["user"]
    merged = builder_introspect.filter_requirements(
        raw["python"], exclude=raw["python"].pop("exclude")
    )
    expected_collection = [
        f"{line}  # from collection azure.azcollection"
        for line in _AZURE_COLLECTION_REQUIREMENTS.splitlines()
        if canonicalize_name(Requirement(line).name) != "azure-identity"
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
        "controller Python requirements must contain only pinned Azure Monitor, "
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
        "azure-identity"
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
        if canonicalize_name(Requirement(line).name) != "azure-identity"
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
