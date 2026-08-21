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


def test_container_smoke_fails_fast_and_prints_server_diagnostics() -> None:
    """A dead/unhealthy container is diagnosed before owned cleanup runs."""
    script = _container_smoke_script()

    assert "docker inspect" in script
    assert 'docker logs "$container_name"' in script
    assert "container exited before becoming healthy" in script.lower()
    assert "container did not become healthy" in script.lower()
