"""Release-container startup and smoke observability regressions."""

import shlex
import shutil
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _wheel_force_include_sources() -> tuple[Path, ...]:
    section: object = tomllib.loads((ROOT / "pyproject.toml").read_text())
    for key in ("tool", "hatch", "build", "targets", "wheel", "force-include"):
        assert isinstance(section, dict)
        section = section[key]
    assert isinstance(section, dict) and section
    sources = []
    for source, destination in section.items():
        assert isinstance(source, str) and isinstance(destination, str)
        path = Path(source)
        assert not path.is_absolute() and ".." not in path.parts
        sources.append(path)
    return tuple(sources)


def _builder_copy_routes() -> list[tuple[Path, Path]]:
    """Read the actual simple context COPY statements before the wheel build."""
    dockerfile = (ROOT / "Dockerfile").read_text().replace("\\\n", " ")
    _, separator, builder = dockerfile.partition(" AS builder\n")
    assert separator
    builder, separator, _ = builder.partition("uv build --wheel")
    assert separator and "\nFROM " not in builder
    assert [line for line in builder.splitlines() if line.startswith("WORKDIR ")] == [
        "WORKDIR /app"
    ]
    routes = []
    for line in builder.splitlines():
        if not line.startswith("COPY "):
            continue
        tokens = shlex.split(line)
        assert len(tokens) >= 3 and not tokens[1].startswith(("--", "["))
        destination = Path(tokens[-1])
        assert not destination.is_absolute() and ".." not in destination.parts
        for token in tokens[1:-1]:
            source = Path(token)
            assert not source.is_absolute() and ".." not in source.parts
            assert (ROOT / source).exists(), source
            target = destination
            if (ROOT / source).is_file() and (tokens[-1].endswith("/") or len(tokens) > 3):
                target /= source.name
            routes.append((source, target))
    return routes


def test_container_builder_stages_every_declared_wheel_force_include(tmp_path: Path) -> None:
    """Repository inputs must also exist at their exact Hatch paths in /app."""
    routes = _builder_copy_routes()
    for source in _wheel_force_include_sources():
        original = ROOT / source
        assert original.exists(), source
        for context_source, destination in routes:
            if not source.is_relative_to(context_source):
                continue
            target = tmp_path / destination
            if (ROOT / context_source).is_dir():
                target /= source.relative_to(context_source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if original.is_dir():
                shutil.copytree(original, target, dirs_exist_ok=True)
            else:
                shutil.copy2(original, target)
        staged = tmp_path / source
        assert staged.exists(), f"Hatch wheel input absent from builder layout: {source}"
        assert staged.is_dir() == original.is_dir(), source
        if original.is_file():
            assert staged.read_bytes() == original.read_bytes(), source


def test_container_stages_only_declared_core_collection_files_after_frozen_sync() -> None:
    """The wheel adapter must not bring controller collections into core sync."""
    required = {
        source
        for source in _wheel_force_include_sources()
        if source.parts[0] == "collections"
    }
    routes = _builder_copy_routes()
    assert all(source != Path(".") for source, _destination in routes)
    copied = {source for source, _destination in routes if source.parts[0] == "collections"}
    assert copied == required and required
    dockerfile = (ROOT / "Dockerfile").read_text()
    for source in copied:
        assert (ROOT / source).is_file(), source
        assert dockerfile.index("--no-install-project") < dockerfile.index(f"COPY {source}")
        assert dockerfile.index(f"COPY {source}") < dockerfile.index("uv build --wheel")


def _container_smoke_script() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
    steps = workflow["jobs"]["container"]["steps"]
    return next(
        str(step["run"])
        for step in steps
        if step.get("name") == "Smoke container health endpoint"
    )


def test_release_container_runs_gunicorn_as_the_owned_foreground_service() -> None:
    """Tini must own one observable Gunicorn tree, not a CLI wrapper process."""
    dockerfile = (ROOT / "Dockerfile").read_text()

    entrypoint = next(
        line for line in dockerfile.splitlines() if line.startswith("ENTRYPOINT ")
    )
    assert '"gunicorn"' in entrypoint
    assert '"general_ludd.daemon:create_daemon_app()"' in entrypoint
    assert '"--bind", "0.0.0.0:8000"' in entrypoint
    assert '"gludd", "daemon"' not in entrypoint


def test_release_container_routes_server_logs_to_owned_stdio() -> None:
    """Startup exceptions must reach CI/container logs instead of DEVNULL."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    entrypoint = next(
        line for line in dockerfile.splitlines() if line.startswith("ENTRYPOINT ")
    )

    assert '"--error-logfile", "-"' in entrypoint
    assert '"--access-logfile", "-"' in entrypoint
    assert '"--capture-output"' in entrypoint


def test_release_container_starts_from_its_owned_writable_state_root() -> None:
    """Relative state such as ``.gludd`` must resolve below the owned volume."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    workdirs = [
        line.split(maxsplit=1)[1]
        for line in dockerfile.splitlines()
        if line.startswith("WORKDIR ")
    ]

    assert workdirs[-1] == "${APP_HOME}"
    assert "VOLUME [\"/var/lib/general-ludd\"]" in dockerfile
    assert dockerfile.index("chown -R gludd:gludd ${APP_HOME}") < dockerfile.index("USER gludd")


def test_container_smoke_fails_fast_and_prints_server_diagnostics() -> None:
    """A dead/unhealthy container is diagnosed before owned cleanup runs."""
    script = _container_smoke_script()

    assert "docker inspect" in script
    assert 'docker logs "$container_name"' in script
    assert "container exited before becoming healthy" in script.lower()
    assert "container did not become healthy" in script.lower()


def test_container_validates_committed_locks_before_injecting_artifact_version() -> None:
    """A branch build must not stale its dependency lock before frozen sync."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
    steps = workflow["jobs"]["container"]["steps"]
    build = next(step for step in steps if step.get("name") == "Build container image")
    for step in steps[: steps.index(build)]:
        script = str(step.get("run", ""))
        assert "pyproject.toml" not in script
        assert "src/general_ludd/__init__.py" not in script
    assert "VERSION=${{ env.VERSION }}" in build["with"]["build-args"]
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert dockerfile.index("scripts/dependency_profiles.py sync") < dockerfile.index("RUN sed -i")
    assert dockerfile.index("RUN sed -i") < dockerfile.index("uv build --wheel")


def test_container_build_context_excludes_owned_scratch_and_local_credentials() -> None:
    """Local caches and secrets must not be uploaded as container build input."""
    patterns = set((ROOT / ".dockerignore").read_text().splitlines())
    assert {
        ".git", ".venv", ".gate-logs", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", "infra/terraform/.plugin-cache", "**/.terraform",
        ".env", ".env.*", "**/*.tfstate", "**/*.tfstate.*", "**/*.tfvars",
    } <= patterns
    assert not {"src", "config", "requirements", "templates", "playbooks", "uv.lock"} & patterns
