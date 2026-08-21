#!/usr/bin/env python3
"""Validate and operate the beta4 Ansible execution-environment artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "config" / "ansible"
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
COLLECTION_ARTIFACTS = (
    (
        ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent",
        ROOT / "dist" / "collections" / "general_ludd-agent-0.2.0.tar.gz",
    ),
    (
        ROOT / "collections" / "ansible_collections" / "general_ludd" / "language",
        ROOT / "dist" / "collections" / "general_ludd-language-0.1.0.tar.gz",
    ),
    (
        ROOT / "collections" / "ansible_collections" / "general_ludd" / "networking",
        ROOT / "dist" / "collections" / "general_ludd-networking-0.2.0.tar.gz",
    ),
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
    missing = [_display_path(path) for path in (*INPUTS.values(), LOCK, MANAGED) if not path.is_file()]
    if missing:
        return [f"missing runtime artifact: {path}" for path in missing]

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_names = _dependency_names(project["project"]["dependencies"])
    for forbidden in ("ansible-core", "ansible-runner", "ansible-builder"):
        if forbidden in runtime_names:
            errors.append(f"core dependency leak: {forbidden}")
    controller = project["project"]["optional-dependencies"].get("ansible-controller", [])
    controller_names = _dependency_names(controller)
    for required in ("ansible-core", "ansible-runner"):
        if required not in controller_names:
            errors.append(f"missing optional controller dependency: {required}")

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
    if shutil.which("ansible-builder") is None:
        print("ansible-builder is unavailable; sync the dev/controller dependencies", file=sys.stderr)
        return 2
    if shutil.which(runtime) is None:
        print(f"container runtime is unavailable: {runtime}", file=sys.stderr)
        return 2
    collection_status = _build_collection_artifacts()
    if collection_status != 0:
        return collection_status
    context.mkdir(parents=True, exist_ok=True)
    command = [
        "ansible-builder",
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
    """Verify a digest-addressed EE and its controller imports."""
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
    command = [
        runtime,
        "run",
        "--rm",
        "--network=none",
        image,
        "python3",
        "-c",
        "import ansible, ansible_runner; print('ANSIBLE_EE_IMPORT_OK')",
    ]
    print(f"ANSIBLE_EE_SMOKE_START runtime={runtime} image={image}", flush=True)
    result = subprocess.run(command, check=False)
    print(f"ANSIBLE_EE_SMOKE_END rc={result.returncode}", flush=True)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    """Run the artifact CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "validate", "verify", "write-lock"))
    parser.add_argument("--runtime", choices=("podman", "docker"), default="podman")
    parser.add_argument("--image", default="gludd-ansible-ee:0.1.0-beta.4")
    parser.add_argument("--context", type=Path, default=Path("/tmp/gludd-ansible-ee-context"))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "write-lock":
        write_lock()
        print(f"ANSIBLE_RUNTIME_LOCK_UPDATED path={LOCK.relative_to(ROOT)}")
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
