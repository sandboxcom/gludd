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


class ProfileError(RuntimeError):
    """Raised when the dependency-profile contract cannot be satisfied."""


@dataclass(frozen=True)
class Profile:
    """One independent uv project."""

    name: str
    project: Path


@dataclass(frozen=True)
class ProfileCatalog:
    """Validated profile metadata rooted in one checkout."""

    root: Path
    manifest: Path
    max_lock_lines: int
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


def sync_commands(
    catalog: ProfileCatalog,
    *,
    profile_set: str,
    environment: Path,
    staging_environment: Path,
    python: str | None,
    uv: str = "uv",
    dry_run: bool = False,
) -> tuple[Command, ...]:
    """Plan locked root/profile syncs into one explicit staged environment."""

    del environment  # The target is intentionally untouched until promotion.
    projects = (catalog.root, *[profile.project for profile in _selected(catalog, profile_set)])
    venv_argv = [uv, "venv", "--relocatable", str(staging_environment)]
    if python:
        venv_argv.extend(("--python", python))
    commands: list[Command] = [Command(tuple(venv_argv), {})]
    overlay = {"UV_PROJECT_ENVIRONMENT": str(staging_environment)}
    for index, project in enumerate(projects):
        argv = [uv, "sync", "--project", str(project), "--locked"]
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


def _run_command(command: Command) -> None:
    environment = os.environ.copy()
    environment.update(command.environment)
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "export", "lock", "sync"))
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--manifest", default="config/dependency_profiles.toml")
    parser.add_argument("--set", dest="profile_set")
    parser.add_argument("--environment", default=".venv")
    parser.add_argument("--output")
    parser.add_argument("--python")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the profile catalog CLI."""

    args = _parser().parse_args(argv)
    try:
        catalog = load_catalog(args.root, args.manifest)
        if args.action == "lock":
            execute_commands(
                lock_commands(catalog, profile_set=args.profile_set, check=False, uv=args.uv)
            )
            validate_catalog(catalog)
        elif args.action == "check":
            validate_catalog(catalog)
            execute_commands(
                lock_commands(catalog, profile_set=args.profile_set, check=True, uv=args.uv)
            )
        elif args.action == "export":
            if not args.profile_set or not args.output:
                raise ProfileError("export requires --set and --output")
            write_requirements(catalog, args.profile_set, Path(args.output))
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
            )
    except (ProfileError, subprocess.CalledProcessError) as exc:
        print(f"PROFILE_ERROR {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
