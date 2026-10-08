"""Regression coverage for dependencies imported dynamically at runtime."""

import tomllib
from pathlib import Path

_ROOT = Path(__file__).parents[2]


def test_safe_diskcache_serializer_is_bundled() -> None:
    spec = (_ROOT / "gludd.spec").read_text()

    assert "'msgpack'," in spec


def test_project_collections_are_externalized_from_frozen_core() -> None:
    spec = (_ROOT / "gludd.spec").read_text()

    assert "('collections', 'collections')" not in spec
    assert (_ROOT / "config/ansible/execution-environment.yml").is_file()
    assert (_ROOT / "config/ansible/requirements.yml").is_file()


def test_frozen_daemon_runtime_is_bundled() -> None:
    spec = (_ROOT / "gludd.spec").read_text()

    assert "'gunicorn.app.wsgiapp'," in spec
    assert "'gunicorn.glogging'," in spec
    assert "'uvicorn_worker'," in spec


def test_gunicorn_type_stubs_are_declared_in_all_dev_profile_sets() -> None:
    with (
        _ROOT / "requirements/profiles/dev-quality/pyproject.toml"
    ).open("rb") as stream:
        profile = tomllib.load(stream)
    with (_ROOT / "config/dependency_profiles.toml").open("rb") as stream:
        catalog = tomllib.load(stream)

    assert any(
        dependency.startswith("types-gunicorn")
        for dependency in profile["project"]["dependencies"]
    )
    for set_name in ("development", "ci"):
        assert "dev-quality" in catalog["sets"][set_name]["profiles"]
