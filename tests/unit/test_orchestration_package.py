"""Smoke test: orchestration directory is a valid importable package."""


def test_orchestration_package_importable() -> None:
    import general_ludd.orchestration  # noqa: F401
