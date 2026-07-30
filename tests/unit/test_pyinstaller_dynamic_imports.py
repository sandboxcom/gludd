"""Regression coverage for dependencies imported dynamically at runtime."""

import tomllib
from pathlib import Path

_ROOT = Path(__file__).parents[2]


def test_safe_diskcache_serializer_is_bundled() -> None:
    spec = (_ROOT / "gludd.spec").read_text()

    assert "'msgpack'," in spec


def test_project_collections_are_bundled() -> None:
    spec = (_ROOT / "gludd.spec").read_text()

    assert "('collections', 'collections')" in spec


def test_frozen_daemon_runtime_is_bundled() -> None:
    spec = (_ROOT / "gludd.spec").read_text()

    assert "'gunicorn.app.wsgiapp'," in spec
    assert "'gunicorn.glogging'," in spec
    assert "'uvicorn_worker'," in spec


def test_gunicorn_type_stubs_are_declared_in_both_dev_sets() -> None:
    with (_ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)

    dependency_sets = (
        project["project"]["optional-dependencies"]["dev"],
        project["dependency-groups"]["dev"],
    )
    for dependencies in dependency_sets:
        assert any(
            dependency.startswith("types-gunicorn")
            for dependency in dependencies
        )
