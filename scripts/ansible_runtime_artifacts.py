#!/usr/bin/env python3
"""Validate and operate the beta4 Ansible execution-environment artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from importlib import import_module, metadata
from importlib.util import find_spec
from pathlib import Path
from typing import Any, cast
from urllib.request import Request, urlopen

import yaml
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "config" / "ansible"
CONTROLLER_PROFILE = ROOT / "requirements" / "profiles" / "ansible-controller" / "pyproject.toml"
DEFINITION = CONFIG_ROOT / "execution-environment.yml"
LOCK = CONFIG_ROOT / "runtime-lock.json"
MANAGED = CONFIG_ROOT / "managed-host-python.lock.json"
INPUTS = {
    "galaxy": CONFIG_ROOT / "requirements.yml",
    "python": CONFIG_ROOT / "requirements.txt",
    "system": CONFIG_ROOT / "bindep.txt",
    "definition": DEFINITION,
}
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
        "python_path": "/usr/bin/python3.11",
    },
}
EXPECTED_BUILD_FILES = [
    {
        "src": f"../../dist/collections/{artifact.name}",
        "dest": "collections",
    }
    for _source, artifact in COLLECTION_ARTIFACTS
]
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
        errors.append("execution environment must stage the exact locked collection artifacts")
    build_steps = definition.get("additional_build_steps", {})
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

    managed = json.loads(MANAGED.read_text(encoding="utf-8"))
    if managed.get("ambient_interpreters_allowed") is not False:
        errors.append("managed-host manifest must reject ambient interpreters")
    if managed.get("interpreter_variable") != "ansible_python_interpreter":
        errors.append("managed-host manifest must select ansible_python_interpreter")
    if not isinstance(managed.get("requirements"), list):
        errors.append("managed-host requirements must be an explicit list")
    return errors


def _staged_collection_metadata(collection_root: Path) -> None:
    """Reject partial staged inputs using Galaxy's own dependency version rules."""
    from ansible.galaxy.dependency_resolution.versioning import meets_requirements

    staged = collection_root / "ansible_collections"
    if not staged.is_dir():
        raise ValueError(f"partial collection inputs: missing staged tree: {staged}")
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
    for name in sorted(required):
        if name not in inventory:
            raise ValueError(f"missing staged collection manifest: {name}")
    for name, (_release, dependencies) in inventory.items():
        for dependency, constraint in dependencies.items():
            if dependency not in inventory:
                raise ValueError(f"missing staged collection manifest: {dependency} (required by {name})")
            if not meets_requirements(inventory[dependency][0], constraint):
                raise ValueError(f"staged collection dependency mismatch: {name} -> {dependency}")


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


def _built_image_id(path: Path) -> str:
    """Read one bounded, owned builder result without following links."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_uid != os.getuid()
            or identity.st_nlink != 1
            or not 0 < identity.st_size <= 80
        ):
            raise ValueError("built image ID must be a bounded owned regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            image = handle.read(81).decode("ascii").strip()
    finally:
        os.close(descriptor)
    if DIGEST_RE.fullmatch(image) is None:
        raise ValueError("built image ID must be one full sha256 identity")
    return image


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
                "-c",
                "import "
                + ", ".join(EXPECTED_CONTROLLER_IMPORTS)
                + "; print('ANSIBLE_EE_IMPORT_OK')",
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


def build_environment(runtime: str, image: str, context: Path, validate_only: bool) -> int:
    """Build through ansible-builder after fail-closed input validation."""
    errors = validate_files()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if validate_only:
        print(f"ANSIBLE_EE_BUILD_VALIDATED definition={DEFINITION.relative_to(ROOT)} context={context}")
        return 0
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
    collection_status = _build_collection_artifacts()
    if collection_status != 0:
        return collection_status
    context.mkdir(parents=True, exist_ok=True)
    project = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    with tempfile.TemporaryDirectory(
        prefix=f"gludd-ansible-ee-{project}-{os.getpid()}-"
    ) as temporary:
        image_id_path = Path(temporary) / "built-image-id"
        command = [
            sys.executable,
            "-m",
            "ansible_builder",
            "build",
            "--file",
            str(DEFINITION),
            "--context",
            str(context),
            "--tag",
            image,
            "--container-runtime",
            runtime,
            "--extra-build-cli-args="
            + shlex.join(["--iidfile", str(image_id_path)]),
        ]
        print(f"ANSIBLE_EE_BUILD_START runtime={runtime} tag={image} context={context}", flush=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        status = result.returncode
        if status == 0:
            try:
                built_image = _built_image_id(image_id_path)
            except (OSError, ValueError) as exc:
                print(f"Ansible EE built image identity failed: {exc}", file=sys.stderr, flush=True)
                status = 1
            else:
                status = _smoke_environment(runtime, built_image)
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
            "check-base-image",
            "refresh-base-image",
            "resolve-requirements",
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
    args = parser.parse_args(argv)
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
        return build_environment(args.runtime, args.image, args.context, args.validate_only)
    if args.mode == "verify":
        return verify_environment(args.runtime, args.image, args.validate_only)
    errors = validate_files()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("ANSIBLE_RUNTIME_BOUNDARY_PASS inputs=4 managed_host=locked core=separate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
