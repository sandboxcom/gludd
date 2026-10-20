#!/usr/bin/env python3
"""Compose independent uv projects into explicit, locked install profiles.

Each profile is deliberately outside a uv workspace so uv gives it an
independent lock.  Synchronization is staged in a sibling environment and
promoted only after every locked project installs and ``uv pip check`` passes.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING


class ProfileError(RuntimeError):
    """Raised when the dependency-profile contract cannot be satisfied."""


@dataclass(frozen=True)
class Profile:
    """One independent uv project."""

    name: str
    project: Path
    audit_ignores: tuple[str, ...]


@dataclass(frozen=True)
class ProfileCatalog:
    """Validated profile metadata rooted in one checkout."""

    root: Path
    manifest: Path
    max_lock_lines: int
    root_audit_ignores: tuple[str, ...]
    profiles: Mapping[str, Profile]
    sets: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class Command:
    """One observable subprocess with a narrow environment overlay."""

    argv: tuple[str, ...]
    environment: Mapping[str, str]


RunCommand = Callable[[Command], None]
TextReader = Callable[[Path], str]


def _profile_text(path: Path, reader: TextReader | None) -> str:
    """Read one profile input from disk or the current inspection snapshot."""
    return path.read_text(encoding="utf-8") if reader is None else reader(path)


def _table(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProfileError(f"{label} must be a TOML table")
    return value


def _confined_project(root: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ProfileError(f"profile {name!r} has an invalid project path")
    project = (root / value).resolve()
    try:
        project.relative_to(root)
    except ValueError as exc:
        raise ProfileError(f"profile {name!r} escapes the repository") from exc
    return project


def _audit_ignores(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ProfileError(f"{label} must be a non-empty string list")
    if len(value) != len(set(value)):
        raise ProfileError(f"{label} contains duplicates")
    return tuple(value)


def load_catalog(
    root: str | Path,
    manifest: str | Path = "config/dependency_profiles.toml",
    *,
    read_text: TextReader | None = None,
) -> ProfileCatalog:
    """Load and structurally validate the dependency-profile catalog."""

    repo_root = Path(root).resolve()
    manifest_path = Path(manifest)
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path
    try:
        raw = tomllib.loads(_profile_text(manifest_path, read_text))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"cannot load profile catalog {manifest_path}: {exc}") from exc

    if raw.get("schema-version") != 1:
        raise ProfileError("dependency profile schema-version must be 1")
    max_lock_lines = raw.get("max-lock-lines")
    if not isinstance(max_lock_lines, int) or not 1 <= max_lock_lines < 2500:
        raise ProfileError("max-lock-lines must be an integer from 1 through 2499")

    profile_table = _table(raw.get("profiles"), "profiles")
    profiles: dict[str, Profile] = {}
    for name, value in profile_table.items():
        metadata = _table(value, f"profiles.{name}")
        profiles[name] = Profile(
            name=name,
            project=_confined_project(repo_root, metadata.get("project"), name),
            audit_ignores=_audit_ignores(
                metadata.get("audit-ignore"),
                f"profiles.{name}.audit-ignore",
            ),
        )

    set_table = _table(raw.get("sets"), "sets")
    profile_sets: dict[str, tuple[str, ...]] = {}
    for name, value in set_table.items():
        metadata = _table(value, f"sets.{name}")
        selected = metadata.get("profiles")
        if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
            raise ProfileError(f"sets.{name}.profiles must be a string list")
        if len(selected) != len(set(selected)):
            raise ProfileError(f"profile set {name!r} contains duplicates")
        unknown = sorted(set(selected) - set(profiles))
        if unknown:
            raise ProfileError(f"profile set {name!r} references unknown profiles: {', '.join(unknown)}")
        profile_sets[name] = tuple(selected)

    return ProfileCatalog(
        root=repo_root,
        manifest=manifest_path,
        max_lock_lines=max_lock_lines,
        root_audit_ignores=_audit_ignores(raw.get("audit-ignore"), "audit-ignore"),
        profiles=profiles,
        sets=profile_sets,
    )


def _selected(catalog: ProfileCatalog, profile_set: str | None) -> tuple[Profile, ...]:
    if profile_set is None:
        return tuple(catalog.profiles.values())
    names = catalog.sets.get(profile_set)
    if names is None:
        raise ProfileError(f"unknown profile set {profile_set!r}")
    return tuple(catalog.profiles[name] for name in names)


def lock_commands(
    catalog: ProfileCatalog,
    *,
    profile_set: str | None,
    check: bool,
    uv: str = "uv",
    profile: str | None = None,
) -> tuple[Command, ...]:
    """Return native lock commands for one profile, a set, or every project."""

    projects: tuple[Path, ...]
    if profile is not None:
        if profile_set is not None:
            raise ProfileError("profile and profile set are mutually exclusive")
        selected = catalog.profiles.get(profile)
        if selected is None:
            raise ProfileError(f"unknown profile {profile!r}")
        projects = (selected.project,)
    else:
        projects = (catalog.root, *[item.project for item in _selected(catalog, profile_set)])
    commands: list[Command] = []
    for project in projects:
        argv = [uv, "lock"]
        if check:
            argv.append("--check")
        argv.extend(("--project", str(project)))
        commands.append(Command(tuple(argv), {}))
    return tuple(commands)


def audit_commands(
    catalog: ProfileCatalog,
    *,
    profile_set: str,
    ignored_vulnerabilities: Sequence[str] = (),
    uv: str = "uv",
) -> tuple[Command, ...]:
    """Return one fail-closed vulnerability audit per selected locked project."""

    projects = (
        (catalog.root, catalog.root_audit_ignores),
        *[
            (profile.project, profile.audit_ignores)
            for profile in _selected(catalog, profile_set)
        ],
    )
    commands: list[Command] = []
    for project, scoped_ignores in projects:
        argv = [
            uv,
            "audit",
            "--preview-features",
            "audit",
            "--locked",
            "--project",
            str(project),
        ]
        for vulnerability in dict.fromkeys((*scoped_ignores, *ignored_vulnerabilities)):
            argv.extend(("--ignore", vulnerability))
        commands.append(Command(tuple(argv), {}))
    return tuple(commands)


def sync_commands(
    catalog: ProfileCatalog,
    *,
    profile_set: str,
    environment: Path,
    staging_environment: Path,
    python: str | None,
    uv: str = "uv",
    dry_run: bool = False,
    install_project: bool = True,
) -> tuple[Command, ...]:
    """Plan locked root/profile syncs into one explicit staged environment."""

    del environment  # The target is intentionally untouched until promotion.
    projects = (catalog.root, *[profile.project for profile in _selected(catalog, profile_set)])
    venv_argv = [uv, "venv", "--relocatable", str(staging_environment)]
    if python:
        venv_argv.extend(("--python", python))
    commands: list[Command] = [Command(tuple(venv_argv), {})]
    overlay = {
        "UV_PROJECT_ENVIRONMENT": str(staging_environment),
        "VIRTUAL_ENV": str(staging_environment),
    }
    for index, project in enumerate(projects):
        argv = [uv, "sync", "--project", str(project), "--locked"]
        if index == 0 and not install_project:
            argv.append("--no-install-project")
        if index:
            argv.append("--inexact")
        if python:
            argv.extend(("--python", python))
        if dry_run:
            argv.append("--dry-run")
        commands.append(Command(tuple(argv), overlay))
    return tuple(commands)


def render_requirements(
    catalog: ProfileCatalog, profile_set: str, *, read_text: TextReader | None = None,
) -> str:
    """Render the direct requirement union for tools that accept requirements files."""

    root = tomllib.loads(_profile_text(catalog.root / "pyproject.toml", read_text))
    project = _table(root.get("project"), "root project")
    root_dependencies = project.get("dependencies")
    if not isinstance(root_dependencies, list) or not all(
        isinstance(item, str) for item in root_dependencies
    ):
        raise ProfileError("root project.dependencies must be a string list")

    ordered = list(root_dependencies)
    for profile in _selected(catalog, profile_set):
        dependencies, _ = _project_metadata(profile.project / "pyproject.toml", read_text=read_text)
        ordered.extend(dependencies)
    unique = tuple(dict.fromkeys(ordered))
    return "".join(f"{requirement}\n" for requirement in unique)


def write_requirements(catalog: ProfileCatalog, profile_set: str, output: Path) -> None:
    """Atomically publish a generated direct-requirements view."""

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{output.name}.",
            dir=output.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(render_requirements(catalog, profile_set))
            handle.flush()
            os.fsync(handle.fileno())
        assert temporary is not None
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _project_metadata(
    path: Path, *, read_text: TextReader | None = None,
) -> tuple[list[str], Mapping[str, object]]:
    try:
        data = tomllib.loads(_profile_text(path, read_text))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"cannot load {path}: {exc}") from exc
    project = _table(data.get("project"), f"{path}: project")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise ProfileError(f"{path}: project.dependencies must be a string list")
    tool = _table(data.get("tool"), f"{path}: tool")
    uv = _table(tool.get("uv"), f"{path}: tool.uv")
    return dependencies, uv


def validate_catalog(
    catalog: ProfileCatalog, *, require_locks: bool = True, read_text: TextReader | None = None,
) -> None:
    """Validate independence, lock presence, and the hard line ceiling."""

    try:
        root_data = tomllib.loads(_profile_text(catalog.root / "pyproject.toml", read_text))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"cannot load root pyproject.toml: {exc}") from exc
    root_project = _table(root_data.get("project"), "root project")
    if "optional-dependencies" in root_project or "dependency-groups" in root_data:
        raise ProfileError("root project must contain only the deployable core dependency closure")

    seen_projects: set[Path] = set()
    for profile in catalog.profiles.values():
        if profile.project in seen_projects:
            raise ProfileError(f"profile project is reused: {profile.project}")
        seen_projects.add(profile.project)
        dependencies, uv = _project_metadata(profile.project / "pyproject.toml", read_text=read_text)
        if uv.get("package") is not False:
            raise ProfileError(f"profile {profile.name!r} must set tool.uv.package=false")
        if "workspace" in uv:
            raise ProfileError(f"profile {profile.name!r} must not declare a uv workspace")
        if any("general-ludd-agent" in dependency.lower() for dependency in dependencies):
            raise ProfileError(f"profile {profile.name!r} must not depend on the root project")

    if not require_locks:
        return
    for project in (catalog.root, *seen_projects):
        lock = project / "uv.lock"
        try:
            lines = len(_profile_text(lock, read_text).splitlines())
        except OSError as exc:
            raise ProfileError(f"missing lock for project {project}: {exc}") from exc
        if lines > catalog.max_lock_lines:
            raise ProfileError(
                f"{lock.relative_to(catalog.root)} has {lines} lines; "
                f"maximum is {catalog.max_lock_lines}"
            )


def locked_package_version(
    catalog: ProfileCatalog,
    *,
    profile: str,
    package: str,
) -> str:
    """Return one package's unambiguous version from an exact profile lock."""

    if not profile:
        raise ProfileError("profile must be non-empty")
    if not package:
        raise ProfileError("package must be non-empty")
    selected = catalog.profiles.get(profile)
    if selected is None:
        raise ProfileError(f"unknown profile {profile!r}")
    lock_path = selected.project / "uv.lock"
    try:
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"cannot load lock {lock_path}: {exc}") from exc
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise ProfileError(f"{lock_path} does not lock package {package!r}")
    matches: list[Mapping[str, object]] = []
    for index, value in enumerate(packages):
        entry = _table(value, f"{lock_path}: package[{index}]")
        if entry.get("name") == package:
            matches.append(entry)
    if not matches:
        raise ProfileError(f"{lock_path} does not lock package {package!r}")
    if len(matches) != 1:
        raise ProfileError(f"{lock_path} must lock package {package!r} exactly once")
    version = matches[0].get("version")
    if not isinstance(version, str) or not version:
        raise ProfileError(f"{lock_path} has an invalid version for package {package!r}")
    return version


def _run_command(command: Command) -> None:
    environment = os.environ.copy()
    environment.update(command.environment)
    # CI exports UV_NO_SYNC to protect the completed composed environment from
    # later ``uv run`` calls. Explicit profile-manager mutations must opt back in.
    environment["UV_NO_SYNC"] = "0"
    print(f"PROFILE_COMMAND {' '.join(command.argv)}", flush=True)
    subprocess.run(command.argv, check=True, env=environment)


def execute_commands(commands: Iterable[Command], run: RunCommand = _run_command) -> None:
    """Execute commands in order while retaining observable phase output."""

    for command in commands:
        run(command)


def _environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _promote_environment(staging: Path, target: Path) -> None:
    backup = target.with_name(f".{target.name}.profile-backup-{os.getpid()}")
    if backup.exists():
        raise ProfileError(f"refusing to overwrite stale profile backup {backup}")
    moved_target = False
    try:
        if target.exists():
            target.rename(backup)
            moved_target = True
        staging.rename(target)
    except BaseException:
        if moved_target and not target.exists() and backup.exists():
            backup.rename(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def sync_profile_set(
    catalog: ProfileCatalog,
    *,
    profile_set: str,
    environment: Path,
    python: str | None,
    uv: str = "uv",
    validate_only: bool = False,
    install_project: bool = True,
    run: RunCommand = _run_command,
) -> None:
    """Check locks, build a staged environment, and atomically promote it."""

    validate_catalog(catalog)
    execute_commands(
        lock_commands(catalog, profile_set=profile_set, check=True, uv=uv),
        run,
    )
    environment = environment.resolve()
    environment.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(
        tempfile.mkdtemp(prefix=f".{environment.name}.profile-stage-", dir=environment.parent)
    )
    staging = stage_parent / "environment"
    try:
        commands = sync_commands(
            catalog,
            profile_set=profile_set,
            environment=environment,
            staging_environment=staging,
            python=python,
            uv=uv,
            dry_run=validate_only,
            install_project=install_project,
        )
        execute_commands(commands, run)
        if validate_only:
            return
        execute_commands(
            (
                Command(
                    (uv, "pip", "check", "--python", str(_environment_python(staging))),
                    {},
                ),
            ),
            run,
        )
        _promote_environment(staging, environment)
        print(
            f"PROFILE_SYNC_PASS set={profile_set} environment={environment}",
            flush=True,
        )
    finally:
        if stage_parent.exists():
            shutil.rmtree(stage_parent)


def runtime_metadata() -> dict[str, object]:
    """Read the selected interpreter's distribution metadata without loading plugins."""
    import importlib.metadata
    import json
    import platform

    from ci_batch_receipts import _installed_distributions_sha256, _pytest_plugin_identity
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    request = json.load(sys.stdin)
    requirements = [Requirement(value) for value in request["requirements"]]
    plugins = tuple(importlib.metadata.entry_points(group="pytest11"))
    names = {
        canonicalize_name(requirement.name)
        for requirement in requirements
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""})
    }
    names.update(canonicalize_name(point.dist.name) for point in plugins if point.dist)
    versions: dict[str, str] = {}
    mismatches: list[str] = []
    for name in sorted(names):
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{name}: absent")
            continue
        versions[name] = version
        if version not in request["locked_versions"].get(name, []):
            mismatches.append(f"{name}=={version}: selected lock mismatch")
    for requirement in requirements:
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        installed = versions.get(canonicalize_name(requirement.name))
        if installed is not None and installed not in requirement.specifier:
            mismatches.append(f"{requirement}: installed {installed} violates profile")
    return {
        "version": platform.python_version(), "implementation": platform.python_implementation(),
        "prefix": sys.prefix, "base_prefix": sys.base_prefix, "executable": sys.executable,
        "mismatches": mismatches, "versions": versions,
        "plugins": _pytest_plugin_identity(),
        "installed_distributions_sha256": _installed_distributions_sha256(),
    }


def inspect_runtime(
    catalog: ProfileCatalog, *, profile_set: str, environment: Path | None,
    python: str, validate_only: bool = False,
) -> dict[str, object]:
    """Verify one existing registered toolchain slot; never install or modify it."""
    import hashlib
    import json
    import re

    if TYPE_CHECKING:
        from active_work_status import _repository_roots
        from ci_batch_receipts import file_sha256
        from resource_arbiter import resource_root
    elif __package__:
        from scripts.active_work_status import _repository_roots
        from scripts.ci_batch_receipts import file_sha256
        from scripts.resource_arbiter import resource_root
    else:
        from active_work_status import _repository_roots
        from ci_batch_receipts import file_sha256
        from resource_arbiter import resource_root

    if re.fullmatch(r"3\.(?:11|12|13|14)\.[0-9]+", python) is None:
        raise ProfileError("inspection requires an exact supported Python patch version")
    frozen = {catalog.manifest: catalog.manifest.read_bytes()}

    def read_frozen(path: Path) -> str:
        return frozen[path].decode("utf-8")

    # Select and validate from the same bytes whose hashes are reported.
    catalog = load_catalog(catalog.root, catalog.manifest, read_text=read_frozen)
    for project in (catalog.root, *(profile.project for profile in catalog.profiles.values())):
        for filename in ("pyproject.toml", "uv.lock"):
            path = project / filename
            frozen[path] = path.read_bytes()
    fingerprint = {
        str(path.relative_to(catalog.root)): hashlib.sha256(payload).hexdigest()
        for path, payload in frozen.items()
    }
    selected = _selected(catalog, profile_set)
    validate_catalog(catalog, read_text=read_frozen)
    roots = _repository_roots()
    minor = python.rsplit(".", 1)[0]
    slots = {
        root: Path(f"{resource_root(root)}-toolchain") / "ci-shards" / f"python-{minor}"
        for root in roots
    }
    candidate = environment if environment is not None else slots.get(catalog.root)
    if candidate is None or not candidate.is_absolute() or ".." in candidate.parts or candidate not in slots.values():
        raise ProfileError("inspection requires an exact registered toolchain environment")
    result: dict[str, object] = {
        "state": "plan", "environment": str(candidate), "python": python,
        "profile_set": profile_set, "gate_evidence": False, "release_ready": False,
    }
    if validate_only:
        return result
    cfg = candidate / "pyvenv.cfg"
    executable = _environment_python(candidate)
    protected = (candidate, candidate.parent, candidate.parent.parent, cfg, executable.parent)
    shadow = executable.parent / "pyvenv.cfg"
    if any(path.is_symlink() for path in protected):
        raise ProfileError("runtime environment and configuration must not be symlinks")
    if shadow.exists() or shadow.is_symlink():
        raise ProfileError("executable-directory pyvenv.cfg shadow is refused")
    if not cfg.is_file() or cfg.stat().st_size > 16384 or not executable.is_file():
        raise ProfileError(f"existing runtime environment is unavailable: {candidate}")
    cfg_bytes = cfg.read_bytes()
    site_flags = [
        value.strip().lower()
        for line in cfg_bytes.decode("utf-8").splitlines() if "=" in line
        for key, value in (line.split("=", 1),)
        if key.strip().lower() == "include-system-site-packages"
    ]
    if site_flags != ["false"]:
        raise ProfileError("runtime requires one normalized system-site-packages=false declaration")
    locked_versions: dict[str, set[str]] = {}
    for project in (catalog.root, *(profile.project for profile in selected)):
        lock = tomllib.loads(read_frozen(project / "uv.lock"))
        for value in lock.get("package", []):
            package = _table(value, "locked runtime package")
            name, version = package.get("name"), package.get("version")
            if not isinstance(name, str) or not isinstance(version, str):
                raise ProfileError("runtime lock package must contain a name and version")
            locked_versions.setdefault(re.sub(r"[-_.]+", "-", name).lower(), set()).add(version)
    def identities() -> tuple[tuple[int, ...], ...]:
        values = (path.stat() for path in (candidate, cfg, executable))
        return tuple(
            (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            for value in values
        )

    original = identities()
    request = {
        "requirements": render_requirements(catalog, profile_set, read_text=read_frozen).splitlines(),
        "locked_versions": {name: sorted(versions) for name, versions in locked_versions.items()},
    }
    code = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);"
        "from dependency_profiles import runtime_metadata;"
        "print(json.dumps(runtime_metadata(),sort_keys=True))"
    )
    try:
        probe = subprocess.run(
            [str(executable), "-I", "-B", "-c", code, str(catalog.root / "scripts")],
            input=json.dumps(request), cwd=catalog.root, capture_output=True, text=True,
            check=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProfileError(f"bounded metadata probe failed: {exc}") from exc
    if len(probe.stdout) > 131072:
        raise ProfileError("runtime metadata output is oversized")
    try:
        metadata: object = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise ProfileError("runtime metadata output is malformed") from exc
    if not isinstance(metadata, dict):
        raise ProfileError("runtime metadata must be an object")
    digest = metadata.get("installed_distributions_sha256")
    if (
        metadata.get("version") != python or metadata.get("implementation") != "CPython"
        or metadata.get("prefix") != str(candidate)
        or not isinstance(metadata.get("base_prefix"), str)
        or metadata["base_prefix"] == str(candidate)
        or metadata.get("executable") != str(executable)
        or metadata.get("mismatches") != []
        or not isinstance(metadata.get("plugins"), dict)
        or not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None
    ):
        raise ProfileError(
            f"runtime identity/profile/lock mismatch: profile_set={profile_set!r} "
            f"input_sha256={fingerprint} runtime={metadata}"
        )
    current_fingerprint = {
        str(path.relative_to(catalog.root)): file_sha256(path) for path in frozen
    }
    if (
        current_fingerprint != fingerprint or identities() != original
        or cfg.read_bytes() != cfg_bytes or any(path.is_symlink() for path in protected)
        or shadow.exists() or shadow.is_symlink()
    ):
        raise ProfileError("runtime or profile inputs changed during inspection")
    result.update(
        state="verified", runtime=metadata, input_sha256=fingerprint,
        runtime_configuration_sha256=hashlib.sha256(cfg_bytes).hexdigest(),
    )
    return result



def _uv_workflow_pin(root: Path) -> tuple[str, bytes]:
    """Read the sole exact hosted managed-3.12 uv pin without another pin."""
    import re

    import yaml

    payload = _uv_read_bytes(root / ".github/workflows/build.yml", maximum=1048576)
    try:
        raw = yaml.load(payload, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    except yaml.YAMLError as exc:
        raise ProfileError(f"hosted uv workflow is malformed: {exc}") from exc
    jobs = _table(_table(raw, "workflow").get("jobs"), "workflow.jobs")
    gate = _table(jobs.get("gate"), "workflow.jobs.gate")
    steps = gate.get("steps")
    if not isinstance(steps, list):
        raise ProfileError("hosted gate steps are unavailable")
    matches: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if (str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
                and step.get("if") == "matrix.python-version == '3.12'"):
            settings = _table(step.get("with"), "managed-3.12 setup-uv.with")
            version = settings.get("version")
            python = settings.get("python-version")
            if (not isinstance(version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None
                    or not isinstance(python, str) or re.fullmatch(r"3\.12\.[0-9]+", python) is None):
                raise ProfileError("hosted managed-3.12 uv requires one exact stable pin")
            matches.append(version)
    if len(matches) != 1:
        raise ProfileError("hosted managed-3.12 uv pin must be unique")
    return matches[0], payload


def _uv_read_bytes(path: Path, *, maximum: int = 65536) -> bytes:
    """Read a bounded ordinary native metadata file, never a symlink."""
    uid = getattr(os, "getuid", lambda: None)()
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > maximum
            or (uid is not None and path.stat().st_uid != uid)):
        raise ProfileError(f"native uv metadata is missing, oversized or a symlink: {path}")
    payload = path.read_bytes()
    if len(payload) > maximum:
        raise ProfileError(f"native uv metadata exceeded its bound: {path}")
    return payload


def _uv_owned_paths(paths: Iterable[Path]) -> None:
    """Refuse preexisting symlink or foreign-owned acquisition path components."""
    uid = getattr(os, "getuid", lambda: None)()
    for path in paths:
        if path.is_symlink():
            raise ProfileError(f"native uv acquisition path is a symlink: {path}")
        if path.exists():
            value = path.stat()
            if not path.is_dir() or (uid is not None and value.st_uid != uid):
                raise ProfileError(f"native uv acquisition path ownership is unproven: {path}")


def _uv_check_lease(path: Path) -> None:
    """Reject symlink/foreign/held leases before the existing kernel owner."""
    if TYPE_CHECKING:
        from collection_lock import inspect_lease
    elif __package__:
        from scripts.collection_lock import inspect_lease
    else:
        from collection_lock import inspect_lease

    if path.exists() or path.is_symlink():
        _uv_read_bytes(path, maximum=512)
    if inspect_lease(path).state != "available":
        raise ProfileError(f"native uv acquisition lease is not available: {path}")


def _uv_version(executable: Path) -> str:
    """Probe only a bounded native version command."""
    import re

    uid = getattr(os, "getuid", lambda: None)()
    if (not executable.is_file() or not os.access(executable, os.X_OK)
            or (uid is not None and executable.stat().st_uid != uid)):
        raise ProfileError("native uv executable ownership is unproven")
    result = subprocess.run(
        [str(executable), "--version"], check=True, capture_output=True, text=True, timeout=10,
    )
    if len(result.stdout) > 256 or re.fullmatch(
        r"uv [0-9]+\.[0-9]+\.[0-9]+(?: \([^\r\n]*\))?\n?", result.stdout,
    ) is None:
        raise ProfileError("native uv version output is malformed")
    return result.stdout.split()[1]


def _uv_wheel_metadata(version: str) -> list[dict[str, object]]:
    """Read bounded official release metadata; native uv alone downloads wheels."""
    import json
    import time

    import httpx

    started = time.monotonic()
    try:
        with httpx.stream(
            "GET", f"https://pypi.org/pypi/uv/{version}/json", timeout=20,
            follow_redirects=False, trust_env=False,
        ) as response:
            response.raise_for_status()
            payload = bytearray()
            for block in response.iter_bytes():
                if time.monotonic() - started > 20:
                    raise ProfileError("official uv release metadata exceeded its read deadline")
                payload.extend(block)
                if len(payload) > 1048576:
                    raise ProfileError("official uv release metadata is oversized")
        raw = _table(json.loads(payload), "official uv release")
        files = raw.get("urls")
        if not isinstance(files, list) or not all(isinstance(value, dict) for value in files):
            raise ProfileError("official uv release wheel metadata is malformed")
        return files
    except (httpx.HTTPError, ValueError) as exc:
        raise ProfileError(f"official uv release metadata is unavailable: {exc}") from exc


def _verify_pinned_uv(leaf: Path, bootstrap: Path, version: str) -> dict[str, object]:
    """Verify native receipt, private exports, metadata and actual executable."""
    import base64
    import csv
    import hashlib
    import json
    import re
    from email.parser import Parser
    from urllib.parse import urlsplit

    from packaging.tags import parse_tag
    from packaging.utils import parse_wheel_filename

    environment = leaf / "tools/uv"
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    binary = scripts / f"uv{suffix}"
    executable = leaf / "bin" / f"uv{suffix}"
    python = _environment_python(environment)
    info = (environment / "Lib/site-packages" if os.name == "nt"
            else environment / "lib/python3.11/site-packages") / f"uv-{version}.dist-info"
    _uv_owned_paths((environment, scripts, leaf / "bin", info.parent.parent, info.parent, info))
    receipt_path = environment / "uv-receipt.toml"
    files = (receipt_path, environment / "pyvenv.cfg",
             info / "METADATA", info / "WHEEL", info / "RECORD")
    identity_paths = (leaf, leaf / "tools", environment, scripts, leaf / "bin", info,
                      *files, binary, scripts / f"uvx{suffix}", python,
                      executable, leaf / "bin" / f"uvx{suffix}")
    def identities() -> tuple[tuple[int, int, int, int], ...]:
        return tuple(
            (value.st_dev, value.st_ino, value.st_uid, value.st_mode)
            for value in (path.lstat() for path in identity_paths)
        )
    original = identities()
    frozen = {path: _uv_read_bytes(path, maximum=1048576) for path in files}
    receipt = _table(tomllib.loads(frozen[receipt_path].decode("utf-8")).get("tool"), "native uv tool")
    if receipt.get("requirements") != [{"name": "uv", "specifier": f"=={version}"}]:
        raise ProfileError("native uv receipt has a different exact requirement")
    if receipt.get("python") != str(bootstrap) or receipt.get("constraints") or receipt.get("overrides"):
        raise ProfileError("native uv receipt interpreter/constraints do not match")
    options = _table(receipt.get("options"), "native uv tool options")
    indexes = options.get("index")
    if (not isinstance(indexes, list) or len(indexes) != 1
            or not isinstance(indexes[0], dict)
            or str(indexes[0].get("url", "")).rstrip("/") != "https://pypi.org/simple"
            or indexes[0].get("default") is not True or options.get("find-links")):
        raise ProfileError("native uv receipt is not bound to official PyPI")
    points = receipt.get("entrypoints")
    expected = [
        {"name": f"{name}{suffix}", "install-path": str(leaf / "bin" / f"{name}{suffix}"), "from": "uv"}
        for name in ("uv", "uvx")
    ]
    if points != expected:
        raise ProfileError("native uv receipt exports do not match private paths")
    for name in ("uv", "uvx"):
        target = scripts / f"{name}{suffix}"
        export = leaf / "bin" / f"{name}{suffix}"
        if target.is_symlink() or not target.is_file() or export.resolve(strict=True) != target:
            raise ProfileError("native uv export escapes its immutable tool environment")
    if python.resolve(strict=True) != bootstrap.resolve(strict=True):
        raise ProfileError("native uv tool interpreter differs from the selected existing interpreter")
    if (scripts / "pyvenv.cfg").exists() or (scripts / "pyvenv.cfg").is_symlink():
        raise ProfileError("native uv tool configuration is shadowed")
    flags = [
        value.strip().lower()
        for line in frozen[environment / "pyvenv.cfg"].decode("utf-8").splitlines() if "=" in line
        for key, value in (line.split("=", 1),)
        if key.strip().lower() == "include-system-site-packages"
    ]
    if flags != ["false"]:
        raise ProfileError("native uv tool is not isolated from system site packages")
    metadata = Parser().parsestr(frozen[info / "METADATA"].decode("utf-8"))
    if metadata.get_all("Name") != ["uv"] or metadata.get_all("Version") != [version]:
        raise ProfileError("native uv installed distribution does not match its pin")
    wheel = Parser().parsestr(frozen[info / "WHEEL"].decode("utf-8"))
    tags = {tag for value in wheel.get_all("Tag", []) for tag in parse_tag(value)}
    matches: list[dict[str, object]] = []
    for candidate in _uv_wheel_metadata(version):
        if candidate.get("packagetype") != "bdist_wheel":
            continue
        filename, url = candidate.get("filename"), candidate.get("url")
        if not isinstance(filename, str) or not isinstance(url, str):
            continue
        name, release, _, published_tags = parse_wheel_filename(filename)
        if (str(name) == "uv" and str(release) == version and tags and tags == published_tags
                and candidate.get("packagetype") == "bdist_wheel" and not candidate.get("yanked")):
            parsed = urlsplit(url)
            digests = _table(candidate.get("digests"), "official uv wheel digests")
            digest = digests.get("sha256")
            if (parsed.scheme != "https" or parsed.netloc != "files.pythonhosted.org"
                    or not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None):
                raise ProfileError("official uv wheel identity is malformed")
            matches.append({"wheel_filename": filename, "wheel_url": url, "wheel_sha256": digest})
    if len(matches) != 1:
        raise ProfileError("installed uv WHEEL tags do not identify one official binary wheel")
    with binary.open("rb") as binary_file:
        digest = hashlib.file_digest(binary_file, "sha256").hexdigest()
    encoded = base64.urlsafe_b64encode(bytes.fromhex(digest)).decode().rstrip("=")
    records = [
        row for row in csv.reader(frozen[info / "RECORD"].decode("utf-8").splitlines())
        if len(row) == 3 and (info.parent / row[0]).resolve() == binary
    ]
    if len(records) != 1 or records[0][1:] != [f"sha256={encoded}", str(binary.stat().st_size)]:
        raise ProfileError("native uv executable does not match its installed RECORD")
    probe = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import json,sys;print(json.dumps([sys.prefix,sys.executable,list(sys.version_info[:3])]))"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    if len(probe.stdout) > 4096 or json.loads(probe.stdout) != [
        str(environment), str(python), list(sys.version_info[:3]),
    ]:
        raise ProfileError("native uv environment interpreter identity is inconsistent")
    if _uv_version(executable) != version:
        raise ProfileError("native uv executable version does not match the workflow")
    _uv_owned_paths((environment, scripts, leaf / "bin", info.parent.parent, info.parent, info))
    with binary.open("rb") as binary_file:
        current_digest = hashlib.file_digest(binary_file, "sha256").hexdigest()
    if (identities() != original or current_digest != digest
            or any(_uv_read_bytes(path, maximum=1048576) != payload for path, payload in frozen.items())
            or python.resolve(strict=True) != bootstrap.resolve(strict=True)
            or (scripts / "pyvenv.cfg").exists() or (scripts / "pyvenv.cfg").is_symlink()
            or any(
                (leaf / "bin" / f"{name}{suffix}").resolve(strict=True) != scripts / f"{name}{suffix}"
                for name in ("uv", "uvx")
            )):
        raise ProfileError("native uv installation changed during verification")
    return {**matches[0], "executable": str(executable), "executable_sha256": digest,
            "artifact_hash_locked": False, "wheel_identity_kind": "published-tag-match",
            "native_receipt_sha256": hashlib.sha256(frozen[receipt_path]).hexdigest()}



def _require_uv_acquisition_parent_version() -> None:
    if sys.version_info[:2] != (3, 11):
        raise ProfileError("native uv acquisition requires an existing registered Python 3.11 interpreter")


def acquire_pinned_uv(root: Path, *, validate_only: bool = True) -> dict[str, object]:
    """Acquire only the hosted uv pin in one fresh immutable native tool leaf."""
    import hashlib
    import uuid
    from contextlib import ExitStack

    if TYPE_CHECKING:
        from active_work_status import _repository_roots
        from clean_ci_shard_scratch import ProcessInspectionError, _active_process_pids
        from collection_lock import collection_lock
        from resource_arbiter import resource_path, resource_root
        from stream_command import stream_command
    elif __package__:
        from scripts.active_work_status import _repository_roots
        from scripts.clean_ci_shard_scratch import ProcessInspectionError, _active_process_pids
        from scripts.collection_lock import collection_lock
        from scripts.resource_arbiter import resource_path, resource_root
        from scripts.stream_command import stream_command
    else:
        from active_work_status import _repository_roots
        from clean_ci_shard_scratch import ProcessInspectionError, _active_process_pids
        from collection_lock import collection_lock
        from resource_arbiter import resource_path, resource_root
        from stream_command import stream_command

    root = root.resolve()
    roots = _repository_roots()
    if root not in roots:
        raise ProfileError("native uv acquisition requires a registered checkout")
    version, workflow = _uv_workflow_pin(root)
    namespace = resource_root(root)
    toolchain = Path(f"{namespace}-toolchain")
    leaf = toolchain / "pinned-uv" / version
    bootstrap = Path(sys.executable)
    slots = {
        Path(f"{resource_root(selected)}-toolchain") / "ci-shards/python-3.11"
        for selected in roots
    }
    _require_uv_acquisition_parent_version()
    if (Path(sys.prefix) not in slots
            or bootstrap.parent.parent != Path(sys.prefix) or not bootstrap.is_file()):
        raise ProfileError("native uv acquisition requires an existing registered Python 3.11 interpreter")
    _uv_owned_paths((Path(sys.prefix), bootstrap.parent))
    paths = (namespace.parent, namespace, toolchain, leaf.parent, leaf)
    _uv_owned_paths(paths)
    if leaf.exists() and not (leaf / "tools/uv/uv-receipt.toml").is_file():
        raise ProfileError("partial native uv acquisition leaf is preserved")
    result: dict[str, object] = {
        "state": "plan", "version": version,
        "executable": str(leaf / "bin" / ("uv.exe" if os.name == "nt" else "uv")),
        "bootstrap": str(bootstrap), "leaf": str(leaf),
        "workflow_sha256": hashlib.sha256(workflow).hexdigest(),
        "gate_evidence": False, "release_ready": False, "artifact_hash_locked": False,
    }
    if validate_only:
        return result

    def inactive() -> None:
        try:
            if _active_process_pids(leaf):
                raise ProfileError("native uv acquisition leaf has an active process")
        except ProcessInspectionError as exc:
            raise ProfileError("native uv acquisition process inventory is unavailable") from exc

    def unchanged() -> None:
        _uv_owned_paths(paths)
        if (root / ".github/workflows/build.yml").read_bytes() != workflow:
            raise ProfileError("hosted uv workflow input changed during acquisition")

    def leaf_identity() -> tuple[int, int, int, int]:
        value = leaf.lstat()
        return value.st_dev, value.st_ino, value.st_uid, value.st_mode

    try:
        acquisition_lease = resource_path(f"pinned-uv-{version}", root)
        _uv_check_lease(acquisition_lease)
        with collection_lock(acquisition_lease, timeout=0):
            created: tuple[int, int, int, int] | None = None
            drained = False
            try:
                unchanged()
                inactive()
                reused = leaf.exists()
                if not reused:
                    installer_text = shutil.which("uv")
                    if installer_text is None:
                        raise ProfileError("existing native uv installer is unavailable")
                    installer = Path(installer_text).resolve(strict=True)
                    installer_version = _uv_version(installer)
                    leaf.parent.mkdir(parents=True, exist_ok=True)
                    _uv_owned_paths(paths)
                    leaf.mkdir(mode=0o700)
                    created = leaf_identity()
                    environment = {
                        key: value for key, value in os.environ.items()
                        if not key.startswith(("UV_", "PIP_", "PYTHON"))
                        and key not in {"VIRTUAL_ENV", "CONDA_PREFIX", "__PYVENV_LAUNCHER__"}
                    }
                    environment.update({
                        "UV_TOOL_DIR": str(leaf / "tools"), "UV_TOOL_BIN_DIR": str(leaf / "bin"),
                        "UV_CACHE_DIR": str(leaf / "cache"), "UV_PYTHON_INSTALL_DIR": str(leaf / "python"),
                        "UV_PYTHON_DOWNLOADS": "never", "UV_CONCURRENT_DOWNLOADS": "1",
                        "UV_CONCURRENT_INSTALLS": "1", "UV_CONCURRENT_BUILDS": "1",
                        "UV_HTTP_RETRIES": "0", "UV_HTTP_TIMEOUT": "30",
                        "PATH": str(leaf / "bin") + os.pathsep + os.environ.get("PATH", ""),
                    })
                    run_id = f"uv-{version}-{uuid.uuid4().hex}"
                    log = root / ".gate-logs" / f"{run_id}.log"
                    result.update(installer=str(installer), installer_version=installer_version, log=str(log))
                    code = stream_command(
                        [str(installer), "--no-config", "tool", "install", "--python", str(bootstrap),
                         "--no-python-downloads", "--no-build", "--default-index", "https://pypi.org/simple",
                         f"uv=={version}"],
                        log, observed_root=root / ".gate-logs/observed", label="pinned-uv-acquisition",
                        run_id=run_id, heartbeat_seconds=10, quiet_seconds=60, max_seconds=180,
                        environment=environment, cwd=root,
                    )
                    drained = True
                    if code != 0:
                        raise ProfileError(f"native uv installer failed rc={code}; raw log={log}")
                unchanged()
                inactive()
                _uv_owned_paths((leaf / "tools",))
                # Native uv uses this same tools-directory kernel lock.
                _uv_check_lease(leaf / "tools/.lock")
                with collection_lock(leaf / "tools/.lock", timeout=0):
                    result.update(_verify_pinned_uv(leaf, bootstrap, version))
                    unchanged()
                    inactive()
                result.update(state="verified", reused=reused)
                return result
            except (ProfileError, OSError, ValueError, subprocess.SubprocessError) as exc:
                retained = ""
                if created is not None:
                    if not drained:
                        retained = "; fresh leaf retained: native child drain is unconfirmed"
                    else:
                        try:
                            with ExitStack() as cleanup_locks:
                                tools = leaf / "tools"
                                if tools.exists() or tools.is_symlink():
                                    _uv_owned_paths((tools,))
                                    _uv_check_lease(tools / ".lock")
                                    cleanup_locks.enter_context(collection_lock(tools / ".lock", timeout=0))
                                _uv_owned_paths(paths)
                                if leaf_identity() != created:
                                    raise ProfileError("fresh leaf identity changed")
                                inactive()
                                # Created inode + held acquisition/native leases +
                                # confirmed child drain authorize this removal.
                                # Census absence is only an additional refusal.
                                shutil.rmtree(leaf)
                        except (ProfileError, OSError) as cleanup:
                            retained = f"; fresh leaf retained: {cleanup}"
                raise ProfileError(f"native uv acquisition refused: {exc}{retained}") from exc
    except OSError as exc:
        raise ProfileError(f"native uv acquisition lease is unavailable: {exc}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("acquire-uv", "audit", "check", "export", "inspect", "lock", "locked-version", "sync"),
    )
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--manifest", default="config/dependency_profiles.toml")
    parser.add_argument("--set", dest="profile_set")
    parser.add_argument("--environment", default=".venv")
    parser.add_argument("--output")
    parser.add_argument("--profile")
    parser.add_argument("--package")
    parser.add_argument("--python")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-install-project", action="store_true")
    parser.add_argument("--ignore-vulnerability", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the profile catalog CLI."""

    args = _parser().parse_args(argv)
    if args.action == "acquire-uv":
        if (args.package or args.profile or args.profile_set or args.output or args.python
                or args.uv != "uv" or args.manifest != "config/dependency_profiles.toml"
                or args.environment != ".venv" or args.no_install_project or args.ignore_vulnerability):
            _parser().error("acquire-uv accepts only --root and --validate-only")
        import json

        try:
            print(json.dumps(acquire_pinned_uv(Path(args.root), validate_only=args.validate_only), sort_keys=True))
        except (ProfileError, OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"PROFILE_ERROR {exc}", file=sys.stderr, flush=True)
            return 1
        return 0
    try:
        catalog = load_catalog(args.root, args.manifest)
        if args.action == "inspect":
            if not args.profile_set or not args.python:
                raise ProfileError("inspect requires --set and an exact --python")
            import json

            try:
                report = inspect_runtime(
                    catalog, profile_set=args.profile_set,
                    environment=None if args.environment == ".venv" else Path(args.environment),
                    python=args.python, validate_only=args.validate_only,
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                raise ProfileError(f"runtime inspection failed: {exc}") from exc
            print(json.dumps(report, sort_keys=True))
        elif args.action == "lock":
            execute_commands(
                lock_commands(
                    catalog, profile_set=args.profile_set, check=False, uv=args.uv, profile=args.profile,
                )
            )
            validate_catalog(catalog)
        elif args.action == "check":
            validate_catalog(catalog)
            execute_commands(
                lock_commands(
                    catalog, profile_set=args.profile_set, check=True, uv=args.uv, profile=args.profile,
                )
            )
        elif args.action == "audit":
            if not args.profile_set:
                raise ProfileError("audit requires --set")
            validate_catalog(catalog)
            execute_commands(
                audit_commands(
                    catalog,
                    profile_set=args.profile_set,
                    ignored_vulnerabilities=args.ignore_vulnerability,
                    uv=args.uv,
                )
            )
        elif args.action == "export":
            if not args.profile_set or not args.output:
                raise ProfileError("export requires --set and --output")
            write_requirements(catalog, args.profile_set, Path(args.output))
        elif args.action == "locked-version":
            if not args.profile or not args.package:
                raise ProfileError("locked-version requires --profile and --package")
            print(
                locked_package_version(
                    catalog,
                    profile=args.profile,
                    package=args.package,
                ),
                flush=True,
            )
        else:
            if not args.profile_set:
                raise ProfileError("sync requires --set")
            environment = Path(args.environment)
            if not environment.is_absolute():
                environment = catalog.root / environment
            sync_profile_set(
                catalog,
                profile_set=args.profile_set,
                environment=environment,
                python=args.python,
                uv=args.uv,
                validate_only=args.validate_only,
                install_project=not args.no_install_project,
            )
    except (ProfileError, subprocess.CalledProcessError) as exc:
        print(f"PROFILE_ERROR {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
