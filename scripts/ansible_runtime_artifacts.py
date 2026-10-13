#!/usr/bin/env python3
"""Validate and operate the beta4 Ansible execution-environment artifacts."""

from __future__ import annotations

import argparse
import copy
import ctypes
import errno
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from importlib import import_module, metadata
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "config" / "ansible"
CONTROLLER_PROFILE = ROOT / "requirements" / "profiles" / "ansible-controller" / "pyproject.toml"
DEFINITION = CONFIG_ROOT / "execution-environment.yml"
LOCK = CONFIG_ROOT / "runtime-lock.json"
MANAGED = CONFIG_ROOT / "managed-host-python.lock.json"
INPUTS = {
    "galaxy": CONFIG_ROOT / "requirements.yml",
    "collection_lock": CONFIG_ROOT / "collections.lock.json",
    "python": CONFIG_ROOT / "requirements.txt",
    "system": CONFIG_ROOT / "bindep.txt",
    "definition": DEFINITION,
    "smoke_settings": CONFIG_ROOT / "ee-smoke-settings.yml",
    "smoke_limiter": CONFIG_ROOT / "ee-smoke-limiter.toml",
}
CONTROLLER_PYTHON_PATH = "/opt/gludd/ansible-ee-python/bin/python3.11"
SMOKE_SETTINGS_PATH = Path("/etc/gludd/ansible-ee-smoke/settings.yml")
EXPECTED_SMOKE_SETTINGS = {"use_default_settings": {"engines": {"keep_only": ["wikipedia"]}}}
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
BASE_IMAGE_TAG = "quay.io/centos/centos:stream9"
BASE_IMAGE_MANIFEST_ROOT = "https://quay.io/v2/centos/centos/manifests"
BASE_IMAGE_MANIFEST_URL = f"{BASE_IMAGE_MANIFEST_ROOT}/stream9"
MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_COLLECTION_ROOT = ROOT / "collections" / "ansible_collections" / "general_ludd"
_COLLECTION_DIST_ROOT = ROOT / "dist" / "collections"
_COLLECTION_VERSIONS = (
    ("agent", "0.2.0"),
    ("ai_ml", "0.2.0"),
    ("azure", "0.2.0"),
    ("infrastructure", "0.2.0"),
    ("language", "0.1.0"),
    ("networking", "0.2.0"),
    ("travel", "0.1.0"),
    ("xml", "0.1.0"),
)
COLLECTION_ARTIFACTS = tuple(
    (
        _COLLECTION_ROOT / name,
        _COLLECTION_DIST_ROOT / f"general_ludd-{name}-{version}.tar.gz",
    )
    for name, version in _COLLECTION_VERSIONS
)
SEARXNG_SOURCE_REVISION = "7b4612e86250389dc9d5ee67e4cc2cd64d06602a"
SEARXNG_REQUIREMENT = (
    "searxng @ git+https://github.com/searxng/searxng.git@"
    f"{SEARXNG_SOURCE_REVISION}"
)
AZURE_IDENTITY_REQUIREMENT = "azure-identity==1.26.0"
AZURE_CLI_CORE_REQUIREMENT = "azure-cli-core==2.91.0"
AZURE_CORE_REQUIREMENT = "azure-core==1.39.0"
AZURE_MONITOR_QUERY_REQUIREMENT = "azure-monitor-query==2.0.0"
FRICTIONLESS_REQUIREMENT = "frictionless==5.19.1"
JSONPOINTER_REQUIREMENT = "jsonpointer==3.2.0"
OPENAPI_CORE_REQUIREMENT = "openapi-core==0.23.1"
SIGNXML_REQUIREMENT = "signxml==5.1.0"
EXPECTED_CONTROLLER_IMPORTS = (
    "ansible",
    "ansible_runner",
    "azure.identity",
    "azure.monitor.query",
    "frictionless",
    "jsonpointer",
    "openapi_core",
    "searx.webapp",
    "signxml",
)
EXPECTED_DEPENDENCIES: dict[str, object] = {
    "galaxy": "requirements.yml",
    "python": "requirements.txt",
    "system": "bindep.txt",
    "exclude": {"python": ["azure-identity", "azure-cli-core", "azure-core"]},
    "ansible_core": {"package_pip": "ansible-core==2.19.12"},
    "ansible_runner": {"package_pip": "ansible-runner==2.4.3"},
    "python_interpreter": {
        "package_system": "python3.11",
        "python_path": CONTROLLER_PYTHON_PATH,
    },
}
EXPECTED_BUILD_FILES = [
    {
        "src": f"../../dist/collections/{artifact.name}",
        "dest": "collections",
    }
    for _source, artifact in COLLECTION_ARTIFACTS
] + [
    {"src": "ee-smoke-settings.yml", "dest": "smoke"},
    {"src": "ee-smoke-limiter.toml", "dest": "smoke"},
]
EXPECTED_CONTROLLER_PYTHON_MARKERS = (
    "cpython-3.11.17+20261001-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
    "cpython-3.11.17+20261001-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
    "7086a336e6ea0a49595cf891066ab6517156c85116f77fbc23b62c1d9e9b7d92",
    "26ee69bff5d6d4173360275fcf38d4d25228286b79a7d9b36ad39af970909e37",
    "github.com/astral-sh/python-build-standalone/releases/download/20261001",
    "sha256sum --check --strict",
    "/usr/bin/python3.11",
    CONTROLLER_PYTHON_PATH,
)
EXPECTED_OPENTOFU_MARKERS = (
    "tofu_1.12.6_linux_amd64.zip",
    "tofu_1.12.6_linux_arm64.zip",
    "5dc43da4f750f33873dc25e94587128709e819e544b7be9016b255316153c3a8",
    "e573979ba68a17fe7b881752051a694a7efcd970e39521f6a25775197861ed4d",
    "github.com/opentofu/opentofu/releases/download/v1.12.6",
    "sha256sum --check --strict",
    "/usr/local/bin/tofu",
)
FORBIDDEN_HASHICORP_TERRAFORM_MARKERS = (
    "releases.hashicorp.com",
    "/usr/local/bin/terraform",
)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path) -> str:
    """Render repository paths compactly without rejecting external test inputs."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def expected_input_hashes() -> dict[str, str]:
    """Return content hashes for every immutable EE definition input."""
    return {name: _sha256(path) for name, path in INPUTS.items()}


def _reviewed_runtime_input_hash(name: str) -> str:
    """Read an independent expected identity from the reviewed runtime manifest."""
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    inputs = lock.get("inputs") if isinstance(lock, dict) else None
    expected = inputs.get(name) if isinstance(inputs, dict) else None
    if not isinstance(expected, str) or DIGEST_RE.fullmatch(expected) is None:
        raise ValueError(f"runtime lock must independently bind {name}")
    return expected


def required_collection_versions() -> dict[str, str]:
    """Return canonical native Galaxy pins only after reviewed-input validation."""
    if _sha256(INPUTS["galaxy"]) != _reviewed_runtime_input_hash("galaxy"):
        raise ValueError("canonical collection requirements differ from the reviewed runtime input")
    return _required_collection_versions(INPUTS["galaxy"])


def bound_collection_lock_sha256() -> str:
    """Admit canonical lock bytes using their independent reviewed expectation."""
    expected = _reviewed_runtime_input_hash("collection_lock")
    _collection_lock_entries(INPUTS["collection_lock"], expected, required_collection_versions())
    return expected


def _base_image_from_definition(definition: object) -> str:
    """Return the configured EE base image or an empty value for bad shapes."""
    if not isinstance(definition, dict):
        return ""
    images = definition.get("images")
    if not isinstance(images, dict):
        return ""
    base_image = images.get("base_image")
    if not isinstance(base_image, dict):
        return ""
    name = base_image.get("name")
    return name if isinstance(name, str) else ""


def _registry_manifest_digest(reference: str, *, opener: Any | None = None) -> str:
    """Return a registry-asserted digest for one safe CentOS manifest reference."""
    if not (reference == "stream9" or DIGEST_RE.fullmatch(reference)):
        raise ValueError("unsupported CentOS manifest reference")
    request = Request(
        f"{BASE_IMAGE_MANIFEST_ROOT}/{reference}",
        headers={"Accept": MANIFEST_ACCEPT},
        method="HEAD",
    )
    open_request = urlopen if opener is None else opener
    try:
        with open_request(request, timeout=30) as response:
            digest = str(response.headers.get("Docker-Content-Digest", ""))
    except Exception as exc:
        raise RuntimeError(
            f"unable to resolve supported Ansible base image: {exc}"
        ) from exc
    if DIGEST_RE.fullmatch(digest) is None:
        raise RuntimeError(
            "registry response has no valid Docker-Content-Digest header"
        )
    return digest


def resolve_base_image_digest(*, opener: Any | None = None) -> str:
    """Resolve the supported mutable source tag to its current manifest index."""
    digest = _registry_manifest_digest("stream9", opener=opener)
    return f"{BASE_IMAGE_TAG}@{digest}"


def check_configured_base_image(*, opener: Any | None = None) -> str:
    """Prove the exact configured immutable image is still served by Quay."""
    definition = yaml.safe_load(DEFINITION.read_text(encoding="utf-8"))
    image = _base_image_from_definition(definition)
    expected_prefix = f"{BASE_IMAGE_TAG}@"
    if not image.startswith(expected_prefix) or IMAGE_RE.fullmatch(image) is None:
        raise ValueError("execution environment must use the supported digest-pinned base image")
    expected_digest = image.removeprefix(expected_prefix)
    observed_digest = _registry_manifest_digest(expected_digest, opener=opener)
    if observed_digest != expected_digest:
        raise RuntimeError(
            "registry manifest identity mismatch: "
            f"expected {expected_digest}, received {observed_digest}"
        )
    return image


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace one tracked text artifact without exposing partial content."""
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
        temporary_name = ""
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def refresh_base_image() -> str:
    """Resolve and atomically synchronize the EE definition and runtime lock."""
    resolved = resolve_base_image_digest()
    original_definition = DEFINITION.read_text(encoding="utf-8")
    original_lock = LOCK.read_text(encoding="utf-8")
    definition = yaml.safe_load(original_definition)
    current = _base_image_from_definition(definition)
    if IMAGE_RE.fullmatch(current) is None:
        raise ValueError("execution environment base image must be digest-pinned")
    if original_definition.count(current) != 1:
        raise ValueError("execution environment base image must appear exactly once")
    try:
        _atomic_write_text(DEFINITION, original_definition.replace(current, resolved, 1))
        write_lock()
    except Exception:
        _atomic_write_text(DEFINITION, original_definition)
        _atomic_write_text(LOCK, original_lock)
        raise
    return resolved


def write_lock() -> None:
    """Refresh the immutable base identity and deterministic input hashes."""
    definition = yaml.safe_load(DEFINITION.read_text(encoding="utf-8"))
    base_image = _base_image_from_definition(definition)
    if IMAGE_RE.fullmatch(base_image) is None:
        raise ValueError("execution environment base image must be digest-pinned")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    lock["base_image"] = base_image
    lock["inputs"] = expected_input_hashes()
    LOCK.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def _dependency_names(requirements: list[str]) -> set[str]:
    names: set[str] = set()
    for requirement in requirements:
        name = re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0]
        names.add(name.strip().lower())
    return names


def validate_files() -> list[str]:
    """Return all runtime-boundary artifact validation errors."""
    errors: list[str] = []
    missing = [
        _display_path(path)
        for path in (*INPUTS.values(), LOCK, MANAGED, CONTROLLER_PROFILE)
        if not path.is_file()
    ]
    if missing:
        return [f"missing runtime artifact: {path}" for path in missing]

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_names = _dependency_names(project["project"]["dependencies"])
    for forbidden in ("ansible-core", "ansible-runner", "ansible-builder"):
        if forbidden in runtime_names:
            errors.append(f"core dependency leak: {forbidden}")
    controller_project = tomllib.loads(
        CONTROLLER_PROFILE.read_text(encoding="utf-8")
    )
    controller = controller_project["project"]["dependencies"]
    controller_names = _dependency_names(controller)
    for required in (
        "ansible-core",
        "ansible-runner",
        "azure-identity",
        "azure-monitor-query",
        "jsonpointer",
        "openapi-core",
        "signxml",
    ):
        if required not in controller_names:
            errors.append(f"missing optional controller dependency: {required}")
    python_requirements = INPUTS["python"].read_text(encoding="utf-8").splitlines()
    pinned_requirements = [
        line.strip()
        for line in python_requirements
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if pinned_requirements != [
        AZURE_IDENTITY_REQUIREMENT,
        AZURE_CLI_CORE_REQUIREMENT,
        AZURE_CORE_REQUIREMENT,
        AZURE_MONITOR_QUERY_REQUIREMENT,
        FRICTIONLESS_REQUIREMENT,
        JSONPOINTER_REQUIREMENT,
        OPENAPI_CORE_REQUIREMENT,
        SIGNXML_REQUIREMENT,
        SEARXNG_REQUIREMENT,
    ]:
        errors.append(
            "controller Python requirements must contain only pinned Azure Identity, "
            "CLI Core, Azure Core, Azure Monitor, "
            "Frictionless, jsonpointer, openapi-core, SignXML, and official SearXNG"
        )

    definition: dict[str, Any] = yaml.safe_load(DEFINITION.read_text(encoding="utf-8"))
    base_image = _base_image_from_definition(definition)
    if IMAGE_RE.fullmatch(base_image) is None:
        errors.append("execution environment base image is not digest-pinned")
    if definition.get("version") != 3:
        errors.append("execution environment definition must use schema version 3")
    if definition.get("dependencies") != EXPECTED_DEPENDENCIES:
        errors.append("execution environment dependencies must name the locked inputs and controller interpreter")
    if definition.get("additional_build_files") != EXPECTED_BUILD_FILES:
        errors.append("execution environment must stage the exact locked collection artifacts and smoke configuration")
    build_steps = definition.get("additional_build_steps", {})
    prepend_base = build_steps.get("prepend_base", []) if isinstance(build_steps, dict) else []
    rendered_bootstrap = "\n".join(step for step in prepend_base if isinstance(step, str))
    if any(marker not in rendered_bootstrap for marker in EXPECTED_CONTROLLER_PYTHON_MARKERS):
        errors.append("execution environment must bootstrap the exact checksum-pinned native controller Python")
    if yaml.safe_load(INPUTS["smoke_settings"].read_text(encoding="utf-8")) != EXPECTED_SMOKE_SETTINGS:
        errors.append("execution environment smoke settings must use only the native Wikipedia engine")
    try:
        with INPUTS["smoke_limiter"].open("rb") as handle:
            limiter = tomllib.load(handle)
    except tomllib.TOMLDecodeError:
        errors.append("execution environment smoke limiter configuration is invalid TOML")
    else:
        if limiter != {"botdetection": {}}:
            errors.append("execution environment smoke limiter must inherit pinned native defaults")
    append_final = (
        build_steps.get("append_final", [])
        if isinstance(build_steps, dict)
        else []
    )
    rendered_steps = "\n".join(
        step for step in append_final if isinstance(step, str)
    )
    if not append_final or any(
        marker not in rendered_steps for marker in EXPECTED_OPENTOFU_MARKERS
    ):
        errors.append(
            "execution environment must install the checksum-pinned OpenTofu CLI"
        )
    if any(
        marker in rendered_steps for marker in FORBIDDEN_HASHICORP_TERRAFORM_MARKERS
    ):
        errors.append(
            "execution environment must not install HashiCorp Terraform"
        )

    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1 or lock.get("release") != "0.1.0-beta.4":
        errors.append("runtime lock schema/release mismatch")
    if lock.get("base_image") != base_image:
        errors.append("runtime lock base image differs from execution environment definition")
    if lock.get("inputs") != expected_input_hashes():
        errors.append("runtime lock input hashes are stale; run make update-ansible-runtime-lock")
    try:
        bound_collection_lock_sha256()
    except (OSError, ValueError) as exc:
        errors.append(str(exc))

    managed = json.loads(MANAGED.read_text(encoding="utf-8"))
    if managed.get("ambient_interpreters_allowed") is not False:
        errors.append("managed-host manifest must reject ambient interpreters")
    if managed.get("interpreter_variable") != "ansible_python_interpreter":
        errors.append("managed-host manifest must select ansible_python_interpreter")
    if not isinstance(managed.get("requirements"), list):
        errors.append("managed-host requirements must be an explicit list")
    return errors


def _validate_collection_dependencies(
    inventory: Mapping[str, tuple[str, dict[str, str]]], required: Mapping[str, str],
) -> None:
    """Use Galaxy's version owner for both archive and installed closure admission."""
    from ansible.galaxy.dependency_resolution.versioning import meets_requirements

    for name, version in required.items():
        if name not in inventory:
            raise ValueError(f"missing staged collection manifest: {name}")
        if inventory[name][0] != version:
            raise ValueError(f"staged collection version mismatch: {name}")
    for name, (_release, dependencies) in inventory.items():
        for dependency, constraint in dependencies.items():
            if dependency not in inventory:
                raise ValueError(f"missing staged collection manifest: {dependency} (required by {name})")
            if not meets_requirements(inventory[dependency][0], constraint):
                raise ValueError(f"staged collection dependency mismatch: {name} -> {dependency}")


def _staged_collection_metadata(
    collection_root: Path, *, required: Mapping[str, str] | None = None, exact: bool = False,
) -> None:
    """Reject partial staged inputs using Galaxy's own dependency version rules."""
    staged = collection_root / "ansible_collections"
    if not staged.is_dir():
        raise ValueError(f"partial collection inputs: missing staged tree: {staged}")
    if required is None:
        galaxy = yaml.safe_load(INPUTS["galaxy"].read_text(encoding="utf-8"))
        required = {
            entry["name"]: entry["version"]
            for entry in galaxy["collections"]
            if entry.get("type") != "file"
        }
        required.update({f"general_ludd.{name}": release for name, release in _COLLECTION_VERSIONS})
    for name in sorted(required):
        if not (staged.joinpath(*name.split(".")) / "MANIFEST.json").is_file():
            raise ValueError(f"missing staged collection manifest: {name}")
    inventory: dict[str, tuple[str, dict[str, str]]] = {}
    for collection in sorted(staged.glob("*/*")):
        if not collection.is_dir():
            continue
        name = f"{collection.parent.name}.{collection.name}"
        if not collection.resolve().is_relative_to(staged.resolve()):
            raise ValueError(f"staged collection escapes the supplied tree: {name}")
        manifest = collection / "MANIFEST.json"
        if not manifest.is_file():
            raise ValueError(f"missing staged collection manifest: {name}")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid staged collection manifest: {name}") from exc
        info = data.get("collection_info") if isinstance(data, dict) else None
        if not isinstance(info, dict) or any(
            not isinstance(info.get(field), str) for field in ("namespace", "name", "version")
        ):
            raise ValueError(f"invalid staged collection manifest: {name}")
        if f"{info['namespace']}.{info['name']}" != name:
            raise ValueError(f"staged collection identity mismatch: {name}")
        release = info["version"]
        if name in required and release != required[name]:
            raise ValueError(f"staged collection version mismatch: {name}")
        dependencies = info.get("dependencies")
        if not isinstance(dependencies, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in dependencies.items()
        ):
            raise ValueError(f"invalid staged collection dependency metadata: {name}")
        inventory[name] = (release, dependencies)
        metadata = collection / "meta" / "execution-environment.yml"
        if not metadata.is_file():
            continue
        definition = yaml.safe_load(metadata.read_text(encoding="utf-8"))
        declared = definition.get("dependencies", {}) if isinstance(definition, dict) else None
        if not isinstance(declared, dict):
            raise ValueError(f"invalid staged collection execution metadata: {name}")
        for kind in ("python", "system"):
            if kind not in declared:
                continue
            relative = declared[kind]
            if not isinstance(relative, str) or not relative:
                raise ValueError(f"invalid declared collection {kind} requirements: {name}")
            dependency_file = collection / relative
            if not dependency_file.resolve().is_relative_to(collection.resolve()):
                raise ValueError(f"declared collection requirements escape the collection: {name}")
            if not dependency_file.is_file():
                label = "Python" if kind == "python" else "system"
                raise ValueError(f"missing declared collection {label} requirements: {name}")
    _validate_collection_dependencies(inventory, required)
    if exact and set(inventory) != set(required):
        raise ValueError("staged collection inventory differs from the exact artifact closure")


def _collection_origin(origin: object) -> str:
    """Require an explicit credential-free HTTPS Galaxy origin."""
    if not isinstance(origin, str):
        raise ValueError("collection artifact origin must be an HTTPS Galaxy URL")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or not origin.endswith("/")):
        raise ValueError("collection artifact origin must be a credential-free HTTPS Galaxy URL ending in /")
    return origin


def _collection_versions(required: Mapping[str, str]) -> dict[str, str]:
    """Require real FQCNs and exact native semantic versions, never ranges/latest."""
    from ansible.utils.collection_loader import AnsibleCollectionRef
    from ansible.utils.version import SemanticVersion

    if not required:
        raise ValueError("required collection versions must not be empty")
    for name, version in required.items():
        if not isinstance(name, str) or not AnsibleCollectionRef.is_valid_collection_name(name):
            raise ValueError("required collection identity must be a native FQCN")
        if not isinstance(version, str) or not version:
            raise ValueError(f"required collection version must be exact: {name}")
        SemanticVersion(version)
    return dict(required)


class CollectionArtifactEntry(TypedDict):
    """One exact reviewed native collection artifact after runtime validation."""

    name: str
    version: str
    artifact: str
    origin: str
    sha256: str
    size: int


def _collection_lock_entries(
    lock_file: Path, expected_lock_sha256: str, required: Mapping[str, str],
) -> list[CollectionArtifactEntry]:
    """Admit an independently bound reviewed lock, with no default identities."""
    if (not isinstance(expected_lock_sha256, str) or DIGEST_RE.fullmatch(expected_lock_sha256) is None
            or lock_file.is_symlink() or not lock_file.is_file()):
        raise ValueError("collection lock digest does not match the reviewed input")
    content = lock_file.read_bytes()
    if "sha256:" + hashlib.sha256(content).hexdigest() != expected_lock_sha256:
        raise ValueError("collection lock digest does not match the reviewed input")
    document = json.loads(content)
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1):
        raise ValueError("collection lock schema must be 1")
    entries = document.get("collections")
    if not isinstance(entries, list) or not entries:
        raise ValueError("collection artifact closure must be nonempty")
    versions: dict[str, str] = {}
    filenames: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "version", "artifact", "origin", "sha256", "size"}:
            raise ValueError("collection artifact lock entry has missing or unknown fields")
        name, version, filename = entry["name"], entry["version"], entry["artifact"]
        _collection_versions({name: version})
        if (name in versions or not isinstance(filename, str) or Path(filename).name != filename
                or not filename.endswith(".tar.gz") or filename in filenames):
            raise ValueError("collection artifact identities and safe filenames must be unique")
        if (not isinstance(entry["sha256"], str) or DIGEST_RE.fullmatch(entry["sha256"]) is None
                or type(entry["size"]) is not int or entry["size"] <= 0):
            raise ValueError("collection artifact digest and positive size are required")
        _collection_origin(entry["origin"])
        versions[name] = version
        filenames.add(filename)
    for name, version in _collection_versions(required).items():
        if versions.get(name) != version:
            raise ValueError(f"missing required exact collection version: {name}:{version}")
    return cast(list[CollectionArtifactEntry], entries)


def _collection_environment(state: Path) -> dict[str, str]:
    """Refuse ambient resolution, then isolate every native Galaxy writable path."""
    inherited = os.environ.get("ANSIBLE_COLLECTIONS_PATH", "")
    if inherited and inherited != str(ROOT / "collections"):
        raise ValueError("ambient ANSIBLE_COLLECTIONS_PATH is not admitted")
    scan = os.environ.get("ANSIBLE_COLLECTIONS_SCAN_SYS_PATH", "false").lower()
    if scan not in {"false", "0", "no"} or os.environ.get("ANSIBLE_CONFIG"):
        raise ValueError("ambient Ansible configuration or sys.path discovery is not admitted")
    private = {name: value for name, value in os.environ.items() if not name.startswith("ANSIBLE_")}
    for name in ("tmp", "home", "response-cache", "plugins", "roles", "collections"):
        (state / name).mkdir()
    configuration = state / "ansible.cfg"
    configuration.write_text("[defaults]\n", encoding="utf-8")
    private.update(
        ANSIBLE_CONFIG=str(configuration), ANSIBLE_HOME=str(state / "home"),
        ANSIBLE_LOCAL_TEMP=str(state / "tmp"), ANSIBLE_GALAXY_CACHE_DIR=str(state / "response-cache"),
        ANSIBLE_COLLECTIONS_PATH=str(state / "collections"), ANSIBLE_COLLECTIONS_SCAN_SYS_PATH="false",
        ANSIBLE_LIBRARY=str(state / "plugins"), ANSIBLE_ACTION_PLUGINS=str(state / "plugins"),
        ANSIBLE_FILTER_PLUGINS=str(state / "plugins"), ANSIBLE_ROLES_PATH=str(state / "roles"),
        ANSIBLE_NOCOLOR="true", PYTHONWARNINGS="error", PYTHON_CPU_COUNT="1", TMPDIR=str(state / "tmp"),
    )
    return private


def _run_collection_command(
    command: list[str], log: Path, *, max_seconds: int, environment: Mapping[str, str], cwd: Path,
) -> int:
    """Use the existing observer's bounded process group and teardown owner."""
    stream = import_module("scripts.stream_command").stream_command
    return cast(int, stream(command, log, observed_root=log.parent / "observed",
                            label="gludd-collection-stage", heartbeat_seconds=10, max_seconds=max_seconds,
                            environment=environment, cwd=cwd))


def _checked_collection_command(
    command: list[str], state: Path, timeout: int, *, environment: Mapping[str, str], cwd: Path,
) -> None:
    """Reject native exit/diagnostics without changing logging, rules or warnings."""
    if TYPE_CHECKING:
        from check_molecule_yaml import validate_native_lint_diagnostics
    else:
        from scripts.check_molecule_yaml import validate_native_lint_diagnostics

    log = state / "galaxy.log"
    result = _run_collection_command(command, log, max_seconds=timeout, environment=environment, cwd=cwd)
    if result != 0 or not validate_native_lint_diagnostics(log):
        raise ValueError(f"native Galaxy collection command failed: rc={result}")


def _collection_archive_inventory(
    state: Path, entries: list[CollectionArtifactEntry], artifact_root: Path,
    *, controller_version: str | None = None,
) -> dict[str, tuple[str, dict[str, str]]]:
    """Read authentic tar metadata through Galaxy's own artifact manager."""
    from ansible.galaxy.collection.concrete_artifact_manager import ConcreteArtifactsManager
    from ansible.galaxy.dependency_resolution.dataclasses import Candidate
    from ansible.release import __version__ as core_version

    core_version = controller_version or core_version

    inventory: dict[str, tuple[str, dict[str, str]]] = {}
    with ConcreteArtifactsManager.under_tmpdir(str(state)) as manager:
        for entry in entries:
            path = artifact_root / entry["artifact"]
            if path.is_symlink() or not path.is_file() or path.resolve().parent != artifact_root.resolve():
                raise ValueError(f"collection artifact is missing or unsafe: {entry['name']}")
            with path.open("rb") as source:
                actual_hash = "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()
            if path.stat().st_size != entry["size"] or actual_hash != entry["sha256"]:
                raise ValueError(f"collection artifact checksum or size differs: {entry['name']}")
            candidate = Candidate(entry["name"], entry["version"], os.fsencode(path), "file", None)
            info = manager.get_direct_collection_meta(candidate)
            if (not isinstance(info, dict) or info.get("namespace") != entry["name"].split(".")[0]
                    or info.get("name") != entry["name"].split(".")[1]
                    or info.get("version") != entry["version"]):
                raise ValueError(f"native artifact identity differs: {entry['name']}")
            dependencies = info.get("dependencies")
            if not isinstance(dependencies, dict) or any(
                not isinstance(name, str) or not isinstance(constraint, str)
                for name, constraint in dependencies.items()
            ):
                raise ValueError(f"native artifact dependency metadata is invalid: {entry['name']}")
            if hasattr(manager, "get_direct_requires_ansible"):
                requires = manager.get_direct_requires_ansible(candidate)
            else:
                # Core 2.19's native manager predates this accessor. Use maintained
                # tarfile/YAML/packaging owners solely for its runtime metadata.
                with tarfile.open(path) as archive:
                    try:
                        member = archive.extractfile("meta/runtime.yml")
                    except KeyError:
                        member = None
                    if member is None:
                        runtime = None
                    else:
                        with member:
                            runtime = yaml.safe_load(member)
                requires = runtime.get("requires_ansible") if isinstance(runtime, dict) else None
            if not isinstance(requires, str) or not requires or not SpecifierSet(requires).contains(
                core_version, prereleases=True,
            ):
                raise ValueError(f"native artifact requires_ansible is missing or unsupported: {entry['name']}")
            inventory[entry["name"]] = (entry["version"], dependencies)
    return inventory


@contextmanager
def staged_collection_closure(
    lock_file: Path, artifact_root: Path, *, expected_lock_sha256: str,
    required_versions: Mapping[str, str], timeout_seconds: int = 120,
) -> Iterator[Path]:
    """Install only the reviewed closure privately, offline, and clean it on exit."""
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
        raise ValueError("collection timeout must be an integer from 1 through 120")
    required = _collection_versions(required_versions)
    entries = _collection_lock_entries(lock_file, expected_lock_sha256, required)
    lock_file, artifact_root = lock_file.absolute(), artifact_root.absolute()
    project = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    with tempfile.TemporaryDirectory(prefix=f"gludd-collection-stage-{project}-") as temporary:
        state = Path(temporary)
        private = _collection_environment(state)
        copied = state / "artifacts"
        copied.mkdir()
        for entry in entries:
            source = artifact_root / entry["artifact"]
            if source.is_symlink() or not source.is_file() or artifact_root.is_symlink():
                raise ValueError(f"collection artifact is missing or unsafe: {entry['name']}")
            shutil.copyfile(source, copied / entry["artifact"])
        inventory = _collection_archive_inventory(state, entries, copied)
        exact = {entry["name"]: entry["version"] for entry in entries}
        _validate_collection_dependencies(inventory, exact)
        requirements = state / "offline-requirements.json"
        requirements.write_text(json.dumps({"collections": [
            {"name": str(copied / entry["artifact"]), "type": "file", "version": entry["version"]}
            for entry in entries
        ]}), encoding="utf-8")
        stage = state / "collections"
        command = [sys.executable, "-m", "ansible.cli.galaxy", "collection", "install",
                   "--offline", "--no-cache", "--requirements-file", str(requirements),
                   "--collections-path", str(stage)]
        _checked_collection_command(command, state, timeout_seconds, environment=private, cwd=state)
        _staged_collection_metadata(stage, required=exact, exact=True)
        yield stage


def _downloaded_collection_entries(state: Path, downloaded: Path, origin: str) -> list[CollectionArtifactEntry]:
    """Consume Galaxy's own generated offline requirements and native metadata."""
    from ansible.galaxy.collection.concrete_artifact_manager import ConcreteArtifactsManager
    from ansible.galaxy.dependency_resolution.dataclasses import Requirement as GalaxyRequirement

    document = yaml.safe_load((downloaded / "requirements.yml").read_text(encoding="utf-8"))
    declared = document.get("collections") if isinstance(document, dict) else None
    if not isinstance(declared, list) or not declared:
        raise ValueError("native Galaxy download produced no complete artifact requirements")
    entries: list[CollectionArtifactEntry] = []
    with ConcreteArtifactsManager.under_tmpdir(str(state)) as manager:
        for entry in declared:
            if (not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
                    or Path(entry["name"]).name != entry["name"] or not entry["name"].endswith(".tar.gz")):
                raise ValueError("native Galaxy download produced an unsafe artifact reference")
            path = downloaded / entry["name"]
            if path.is_symlink() or not path.is_file():
                raise ValueError("native Galaxy download produced a missing or unsafe artifact")
            candidate = GalaxyRequirement.from_requirement_dict({"name": str(path), "type": "file"}, manager)
            info = manager.get_direct_collection_meta(candidate)
            name = f"{info['namespace']}.{info['name']}"
            _collection_versions({name: info["version"]})
            if entry.get("version") != info["version"]:
                raise ValueError("native Galaxy download version differs from the artifact manifest")
            with path.open("rb") as source:
                sha256 = "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()
            entries.append({"name": name, "version": info["version"], "artifact": path.name,
                            "origin": origin, "sha256": sha256, "size": path.stat().st_size})
    return entries


def _rename_collection_generation_no_replace(source: Path, destination: Path) -> None:
    """Delegate one atomic exclusive directory rename to the operating system."""
    if sys.platform == "win32":
        # Windows os.rename never replaces an existing destination.
        os.rename(source, destination)
        return
    native = ctypes.CDLL(None, use_errno=True)
    try:
        if sys.platform == "darwin":
            rename = native.renamex_np
            rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            parameters = (os.fsencode(source.absolute()), os.fsencode(destination.absolute()), 0x00000004)
        elif sys.platform.startswith("linux"):
            rename = native.renameat2
            rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            parameters = (-100, os.fsencode(source.absolute()), -100, os.fsencode(destination.absolute()), 1)
        else:
            raise OSError(errno.ENOTSUP, "native exclusive directory rename is unsupported")
    except AttributeError as exc:
        raise OSError(errno.ENOTSUP, "native exclusive directory rename is unavailable") from exc
    rename.restype = ctypes.c_int
    if rename(*parameters) != 0:
        failure = ctypes.get_errno()
        raise OSError(failure, os.strerror(failure), str(destination))


def _publish_collection_generation(
    downloaded: Path, entries: list[CollectionArtifactEntry], destination: Path, lock_bytes: bytes,
) -> None:
    """Publish a new complete generation atomically, never replace an active tree."""
    if destination.exists() or destination.is_symlink():
        raise ValueError("collection artifact generation already exists; choose a new owned output")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gludd-collection-generation-", dir=destination.parent) as temporary:
        prepared = Path(temporary) / "generation"
        prepared.mkdir()
        for entry in entries:
            shutil.copyfile(downloaded / entry["artifact"], prepared / entry["artifact"])
        (prepared / "collections.lock.json").write_bytes(lock_bytes)
        (prepared / "requirements.yml").write_text(json.dumps({"collections": [
            {"name": entry["artifact"], "version": entry["version"], "type": "file"} for entry in entries
        ]}), encoding="utf-8")
        if destination.exists() or destination.is_symlink():
            raise ValueError("collection artifact generation was acquired by another owner")
        _rename_collection_generation_no_replace(prepared, destination)


def prepare_collection_artifacts(
    mode: str, lock_file: Path, artifact_root: Path, *, required_versions: Mapping[str, str],
    origin: str, expected_lock_sha256: str = "", timeout_seconds: int = 120, validate_only: bool = False,
) -> int:
    """Run bounded native refresh/acquisition or a private offline closure replay."""
    try:
        if mode not in {"refresh", "acquire", "offline"}:
            raise ValueError("collection stage mode must be refresh, acquire or offline")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise ValueError("collection timeout must be an integer from 1 through 120")
        required, source = _collection_versions(required_versions), _collection_origin(origin)
        if validate_only:
            print(f"ANSIBLE_COLLECTION_STAGE_PLAN mode={mode} lock={lock_file} artifacts={artifact_root} "
                  f"timeout={timeout_seconds} source={source} proof=none")
            return 0
        if mode == "offline":
            with staged_collection_closure(lock_file, artifact_root, expected_lock_sha256=expected_lock_sha256,
                                           required_versions=required, timeout_seconds=timeout_seconds):
                print("ANSIBLE_COLLECTION_STAGE_REPLAY_PASS proof=collection-metadata-only")
            return 0
        if artifact_root.exists() or artifact_root.is_symlink():
            raise ValueError("collection acquisition requires a new artifact generation output")
        reviewed: list[CollectionArtifactEntry] | None = None
        if mode == "acquire":
            reviewed = _collection_lock_entries(lock_file, expected_lock_sha256, required)
            if any(entry["origin"] != source for entry in reviewed):
                raise ValueError("reviewed artifact origin differs from the explicit Galaxy source")
            requested = {entry["name"]: entry["version"] for entry in reviewed}
        else:
            if lock_file != artifact_root / "collections.lock.json":
                raise ValueError("refresh lock must be inside the new candidate artifact generation")
            requested = required
        lock_file, artifact_root = lock_file.absolute(), artifact_root.absolute()
        project = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
        with tempfile.TemporaryDirectory(prefix=f"gludd-collection-acquire-{project}-") as temporary:
            state = Path(temporary)
            private = _collection_environment(state)
            downloaded = state / "artifacts"
            downloaded.mkdir()
            requirements = state / "galaxy-requirements.json"
            requirements.write_text(json.dumps({"collections": [
                {"name": name, "version": version, "type": "galaxy", "source": source}
                for name, version in sorted(requested.items())
            ]}), encoding="utf-8")
            command = [sys.executable, "-m", "ansible.cli.galaxy", "collection", "download", "--no-cache",
                       "--requirements-file", str(requirements), "--download-path", str(downloaded),
                       "--server", source, "--timeout", str(min(timeout_seconds, 30))]
            if reviewed is not None:
                command.append("--no-deps")
            _checked_collection_command(command, state, timeout_seconds, environment=private, cwd=state)
            entries = (reviewed if reviewed is not None else
                       _downloaded_collection_entries(state, downloaded, source))
            lock_bytes = (lock_file.read_bytes() if reviewed is not None else
                          (json.dumps({"schema_version": 1, "collections": entries}, indent=2) + "\n").encode())
            if reviewed is not None and "sha256:" + hashlib.sha256(lock_bytes).hexdigest() != expected_lock_sha256:
                raise ValueError("reviewed collection lock digest changed during acquisition")
            candidate_lock = state / "collections.lock.json"
            candidate_lock.write_bytes(lock_bytes)
            entries = _collection_lock_entries(candidate_lock, _sha256(candidate_lock), required)
            inventory = _collection_archive_inventory(state, entries, downloaded)
            _validate_collection_dependencies(inventory, {entry["name"]: entry["version"] for entry in entries})
            _publish_collection_generation(downloaded, entries, artifact_root, lock_bytes)
        print(f"ANSIBLE_COLLECTION_ARTIFACTS_READY mode={mode} path={artifact_root} "
              f"lock={_sha256(artifact_root / 'collections.lock.json')} proof=artifact-closure-only")
        return 0
    except Exception as exc:
        print(f"ANSIBLE_COLLECTION_STAGE_FAILED: {exc} proof=none", file=sys.stderr)
        return 1


def _required_collection_versions(path: Path) -> dict[str, str]:
    """Retain supplied direct Galaxy pins while local tarballs keep their EE owner."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = document.get("collections") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise ValueError("collection requirements must declare a collections list")
    required: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type", "galaxy") not in {"galaxy", "file"}:
            raise ValueError("collection requirements must contain only explicit Galaxy pins or owned local files")
        if entry.get("type") == "file":
            continue
        name, version = entry.get("name"), entry.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or name in required:
            raise ValueError("collection requirements must declare unique exact versions")
        required[name] = version
    return _collection_versions(required)


def _resolution_build_configuration(definition: dict[str, Any]) -> str:
    """Reuse the exact package-specific isolated build configuration from the EE."""
    steps = definition["additional_build_steps"]["append_base"]
    configurations = [step for step in steps if "[pip.extra-build-dependencies]" in step]
    if len(configurations) != 1:
        raise ValueError("execution environment must declare one isolated build configuration")
    tokens = shlex.split(configurations[0].removeprefix("RUN "))
    configuration = "\n".join(tokens[tokens.index("printf") + 2:tokens.index(">")]) + "\n"
    if tomllib.loads(configuration) != {
        "pip": {"extra-build-dependencies": {"searxng": ["pyyaml==6.0.2"]}}
    }:
        raise ValueError("execution environment isolated build configuration differs from frozen SearXNG")
    return configuration


def _check_resolution_tool_identities(uv: str, definition: dict[str, Any]) -> bool:
    """Require existing tools to match the exact EE and controller lock identities."""
    try:
        uv_requirements = [
            Requirement(token)
            for step in definition["additional_build_steps"]["append_base"]
            for token in shlex.split(step.removeprefix("RUN "))
            if token.startswith("uv==")
        ]
        if len(uv_requirements) != 1:
            raise ValueError("execution environment must pin exactly one uv compiler")
        uv_specifiers = list(uv_requirements[0].specifier)
        if len(uv_specifiers) != 1 or uv_specifiers[0].operator != "==" or "*" in uv_specifiers[0].version:
            raise ValueError("execution environment must pin one exact uv compiler version")
        expected_uv = uv_specifiers[0].version
        controller_lock = tomllib.loads(CONTROLLER_PROFILE.with_name("uv.lock").read_text(encoding="utf-8"))
        builder_versions = [
            package["version"] for package in controller_lock["package"]
            if package["name"] == "ansible-builder"
        ]
        if len(builder_versions) != 1 or not isinstance(builder_versions[0], str):
            raise ValueError("controller lock must pin exactly one ansible-builder version")
        expected_builder = builder_versions[0]
        try:
            probe = subprocess.run(
                [uv, "--version"], cwd=ROOT, check=False, capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"uv version probe failed: {exc}") from exc
        if probe.returncode != 0:
            details = "\n".join(
                stream.strip() for stream in (probe.stdout, getattr(probe, "stderr", "")) if stream.strip()
            )
            raise RuntimeError(f"uv version probe failed: rc={probe.returncode}\n{details}")
        response = re.fullmatch(r"uv ([^\s]+)(?:[ \t]+\([^\r\n]+\))?", probe.stdout.strip())
        if response is None:
            raise ValueError(f"invalid uv version response: {probe.stdout!r}")
        observed_uv = response.group(1)
        if observed_uv != expected_uv:
            raise ValueError(f"resolver tool identity mismatch: uv required={expected_uv} observed={observed_uv}")
        observed_builder = metadata.version("ansible-builder")
        if observed_builder != expected_builder:
            raise ValueError(
                "resolver tool identity mismatch: "
                f"ansible-builder required={expected_builder} observed={observed_builder}"
            )
    except metadata.PackageNotFoundError:
        print("ansible-builder distribution metadata is unavailable", file=sys.stderr)
        return False
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f"Ansible EE resolver tool identity validation failed: {exc}", file=sys.stderr)
        return False
    print(
        f"ANSIBLE_EE_RESOLVER_TOOLS uv={observed_uv} ansible_builder={observed_builder} executable={uv}",
        flush=True,
    )
    return True


def resolve_requirements(collection_root: Path, output: Path, validate_only: bool) -> int:
    """Compile every exact Builder-staged requirement without installing a runtime."""
    errors = validate_files()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if validate_only:
        print(
            f"ANSIBLE_EE_REQUIREMENTS_RESOLVE_PLAN collections={collection_root} "
            f"output={output} python=3.11 platform=x86_64-manylinux_2_28 max_seconds=180"
        )
        return 0
    if find_spec("ansible_builder") is None:
        print("ansible-builder is unavailable; sync the controller dependencies", file=sys.stderr)
        return 2
    uv = shutil.which("uv")
    if uv is None:
        print("uv is unavailable; use the locked controller compiler", file=sys.stderr)
        return 2
    try:
        _staged_collection_metadata(collection_root)
        definition = yaml.safe_load(DEFINITION.read_text(encoding="utf-8"))
        if not _check_resolution_tool_identities(uv, definition):
            return 2
        configuration = _resolution_build_configuration(definition)
        introspect = import_module("ansible_builder._target_scripts.introspect")
        with tempfile.TemporaryDirectory(prefix="gludd-ee-resolution-") as temporary:
            work = Path(temporary)
            exclusion = work / "exclude-requirements.txt"
            exclusion.write_text(
                "\n".join(definition["dependencies"]["exclude"]["python"]) + "\n",
                encoding="utf-8",
            )
            raw = introspect.process(
                data_dir=str(collection_root), user_pip=str(INPUTS["python"]),
                exclude_pip=str(exclusion),
            )
            combined = introspect.filter_requirements(
                raw["python"], exclude=raw["python"].pop("exclude")
            )
            for owner in ("ansible_core", "ansible_runner"):
                combined.append(definition["dependencies"][owner]["package_pip"])
            requirements = work / "combined-requirements.txt"
            requirements.write_text("\n".join(combined) + "\n", encoding="utf-8")
            config = work / "uv.toml"
            config.write_text(configuration, encoding="utf-8")
            resolved = work / "resolved.txt"
            command = [
                uv, "pip", "compile", str(requirements),
                "--python-version", "3.11", "--python-platform", "x86_64-manylinux_2_28",
                "--config-file", str(config), "--no-python-downloads",
                "--output-file", str(resolved),
            ]
            observed = [
                sys.executable, str(ROOT / "scripts" / "stream_command.py"),
                "--root", str(ROOT / ".gate-logs" / "observed"),
                "--label", "ansible-ee-requirements", "--run-id", work.name,
                "--heartbeat-secs", "15", "--quiet-secs", "60", "--max-secs", "180",
                "--retain-runs", "20", "--", *command,
            ]
            environment = dict(os.environ)
            environment.update({
                "UV_CONCURRENT_BUILDS": "1", "UV_CONCURRENT_DOWNLOADS": "4",
                "UV_HTTP_TIMEOUT": "30", "UV_HTTP_RETRIES": "0",
                "UV_PYTHON_DOWNLOADS": "never", "UV_LINK_MODE": "copy",
            })
            print(f"ANSIBLE_EE_REQUIREMENTS_RESOLVE_START collections={collection_root}", flush=True)
            result = subprocess.run(observed, cwd=ROOT, env=environment, check=False)
            if result.returncode != 0:
                print(f"ANSIBLE_EE_REQUIREMENTS_RESOLVE_END rc={result.returncode}", flush=True)
                return result.returncode
            if not resolved.is_file() or resolved.stat().st_size == 0:
                raise ValueError("uv produced missing or empty resolved requirements")
            output.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(output, resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"Ansible EE requirements resolution failed: {exc}", file=sys.stderr)
        return 1
    print(f"ANSIBLE_EE_REQUIREMENTS_RESOLVE_END rc=0 output={output}", flush=True)
    return 0


def _require_image(image: str) -> None:
    if IMAGE_RE.fullmatch(image) is None:
        raise ValueError("execution-environment image must be digest-pinned as name@sha256:<64 lowercase hex>")


def _build_collection_artifacts() -> int:
    """Build and verify the exact Galaxy artifacts consumed by the EE."""
    if shutil.which("ansible-galaxy") is None:
        print("ansible-galaxy is unavailable; sync the controller dependencies", file=sys.stderr)
        return 2
    for source, artifact in COLLECTION_ARTIFACTS:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ansible-galaxy",
            "collection",
            "build",
            str(source),
            "--output-path",
            str(artifact.parent),
            "--force",
        ]
        print(f"ANSIBLE_COLLECTION_BUILD_START source={_display_path(source)}", flush=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode != 0:
            print(f"ANSIBLE_COLLECTION_BUILD_END rc={result.returncode}", flush=True)
            return result.returncode
        if not artifact.is_file() or artifact.stat().st_size == 0:
            print(f"collection artifact missing or empty: {artifact}", file=sys.stderr)
            return 1
        print(f"ANSIBLE_COLLECTION_BUILD_END rc=0 artifact={_display_path(artifact)}", flush=True)
    return 0


def _read_owned_native_result(path: Path, limit: int, label: str) -> bytes:
    """Read bounded native-result bytes through one owned descriptor."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_uid != os.getuid()
            or identity.st_nlink != 1
            or not 0 < identity.st_size <= limit
        ):
            raise ValueError(f"{label} must be a bounded owned regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(limit + 1)
        if len(payload) > limit:
            raise ValueError(f"{label} grew beyond its admitted bound")
        return payload
    finally:
        os.close(descriptor)


def _built_image_id(path: Path) -> str:
    """Read one bounded, owned builder result without following links."""
    image = _read_owned_native_result(path, 80, "built image ID").decode("ascii").strip()
    if DIGEST_RE.fullmatch(image) is None:
        raise ValueError("built image ID must be one full sha256 identity")
    return image


def _controller_import_smoke_code() -> str:
    """Import every controller dependency with a child-only secret and frozen identity."""
    return "\n".join(
        (
            "import json, logging, os, secrets, sqlite3, sys, tomllib",
            "from importlib import metadata",
            "from pathlib import Path",
            "from packaging.version import Version",
            "diagnostic_records = []",
            "class RuntimeDiagnostics(logging.Handler):",
            "    def emit(self, record):",
            "        diagnostic_records.append(record)",
            "diagnostic_handler = RuntimeDiagnostics(level=logging.WARNING)",
            "logging.basicConfig(level=logging.WARNING)",
            "logging.getLogger().addHandler(diagnostic_handler)",
            f"settings_path = Path({str(SMOKE_SETTINGS_PATH)!r})",
            '(settings_path.is_file() and not settings_path.is_symlink()) or '
            'sys.exit("Native EE smoke settings are missing or not a regular file")',
            'limiter_path = settings_path.parent / "limiter.toml"',
            '(limiter_path.is_file() and not limiter_path.is_symlink()) or '
            'sys.exit("Native EE smoke limiter is missing or not a regular file")',
            'tomllib.loads(limiter_path.read_text(encoding="utf-8"))',
            'os.environ["SEARXNG_SETTINGS_PATH"] = str(settings_path)',
            'os.environ["SEARXNG_SECRET"] = secrets.token_hex(32)',
            "import " + ", ".join(EXPECTED_CONTROLLER_IMPORTS),
            "import searx.version_frozen as frozen",
            f"expected = {SEARXNG_SOURCE_REVISION!r}",
            'dist = metadata.distribution("searxng")',
            'record = json.loads(dist.read_text("direct_url.json") or "null")',
            'isinstance(record, dict) or sys.exit("SearXNG installed source provenance is missing")',
            'vcs = record.get("vcs_info", {})',
            '(isinstance(vcs, dict) and record.get("url", "").removesuffix(".git") == '
            '"https://github.com/searxng/searxng" and vcs.get("vcs") == "git" and '
            'vcs.get("commit_id") == expected) or sys.exit("SearXNG installed source identity differs")',
            'target = Path(dist.locate_file("searx/version_frozen.py"))',
            '(target.is_file() and not target.is_symlink() and '
            'Path(frozen.__file__).resolve() == target.resolve()) or '
            'sys.exit("SearXNG frozen module is not the installed package file")',
            'installed_version = Version(dist.version)',
            'frozen_version = Version(frozen.VERSION_TAG)',
            '(installed_version.local is not None and 7 <= len(installed_version.local) <= 40 and '
            'expected.startswith(installed_version.local) and '
            'installed_version.public == frozen_version.public and frozen_version.local == expected and '
            'frozen.VERSION_STRING == frozen.VERSION_TAG and '
            'frozen.DOCKER_TAG == frozen.VERSION_STRING.replace("+", "-") and '
            'frozen.GIT_URL in {"https://github.com/searxng/searxng", "unknow"} and '
            'isinstance(frozen.GIT_BRANCH, str) and bool(frozen.GIT_BRANCH)) or '
            'sys.exit("SearXNG frozen version differs from installed source identity")',
            "runtime_errors = []",
            "if sqlite3.sqlite_version_info < (3, 35, 0):",
            '    runtime_errors.append("SQLite runtime must be >= 3.35: " + sqlite3.sqlite_version)',
            "if sqlite3.threadsafety != 3:",
            '    runtime_errors.append("SQLite runtime must use serialized threading: " + str(sqlite3.threadsafety))',
            "runtime_errors.extend(",
            "    record.levelname + ':' + record.name + ': ' + record.getMessage()",
            "    for record in diagnostic_records",
            ")",
            "logging.getLogger().removeHandler(diagnostic_handler)",
            "if runtime_errors:",
            '    sys.exit("Native EE runtime initialization failed:\\n" + "\\n".join(runtime_errors))',
            "print('ANSIBLE_EE_IMPORT_OK')",
        )
    )


def _smoke_environment(runtime: str, image: str) -> int:
    """Warm the complete controller and OpenTofu offline in owned containers."""
    interpreter = cast(
        dict[str, str], EXPECTED_DEPENDENCIES["python_interpreter"]
    )["python_path"]
    project = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    namespace = f"gludd-ansible-ee-{project}-{os.getpid()}"
    smoke_commands = (
        (
            "opentofu",
            [
                runtime,
                "run",
                "--rm",
                "--network=none",
                "--pull=never",
                "--name",
                f"{namespace}-opentofu",
                image,
                "/usr/local/bin/tofu",
                "version",
            ],
        ),
        (
            "python",
            [
                runtime,
                "run",
                "--rm",
                "--network=none",
                "--pull=never",
                "--name",
                f"{namespace}-python",
                image,
                interpreter,
                "-W",
                "error",
                "-c",
                _controller_import_smoke_code(),
            ],
        ),
    )
    for phase, command in smoke_commands:
        print(
            f"ANSIBLE_EE_SMOKE_START phase={phase} runtime={runtime} image={image}",
            flush=True,
        )
        result = subprocess.run(command, check=False)
        print(
            f"ANSIBLE_EE_SMOKE_END phase={phase} rc={result.returncode}",
            flush=True,
        )
        if result.returncode != 0:
            return result.returncode
    return 0


class _NativeHandle(Protocol):
    """One native temporary handle owned by an exclusive creation process."""

    def close(self) -> None:
        """Close the actual native allocation, surfacing teardown failures."""


class _NativeBuilder(Protocol):
    """The pinned native generator and its runtime command owner."""

    @property
    def build_command(self) -> list[str]:
        """Return the command generated by native Builder."""

    def create(self) -> bool:
        """Generate one native context."""


class _NativeBuilderFactory(Protocol):
    """Typed boundary around the existing installed native constructor."""

    def __call__(
        self, *, action: str, filename: str, build_context: str, container_runtime: str,
        tag: list[str], verbosity: int, extra_build_cli_args: str,
    ) -> _NativeBuilder:
        """Create the native owner without reproducing its implementation."""


class _NativeCacheInfo(Protocol):
    currsize: int


class _NativePathCache(Protocol):
    def cache_info(self) -> _NativeCacheInfo:
        """Expose the native cache's real ownership state."""


class _NativeDefinitionClass(Protocol):
    get_dep_abs_path: _NativePathCache


class _NativeDefinitions(Protocol):
    _tempfiles: list[_NativeHandle]
    UserDefinition: _NativeDefinitionClass


class _NativeContextReport(TypedDict):
    """Exclusive worker output bound to the generated context bytes."""

    command: list[str]
    files: dict[str, str]


class _EEContextInputs(TypedDict):
    """Copied, admitted inputs for the existing controller image owner."""

    definition: Path
    requirements: dict[str, list[dict[str, str]]]
    entries: list[CollectionArtifactEntry]
    controller_version: str


def _close_native_builder_resources(resources: list[_NativeHandle], failure: BaseException | None) -> None:
    """Close every exclusive native allocation, retaining all failures."""
    errors: list[BaseException] = [] if failure is None else [failure]
    retained = []
    for resource in tuple(resources):
        try:
            resource.close()
        except BaseException as exc:
            errors.append(exc)
            retained.append(resource)
    resources[:] = retained
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("native Builder creation/teardown failed", errors)


def _context_file_hashes(context: Path) -> dict[str, str]:
    """Reject aliases and bind every generated file, not just known markers."""
    if context.is_symlink() or not context.is_dir():
        raise ValueError("native context root must be a real directory")
    inventory = {}
    for path in sorted(context.rglob("*")):
        if path.is_symlink():
            raise ValueError("native context must not contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("native context must contain only regular files")
        inventory[str(path.relative_to(context))] = _sha256(path)
    return inventory


class _NativeBuilderDiagnostics(logging.Handler):
    """Turn actual native warnings/errors into admission failures."""

    def emit(self, record: logging.LogRecord) -> None:
        raise ValueError(f"native Builder diagnostic: {record.levelname}: {record.getMessage()}")


def _native_create_context(
    definition: Path, context: Path, runtime: str, image: str, image_id_path: Path, output: Path,
) -> int:
    """Create once in a fresh CLI process; never borrow a registry suffix."""
    native = cast(_NativeDefinitions, import_module("ansible_builder.user_definition"))
    resources = native._tempfiles
    paths = native.UserDefinition.get_dep_abs_path
    if (metadata.version("ansible-builder") != "3.1.1" or threading.active_count() != 1
            or not isinstance(resources, list) or resources or paths.cache_info().currsize):
        raise ValueError("native Builder requires an exclusive fresh registry and cache")
    owned = resources
    factory = cast(_NativeBuilderFactory, import_module("ansible_builder.main").AnsibleBuilder)
    failure: BaseException | None = None
    builder: _NativeBuilder | None = None
    logger = logging.getLogger("ansible_builder")
    diagnostics = _NativeBuilderDiagnostics(level=logging.WARNING)
    logger.addHandler(diagnostics)
    try:
        builder = factory(action="create", filename=str(definition), build_context=str(context),
                          container_runtime=runtime, tag=[image], verbosity=3,
                          extra_build_cli_args=shlex.join(["--iidfile", str(image_id_path)]))
        if not builder.create():
            raise ValueError("native Builder did not create the context")
    except BaseException as exc:
        failure = exc
    finally:
        logger.removeHandler(diagnostics)
    if threading.active_count() != 1 or native._tempfiles is not owned:
        exclusive = ValueError("native exclusive ownership was lost; foreign handles are not closed")
        if failure is not None:
            exclusive_failure: BaseException = BaseExceptionGroup(
                "native ownership/creation failed", [failure, exclusive],
            )
        else:
            exclusive_failure = exclusive
        _close_native_builder_resources([diagnostics], exclusive_failure)
        raise exclusive_failure
    closing: list[_NativeHandle] = [*owned, diagnostics]
    try:
        _close_native_builder_resources(closing, failure)
    finally:
        owned[:] = [resource for resource in closing if resource is not diagnostics]
    if builder is None:
        raise ValueError("native Builder did not acquire its owner")
    report: _NativeContextReport = {"command": builder.build_command, "files": _context_file_hashes(context)}
    output.write_text(json.dumps(report), encoding="utf-8")
    return 0


def _require_new_ee_context(context: Path) -> None:
    """Reserve only a new namespaced output with a real existing parent."""
    if (not context.name.startswith("gludd-") or ".." in context.parts
            or context.exists() or context.is_symlink() or not context.parent.is_dir()):
        raise ValueError("EE context must be a new namespaced path with an existing parent")
    for path in (context, *context.parents):
        if path.is_symlink() and path != Path("/tmp"):
            raise ValueError("EE context must not traverse symlink paths")


def _ee_context_inputs(state: Path, artifact_root: Path) -> _EEContextInputs:
    """Snapshot the canonical reviewed closure and existing eight Gludd artifacts."""
    required = required_collection_versions()
    bound = bound_collection_lock_sha256()
    entries = _collection_lock_entries(INPUTS["collection_lock"], bound, required)
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        raise ValueError("reviewed collection artifact root is missing or unsafe")
    for path in artifact_root.parents:
        if path.is_symlink() and path != Path("/tmp"):
            raise ValueError("reviewed collection artifact root traverses a symlink")
    copied = state / "archive-inputs"
    copied.mkdir()
    for entry in entries:
        source = artifact_root / entry["artifact"]
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"collection artifact is missing or unsafe: {entry['name']}")
        shutil.copyfile(source, copied / entry["artifact"])
    frozen = state / "canonical-inputs"
    frozen.mkdir()
    for name in ("definition", "galaxy", "python", "system", "smoke_settings", "smoke_limiter"):
        shutil.copyfile(INPUTS[name], frozen / INPUTS[name].name)
        if _sha256(frozen / INPUTS[name].name) != _reviewed_runtime_input_hash(name):
            raise ValueError(f"canonical EE {name} input changed during preparation")
    definition = cast(dict[str, object], yaml.safe_load((frozen / DEFINITION.name).read_text(encoding="utf-8")))
    dependencies = cast(dict[str, object], definition["dependencies"])
    core = cast(dict[str, str], dependencies["ansible_core"])["package_pip"]
    pins = list(Requirement(core).specifier)
    if len(pins) != 1 or pins[0].operator != "==":
        raise ValueError("EE controller version must be one exact native pin")
    controller_version = pins[0].version
    inventory = _collection_archive_inventory(state, entries, copied, controller_version=controller_version)
    _validate_collection_dependencies(inventory, {entry["name"]: entry["version"] for entry in entries})
    status = _build_collection_artifacts()
    if status:
        raise ValueError(f"owned Gludd collection producer failed: rc={status}")
    original = cast(dict[str, object], yaml.safe_load((frozen / INPUTS["galaxy"].name).read_text(encoding="utf-8")))
    local = [entry for entry in cast(list[dict[str, str]], original["collections"]) if entry.get("type") == "file"]
    expected = [{"name": f"collections/{artifact.name}", "type": "file"} for _source, artifact in COLLECTION_ARTIFACTS]
    if len(local) != 8 or local != expected or len(COLLECTION_ARTIFACTS) != len(_COLLECTION_VERSIONS):
        raise ValueError("EE must retain all eight original Gludd file requirements")
    for (name, version), (_source, artifact) in zip(_COLLECTION_VERSIONS, COLLECTION_ARTIFACTS, strict=True):
        if artifact.is_symlink() or not artifact.is_file() or artifact.name in {entry["artifact"] for entry in entries}:
            raise ValueError(f"owned Gludd artifact is missing or unsafe: {name}")
        target = copied / artifact.name
        shutil.copyfile(artifact, target)
        entries.append({"name": f"general_ludd.{name}", "version": version, "artifact": target.name,
                        "origin": "owned-native-gludd-build", "sha256": _sha256(target), "size": target.stat().st_size})
    inventory = _collection_archive_inventory(state, entries, copied, controller_version=controller_version)
    _validate_collection_dependencies(inventory, {entry["name"]: entry["version"] for entry in entries})
    requirements = {"collections": [{"name": f"collections/third-party/{entry['artifact']}",
                                     "version": entry["version"], "type": "file"}
                                    for entry in entries if not entry["name"].startswith("general_ludd.")] + local}
    requirement_file = state / "offline-requirements.yml"
    requirement_file.write_text(yaml.safe_dump(requirements, sort_keys=False), encoding="utf-8")
    dependencies["galaxy"] = str(requirement_file)
    for kind in ("python", "system"):
        dependencies[kind] = str(frozen / INPUTS[kind].name)
    files = copy.deepcopy(cast(list[dict[str, str]], definition["additional_build_files"]))
    for build_file in files:
        filename = Path(build_file["src"]).name
        build_file["src"] = str(copied / filename if build_file["dest"] == "collections" else frozen / filename)
    files.extend({"src": str(copied / entry["artifact"]), "dest": "collections/third-party"}
                 for entry in entries if not entry["name"].startswith("general_ludd."))
    definition["additional_build_files"] = files
    defaults = cast(dict[str, str], definition.setdefault("build_arg_defaults", {}))
    defaults["ANSIBLE_GALAXY_CLI_COLLECTION_OPTS"] = "--offline --no-cache"
    overlay = state / "execution-environment.yml"
    overlay.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
    return {"definition": overlay, "requirements": requirements, "entries": entries,
            "controller_version": controller_version}


def _admit_native_context(
    state: Path, context: Path, inputs: _EEContextInputs, runtime: str, image: str, image_id: Path,
) -> list[str]:
    """Validate all worker output against parent-owned policy before runtime."""
    report_file = state / "native-context.json"
    report = cast(dict[str, object], json.loads(_read_owned_native_result(
        report_file, 65536, "native context report",
    )))
    instruction = "Dockerfile" if runtime == "docker" else "Containerfile"
    expected = [runtime, "build", "-f", str(context / instruction), "-t", image,
                "--iidfile", str(image_id), str(context)]
    if set(report) != {"command", "files"} or report["command"] != expected:
        raise ValueError("native build command differs from parent-owned runtime policy")
    if report["files"] != _context_file_hashes(context):
        raise ValueError("native context files changed after exclusive generation")
    requirement_file = context / "_build/requirements.yml"
    if yaml.safe_load(requirement_file.read_text(encoding="utf-8")) != inputs["requirements"]:
        raise ValueError("native context must contain only the exact admitted local requirements")
    for name, generated in (("python", "requirements.txt"), ("system", "bindep.txt"),
                            ("smoke_settings", "smoke/ee-smoke-settings.yml"),
                            ("smoke_limiter", "smoke/ee-smoke-limiter.toml")):
        if _sha256(context / "_build" / generated) != _reviewed_runtime_input_hash(name):
            raise ValueError(f"native context changed canonical EE {name} bytes")
    definition = cast(dict[str, object], yaml.safe_load(inputs["definition"].read_text(encoding="utf-8")))
    steps = (context / instruction).read_text(encoding="utf-8")
    install = "ansible-galaxy collection install $ANSIBLE_GALAXY_CLI_COLLECTION_OPTS -r requirements.yml"
    if 'ARG ANSIBLE_GALAXY_CLI_COLLECTION_OPTS="--offline --no-cache"' not in steps or install not in steps:
        raise ValueError("native context must preserve strict offline Galaxy installation")
    configured = cast(dict[str, list[str]], definition["additional_build_steps"])
    if any("\n".join(stage) not in steps for stage in configured.values()):
        raise ValueError("native context lost original canonical EE build steps")
    defaults = cast(dict[str, str], definition["build_arg_defaults"])
    if any(f'ARG {name}="{value}"' not in steps for name, value in defaults.items()):
        raise ValueError("native context changed canonical EE build argument defaults")
    inventory = {}
    for gludd, destination in ((False, "collections/third-party"), (True, "collections")):
        entries = [entry for entry in inputs["entries"] if entry["name"].startswith("general_ludd.") == gludd]
        root = context / "_build" / destination
        actual = {path.name for path in root.iterdir() if path.is_file() or path.is_symlink()}
        if actual != {entry["artifact"] for entry in entries}:
            raise ValueError("native context archive inventory differs from the admitted closure")
        inventory.update(_collection_archive_inventory(state, entries, root,
                                                       controller_version=inputs["controller_version"]))
    _validate_collection_dependencies(inventory, {entry["name"]: entry["version"] for entry in inputs["entries"]})
    return expected


def _cleanup_ee_context_quarantine(quarantine: Path, context: Path, identity: tuple[int, int]) -> None:
    """Finish the private acquisition or explicitly transfer retained output."""
    moved = quarantine / "failed-context"
    try:
        acquired = moved.lstat()
    except FileNotFoundError:
        quarantine.rmdir()
        return
    if stat.S_ISDIR(acquired.st_mode) and (acquired.st_dev, acquired.st_ino) == identity:
        shutil.rmtree(quarantine)
        return
    try:
        _rename_collection_generation_no_replace(moved, context)
    except BaseException as recovery:
        ownership = ValueError(f"foreign EE context retained without deletion: {moved}")
        raise BaseExceptionGroup("EE context ownership/restore failed", [ownership, recovery]) from recovery
    quarantine.rmdir()
    raise ValueError(f"failed EE context ownership changed; retained without deletion: {context}")


def _remove_owned_failed_context(context: Path, identity: tuple[int, int]) -> None:
    """Remove only this acquisition's failed output, never a replacement tree."""
    try:
        current = context.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != identity:
        raise ValueError(f"failed EE context ownership changed; retained without deletion: {context}")
    # Quarantine exclusively before checking the moved inode. Every outcome
    # reaches the teardown owner; unknown output is restored or retained at a
    # reported bounded path, never passed to recursive deletion.
    quarantine = Path(tempfile.mkdtemp(prefix="gludd-ee-context-cleanup-", dir=context.parent))
    failure: BaseException | None = None
    try:
        _rename_collection_generation_no_replace(context, quarantine / "failed-context")
    except BaseException as exc:
        failure = exc
    finally:
        try:
            _cleanup_ee_context_quarantine(quarantine, context, identity)
        except BaseException as cleanup:
            if failure is None and not quarantine.exists():
                raise
            retention = ValueError(f"EE context cleanup failed; inspect acquisition path: {quarantine}")
            errors = [retention, cleanup] if failure is None else [failure, retention, cleanup]
            raise BaseExceptionGroup("EE context quarantine teardown failed", errors) from cleanup
    if failure is not None:
        raise failure


@contextmanager
def _prepared_native_context(runtime: str, image: str, context: Path, artifact_root: Path) -> Iterator[list[str]]:
    """Reuse the existing private ownership boundary and native build command."""
    context = context.absolute()
    _require_new_ee_context(context)
    project = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    with tempfile.TemporaryDirectory(prefix=f"gludd-ansible-ee-{project}-{os.getpid()}-") as temporary:
        state = Path(temporary)
        private = _collection_environment(state)
        inputs = _ee_context_inputs(state, artifact_root.absolute())
        context.mkdir(mode=0o700)
        acquired = context.stat()
        identity = (acquired.st_dev, acquired.st_ino)
        try:
            image_id = state / "built-image-id"
            command = [sys.executable, "-W", "error", str(Path(__file__).absolute()), "native-create-context",
                       "--runtime", runtime, "--image", image, "--context", str(context),
                       "--native-context-definition", str(inputs["definition"]),
                       "--native-context-output", str(state / "native-context.json"),
                       "--native-image-id", str(image_id)]
            log = ROOT / ".gate-logs" / f"{state.name}-native-context.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            print(f"ANSIBLE_EE_CONTEXT_GENERATE_START context={context} log={log}", flush=True)
            result = _run_collection_command(command, log, max_seconds=120, environment=private, cwd=ROOT)
            guard = import_module("scripts.check_molecule_yaml")
            print(f"ANSIBLE_EE_CONTEXT_GENERATE_END rc={result} log={log}", flush=True)
            if result or not guard.validate_native_lint_diagnostics(log):
                raise ValueError(f"exclusive native Builder context creation failed: rc={result}; retained log={log}")
            yield _admit_native_context(state, context, inputs, runtime, image, image_id)
        except BaseException as failure:
            try:
                _remove_owned_failed_context(context, identity)
            except BaseException as cleanup:
                raise BaseExceptionGroup("EE context admission/cleanup failed", [failure, cleanup]) from failure
            raise


def prepare_environment_context(
    runtime: str, image: str, context: Path, artifact_root: Path, validate_only: bool,
) -> int:
    """Prepare reviewed native EE context without a runtime or image build."""
    errors = validate_files()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if validate_only:
        print(f"ANSIBLE_EE_CONTEXT_PLAN runtime={runtime} context={context} artifacts={artifact_root} proof=none")
        return 0
    if find_spec("ansible_builder") is None:
        print("ansible-builder is unavailable; sync the controller dependencies", file=sys.stderr)
        return 2
    try:
        with _prepared_native_context(runtime, image, context, artifact_root):
            print(f"ANSIBLE_EE_CONTEXT_READY context={context} proof=generated-context-only", flush=True)
    except Exception as exc:
        print(f"ANSIBLE_EE_CONTEXT_FAILED: {exc} proof=none", file=sys.stderr)
        return 1
    return 0


def build_environment(
    runtime: str, image: str, context: Path, validate_only: bool, artifact_root: Path | None = None,
) -> int:
    """Build through ansible-builder after fail-closed input validation."""
    errors = validate_files()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if validate_only:
        print(f"ANSIBLE_EE_BUILD_VALIDATED definition={DEFINITION.relative_to(ROOT)} context={context}")
        return 0
    if artifact_root is None:
        print("EE build requires an explicit reviewed collection artifact root", file=sys.stderr)
        return 2
    if find_spec("ansible_builder") is None:
        print("ansible-builder is unavailable; sync the dev/controller dependencies", file=sys.stderr)
        return 2
    if shutil.which(runtime) is None:
        print(f"container runtime is unavailable: {runtime}", file=sys.stderr)
        return 2
    try:
        base_image = check_configured_base_image()
    except (RuntimeError, ValueError) as exc:
        print(
            f"Ansible base image is unavailable: {exc}; "
            "run make refresh-ansible-base-image ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY=0",
            file=sys.stderr,
        )
        return 1
    print(f"ANSIBLE_BASE_IMAGE_AVAILABLE image={base_image}", flush=True)
    try:
        with _prepared_native_context(runtime, image, context, artifact_root) as command:
            image_id_path = Path(command[command.index("--iidfile") + 1])
            print(f"ANSIBLE_EE_BUILD_START runtime={runtime} tag={image} context={context}", flush=True)
            result = subprocess.run(command, cwd=ROOT, check=False)
            status = result.returncode
            if status == 0:
                built_image = _built_image_id(image_id_path)
                status = _smoke_environment(runtime, built_image)
    except Exception as exc:
        print(f"Ansible EE build admission failed: {exc}", file=sys.stderr, flush=True)
        status = 1
    print(f"ANSIBLE_EE_BUILD_END rc={status}", flush=True)
    return status


def verify_environment(runtime: str, image: str, validate_only: bool) -> int:
    """Verify a digest-addressed EE, OpenTofu CLI, and controller imports."""
    try:
        _require_image(image)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    errors = validate_files()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if validate_only:
        print(f"ANSIBLE_EE_VERIFY_VALIDATED image={image}")
        return 0
    if shutil.which(runtime) is None:
        print(f"container runtime is unavailable: {runtime}", file=sys.stderr)
        return 2
    inspect = subprocess.run([runtime, "image", "inspect", image], check=False)
    if inspect.returncode != 0:
        return inspect.returncode
    return _smoke_environment(runtime, image)


def main(argv: list[str] | None = None) -> int:
    """Run the artifact CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "build",
            "prepare-context",
            "native-create-context",
            "check-base-image",
            "refresh-base-image",
            "resolve-requirements",
            "stage-collections",
            "validate",
            "verify",
            "write-lock",
        ),
    )
    parser.add_argument("--runtime", choices=("podman", "docker"), default="podman")
    parser.add_argument("--image", default="gludd-ansible-ee:0.1.0-beta.4")
    parser.add_argument("--context", type=Path, default=Path("/tmp/gludd-ansible-ee-context"))
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--collection-root", type=Path)
    parser.add_argument("--resolution-output", type=Path)
    parser.add_argument("--collection-stage-mode", choices=("refresh", "acquire", "offline"), default="offline")
    parser.add_argument("--collection-lock", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--native-context-definition", type=Path)
    parser.add_argument("--native-context-output", type=Path)
    parser.add_argument("--native-image-id", type=Path)
    parser.add_argument("--collection-requirements", type=Path, default=INPUTS["galaxy"])
    parser.add_argument("--collection-lock-sha256", default="")
    parser.add_argument("--galaxy-origin", default="https://galaxy.ansible.com/")
    parser.add_argument("--collection-timeout-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    if args.mode == "native-create-context":
        paths = (args.native_context_definition, args.native_context_output, args.native_image_id)
        if any(value is None for value in paths):
            parser.error("native context creation requires the existing owner's explicit input/output paths")
        return _native_create_context(args.native_context_definition, args.context, args.runtime,
                                      args.image, args.native_image_id, args.native_context_output)
    if args.mode in {"build", "prepare-context"} and args.artifact_root is None:
        parser.error("build/prepare-context requires --artifact-root")
    if args.mode == "prepare-context":
        return prepare_environment_context(args.runtime, args.image, args.context,
                                           args.artifact_root, args.validate_only)
    if args.mode == "stage-collections":
        if args.collection_lock is None or args.artifact_root is None:
            parser.error("stage-collections requires --collection-lock and --artifact-root")
        try:
            required = _required_collection_versions(args.collection_requirements)
        except Exception as exc:
            print(f"ANSIBLE_COLLECTION_STAGE_FAILED: {exc} proof=none", file=sys.stderr)
            return 1
        return prepare_collection_artifacts(
            args.collection_stage_mode, args.collection_lock, args.artifact_root,
            required_versions=required, origin=args.galaxy_origin, expected_lock_sha256=args.collection_lock_sha256,
            timeout_seconds=args.collection_timeout_seconds, validate_only=args.validate_only,
        )
    if args.mode == "resolve-requirements":
        if args.collection_root is None or args.resolution_output is None:
            parser.error("resolve-requirements requires --collection-root and --resolution-output")
        return resolve_requirements(args.collection_root, args.resolution_output, args.validate_only)
    if args.mode == "write-lock":
        write_lock()
        print(f"ANSIBLE_RUNTIME_LOCK_UPDATED path={LOCK.relative_to(ROOT)}")
        return 0
    if args.mode == "refresh-base-image":
        image = refresh_base_image()
        print(f"ANSIBLE_BASE_IMAGE_REFRESHED image={image}")
        return 0
    if args.mode == "check-base-image":
        image = check_configured_base_image()
        print(f"ANSIBLE_BASE_IMAGE_AVAILABLE image={image}")
        return 0
    if args.mode == "build":
        return build_environment(args.runtime, args.image, args.context, args.validate_only, args.artifact_root)
    if args.mode == "verify":
        return verify_environment(args.runtime, args.image, args.validate_only)
    errors = validate_files()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"ANSIBLE_RUNTIME_BOUNDARY_PASS inputs={len(INPUTS)} managed_host=locked core=separate")
    return 0


if __name__ == "__main__":
    # Bootstrap the standalone CLI only; library calls never change sys.path.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
