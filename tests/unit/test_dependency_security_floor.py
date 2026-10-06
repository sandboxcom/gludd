"""Security regression floors for dependencies used by the beta4 release."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def _project_path(profile: str) -> Path:
    if profile == "runtime":
        return ROOT
    catalog = tomllib.loads(
        (ROOT / "config/dependency_profiles.toml").read_text(encoding="utf-8")
    )
    return ROOT / catalog["profiles"][profile]["project"]


def _declared_requirements(profile: str, package: str) -> list[Requirement]:
    project = tomllib.loads(
        (_project_path(profile) / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = project["project"]["dependencies"]
    return [
        requirement
        for raw in dependencies
        if (requirement := Requirement(raw)).name == package
    ]


def _locked_versions(profile: str, package: str) -> set[Version]:
    lock = tomllib.loads(
        (_project_path(profile) / "uv.lock").read_text(encoding="utf-8")
    )
    return {
        Version(item["version"])
        for item in lock["package"]
        if item["name"] == package
    }


def test_ansible_core_excludes_argument_injection_releases() -> None:
    requirements = _declared_requirements("ansible-controller", "ansible-core")
    assert any(
        requirement.specifier.contains("2.19.11")
        and not requirement.specifier.contains("2.19.10")
        for requirement in requirements
    )
    assert any(
        requirement.specifier.contains("2.21.2")
        and not requirement.specifier.contains("2.21.0")
        for requirement in requirements
    )

    versions = _locked_versions("ansible-controller", "ansible-core")
    assert versions
    assert all(
        version >= (Version("2.19.11") if version < Version("2.20") else Version("2.21.1"))
        for version in versions
    )


def test_setuptools_excludes_manifest_normalization_release() -> None:
    (requirement,) = _declared_requirements("dev-build", "setuptools")
    assert requirement.specifier.contains("83.0.0")
    assert not requirement.specifier.contains("80.10.2")
    versions = _locked_versions("dev-build", "setuptools")
    assert versions
    assert all(version >= Version("83.0.0") for version in versions)
