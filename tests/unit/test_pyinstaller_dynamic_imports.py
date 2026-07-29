"""Regression coverage for dependencies imported dynamically at runtime."""

from pathlib import Path


def test_safe_diskcache_serializer_is_bundled() -> None:
    spec = (Path(__file__).parents[2] / "gludd.spec").read_text()

    assert "'msgpack'," in spec
