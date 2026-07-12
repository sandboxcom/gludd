"""Verify mypy coverage of all src/ packages and pre-commit config existence.

E.3 — Fix lint/type config gaps.
"""
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
SRC_ROOT = PROJECT_ROOT / "src" / "general_ludd"


def _all_src_packages() -> list[str]:
    """Return package names (general_ludd.X) for every __init__.py under src/."""
    packages: list[str] = []
    for py_file in SRC_ROOT.rglob("__init__.py"):
        rel = Path(py_file).resolve().parent.relative_to(SRC_ROOT.parent)
        pkg = str(rel).replace("/", ".").replace("\\", ".")
        packages.append(pkg)
    return sorted(packages)


def _load_pyproject() -> dict:
    with open(PYPROJECT, "rb") as fh:
        return tomllib.load(fh)


def _mypy_excludes() -> list[str]:
    cfg = _load_pyproject()
    return cfg.get("tool", {}).get("mypy", {}).get("exclude", [])


def _mypy_overrides_modules() -> set[str]:
    """Return the set of module patterns that have [[tool.mypy.overrides]] entries."""
    cfg = _load_pyproject()
    modules: set[str] = set()
    for override in cfg.get("tool", {}).get("mypy", {}).get("overrides", []):
        mod = override.get("module")
        if isinstance(mod, str):
            modules.add(mod)
        elif isinstance(mod, list):
            modules.update(mod)
    return modules


class TestMypyConfigGaps:
    def test_no_src_package_excluded(self):
        excludes = _mypy_excludes()
        packages = _all_src_packages()

        for pkg in packages:
            for exc in excludes:
                assert not pkg.startswith(exc), (
                    f"Package {pkg} matches mypy exclude pattern {exc!r}"
                )

    def test_src_packages_present(self):
        packages = _all_src_packages()
        assert len(packages) > 10, f"Expected >10 src packages, found {len(packages)}"

    def test_security_package_not_excluded(self):
        packages = _all_src_packages()
        security_pkgs = [p for p in packages if p.startswith("general_ludd.security")]
        assert len(security_pkgs) > 0, "No security packages found"
        excludes = _mypy_excludes()
        for pkg in security_pkgs:
            for exc in excludes:
                assert not pkg.startswith(exc), (
                    f"Security package {pkg} matches mypy exclude {exc!r}"
                )

    def test_tests_have_mypy_override(self):
        overrides = _mypy_overrides_modules()
        assert "tests" in overrides or "tests.*" in overrides, (
            "tests / tests.* must appear in [[tool.mypy.overrides]] — "
            "tests should be type-checked (with relaxed rules), not excluded"
        )

    def test_exclude_does_not_contain_src(self):
        excludes = _mypy_excludes()
        for exc in excludes:
            assert not exc.startswith("src"), (
                f"mypy exclude pattern {exc!r} would exclude source tree"
            )
            assert not exc.startswith("general_ludd"), (
                f"mypy exclude pattern {exc!r} would exclude source tree"
            )

    def test_pre_commit_config_exists(self):
        path = PROJECT_ROOT / ".pre-commit-config.yaml"
        assert path.exists(), ".pre-commit-config.yaml must exist"
        assert path.stat().st_size > 0, ".pre-commit-config.yaml is empty"
