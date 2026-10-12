"""Release-container startup and smoke observability regressions."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


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
