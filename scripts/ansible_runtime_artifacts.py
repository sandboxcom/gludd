#!/usr/bin/env python3
"""Validate and operate the beta4 Ansible execution-environment artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml

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
FRICTIONLESS_REQUIREMENT = "frictionless==5.19.1"
JSONPOINTER_REQUIREMENT = "jsonpointer==3.2.0"
OPENAPI_CORE_REQUIREMENT = "openapi-core==0.23.1"
EXPECTED_CONTROLLER_IMPORTS = (
    "ansible",
    "ansible_runner",
    "frictionless",
    "jsonpointer",
    "openapi_core",
    "searx.webapp",
)
EXPECTED_DEPENDENCIES: dict[str, object] = {
    "galaxy": "requirements.yml",
    "python": "requirements.txt",
    "system": "bindep.txt",
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
    mode = path.stat().st_mode & 0o777
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
    for required in ("ansible-core", "ansible-runner", "jsonpointer", "openapi-core"):
        if required not in controller_names:
            errors.append(f"missing optional controller dependency: {required}")
    python_requirements = INPUTS["python"].read_text(encoding="utf-8").splitlines()
    pinned_requirements = [
        line.strip()
        for line in python_requirements
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if pinned_requirements != [
        FRICTIONLESS_REQUIREMENT,
        JSONPOINTER_REQUIREMENT,
        OPENAPI_CORE_REQUIREMENT,
        SEARXNG_REQUIREMENT,
    ]:
        errors.append(
            "controller Python requirements must contain only pinned Frictionless, "
            "jsonpointer, openapi-core, and official SearXNG"
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
    ]
    print(f"ANSIBLE_EE_BUILD_START runtime={runtime} tag={image} context={context}", flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    print(f"ANSIBLE_EE_BUILD_END rc={result.returncode}", flush=True)
    return result.returncode


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
    smoke_commands = (
        (
            "opentofu",
            [
                runtime,
                "run",
                "--rm",
                "--network=none",
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
                image,
                "python3",
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


def main(argv: list[str] | None = None) -> int:
    """Run the artifact CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "build",
            "check-base-image",
            "refresh-base-image",
            "validate",
            "verify",
            "write-lock",
        ),
    )
    parser.add_argument("--runtime", choices=("podman", "docker"), default="podman")
    parser.add_argument("--image", default="gludd-ansible-ee:0.1.0-beta.4")
    parser.add_argument("--context", type=Path, default=Path("/tmp/gludd-ansible-ee-context"))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
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
