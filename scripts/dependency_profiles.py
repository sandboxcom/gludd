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
) -> ProfileCatalog:
    """Load and structurally validate the dependency-profile catalog."""

    repo_root = Path(root).resolve()
    manifest_path = Path(manifest)
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path
    try:
        raw = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
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
) -> tuple[Command, ...]:
    """Return uv lock commands for root plus a set, or every project."""

    projects = (catalog.root, *[profile.project for profile in _selected(catalog, profile_set)])
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


def render_requirements(catalog: ProfileCatalog, profile_set: str) -> str:
    """Render the direct requirement union for tools that accept requirements files."""

    root = tomllib.loads((catalog.root / "pyproject.toml").read_text(encoding="utf-8"))
    project = _table(root.get("project"), "root project")
    root_dependencies = project.get("dependencies")
    if not isinstance(root_dependencies, list) or not all(
        isinstance(item, str) for item in root_dependencies
    ):
        raise ProfileError("root project.dependencies must be a string list")

    ordered = list(root_dependencies)
    for profile in _selected(catalog, profile_set):
        dependencies, _ = _project_metadata(profile.project / "pyproject.toml")
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


def _project_metadata(path: Path) -> tuple[list[str], Mapping[str, object]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"cannot load {path}: {exc}") from exc
    project = _table(data.get("project"), f"{path}: project")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise ProfileError(f"{path}: project.dependencies must be a string list")
    tool = _table(data.get("tool"), f"{path}: tool")
    uv = _table(tool.get("uv"), f"{path}: tool.uv")
    return dependencies, uv


def validate_catalog(catalog: ProfileCatalog, *, require_locks: bool = True) -> None:
    """Validate independence, lock presence, and the hard line ceiling."""

    try:
        root_data = tomllib.loads((catalog.root / "pyproject.toml").read_text(encoding="utf-8"))
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
        dependencies, uv = _project_metadata(profile.project / "pyproject.toml")
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
            lines = len(lock.read_text(encoding="utf-8").splitlines())
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
    import json
    import re

    if TYPE_CHECKING:
        from active_work_status import _repository_roots
        from ci_batch_receipts import file_sha256
        from resource_arbiter import resource_root
    elif __package__:
        from ci_batch_receipts import file_sha256

        from scripts.active_work_status import _repository_roots
        from scripts.resource_arbiter import resource_root
    else:
        from active_work_status import _repository_roots
        from ci_batch_receipts import file_sha256
        from resource_arbiter import resource_root

    if re.fullmatch(r"3\.(?:11|12|13|14)\.[0-9]+", python) is None:
        raise ProfileError("inspection requires an exact supported Python patch version")
    selected = _selected(catalog, profile_set)
    validate_catalog(catalog)
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
    for path in (candidate, candidate.parent, candidate.parent.parent, cfg):
        if path.is_symlink():
            raise ProfileError("runtime environment and configuration must not be symlinks")
    executable = _environment_python(candidate)
    if not cfg.is_file() or cfg.stat().st_size > 16384 or not executable.is_file():
        raise ProfileError(f"existing runtime environment is unavailable: {candidate}")
    config = dict(
        line.split("=", 1) for line in cfg.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    if not any(key.strip().lower() == "include-system-site-packages" and value.strip().lower() == "false"
               for key, value in config.items()):
        raise ProfileError("runtime must exclude system site-packages")
    inputs = [catalog.manifest]
    locked_versions: dict[str, set[str]] = {}
    for project in (catalog.root, *(profile.project for profile in selected)):
        inputs.extend((project / "pyproject.toml", project / "uv.lock"))
        lock = tomllib.loads((project / "uv.lock").read_text(encoding="utf-8"))
        for value in lock.get("package", []):
            package = _table(value, "locked runtime package")
            name, version = package.get("name"), package.get("version")
            if not isinstance(name, str) or not isinstance(version, str):
                raise ProfileError("runtime lock package must contain a name and version")
            locked_versions.setdefault(re.sub(r"[-_.]+", "-", name).lower(), set()).add(version)
    fingerprint = {str(path.relative_to(catalog.root)): file_sha256(path) for path in inputs}
    def identities() -> tuple[tuple[int, ...], ...]:
        values = (path.stat() for path in (candidate, cfg, executable))
        return tuple(
            (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            for value in values
        )

    original = identities()
    request = {
        "requirements": render_requirements(catalog, profile_set).splitlines(),
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
    if (
        identities() != original
        or {str(path.relative_to(catalog.root)): file_sha256(path) for path in inputs} != fingerprint
    ):
        raise ProfileError("runtime or profile inputs changed during inspection")
    result.update(state="verified", runtime=metadata, input_sha256=fingerprint)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("audit", "check", "export", "inspect", "lock", "locked-version", "sync"),
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
                lock_commands(catalog, profile_set=args.profile_set, check=False, uv=args.uv)
            )
            validate_catalog(catalog)
        elif args.action == "check":
            validate_catalog(catalog)
            execute_commands(
                lock_commands(catalog, profile_set=args.profile_set, check=True, uv=args.uv)
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
