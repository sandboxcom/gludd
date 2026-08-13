"""Verify mypy coverage of all src/ packages and pre-commit config existence.

E.3 — Fix lint/type config gaps.
"""
import tomllib
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
MYPY_TESTS_CONFIG = PROJECT_ROOT / "config" / "mypy-tests.toml"
SRC_ROOT = PROJECT_ROOT / "src" / "general_ludd"


def _all_src_packages() -> list[str]:
    """Return package names (general_ludd.X) for every __init__.py under src/."""
    packages: list[str] = []
    for py_file in SRC_ROOT.rglob("__init__.py"):
        rel = Path(py_file).resolve().parent.relative_to(SRC_ROOT.parent)
        pkg = str(rel).replace("/", ".").replace("\\", ".")
        packages.append(pkg)
    return sorted(packages)


def _load_pyproject() -> dict[str, Any]:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def _mypy_excludes() -> list[str]:
    cfg = _load_pyproject()
    mypy_config = cast(dict[str, Any], cfg["tool"]["mypy"])
    excludes = mypy_config.get("exclude", [])
    if not isinstance(excludes, list) or not all(
        isinstance(pattern, str) for pattern in excludes
    ):
        raise TypeError("tool.mypy.exclude must be a list of strings")
    return cast(list[str], excludes)


class TestMypyConfigGaps:
    def test_no_src_package_excluded(self) -> None:
        excludes = _mypy_excludes()
        packages = _all_src_packages()

        for pkg in packages:
            for exc in excludes:
                assert not pkg.startswith(exc), (
                    f"Package {pkg} matches mypy exclude pattern {exc!r}"
                )

    def test_src_packages_present(self) -> None:
        packages = _all_src_packages()
        assert len(packages) > 10, f"Expected >10 src packages, found {len(packages)}"

    def test_security_package_not_excluded(self) -> None:
        packages = _all_src_packages()
        security_pkgs = [p for p in packages if p.startswith("general_ludd.security")]
        assert len(security_pkgs) > 0, "No security packages found"
        excludes = _mypy_excludes()
        for pkg in security_pkgs:
            for exc in excludes:
                assert not pkg.startswith(exc), (
                    f"Security package {pkg} matches mypy exclude {exc!r}"
                )

    def test_typecheck_target_actually_checks_tests(self) -> None:
        makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
        typecheck_recipe = makefile.split("\ntypecheck:\n", 1)[1].split("\n\n", 1)[0]
        assert "mypy -p general_ludd" in typecheck_recipe
        assert "--config-file config/mypy-tests.toml" in typecheck_recipe
        assert "tests/unit/test_config_gaps.py" in typecheck_recipe
        assert "-p tests" not in typecheck_recipe

    def test_typed_test_config_is_strict_and_has_no_suppressions(self) -> None:
        config = tomllib.loads(MYPY_TESTS_CONFIG.read_text(encoding="utf-8"))["tool"][
            "mypy"
        ]
        assert config["strict"] is True
        assert config["warn_unused_configs"] is True
        assert "disable_error_code" not in config
        assert "ignore_errors" not in config

    def test_make_preserves_capable_terminals_with_deterministic_fallback(self) -> None:
        makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
        assert "filter-out dumb unknown" in makefile
        assert "override TERM := xterm-256color" in makefile
        assert "export TERM" in makefile
        assert "export TERM := dumb" not in makefile

    def test_tests_do_not_import_source_tree_as_a_namespace_package(self) -> None:
        invalid_import = "from src." + "general_ludd"
        offenders = [
            str(path.relative_to(PROJECT_ROOT))
            for path in (PROJECT_ROOT / "tests").rglob("*.py")
            if invalid_import in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_exclude_does_not_contain_src(self) -> None:
        excludes = _mypy_excludes()
        for exc in excludes:
            assert not exc.startswith("src"), (
                f"mypy exclude pattern {exc!r} would exclude source tree"
            )
            assert not exc.startswith("general_ludd"), (
                f"mypy exclude pattern {exc!r} would exclude source tree"
            )

    def test_pre_commit_config_exists(self) -> None:
        path = PROJECT_ROOT / ".pre-commit-config.yaml"
        assert path.exists(), ".pre-commit-config.yaml must exist"
        assert path.stat().st_size > 0, ".pre-commit-config.yaml is empty"
