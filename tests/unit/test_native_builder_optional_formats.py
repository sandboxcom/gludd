"""The maintained native optional-validator path preserves every IRI/URI check."""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from importlib import import_module, metadata, util
from pathlib import Path
from typing import Protocol, cast

import pytest


class _NativeFormatChecker(Protocol):
    @property
    def checkers(self) -> Mapping[str, object]: ...


class _NativeFormatCheckerFactory(Protocol):
    def __call__(self) -> _NativeFormatChecker: ...


class _NativeValidator(Protocol):
    def validate(self, instance: str) -> None: ...


class _NativeValidatorFactory(Protocol):
    def __call__(
        self, schema: dict[str, str], *, format_checker: _NativeFormatChecker,
    ) -> _NativeValidator: ...


def test_development_builder_selects_reviewed_native_optional_validator() -> None:
    root = Path(os.environ.get("GLUDD_FORMAT_PROFILE_ROOT", Path(__file__).resolve().parents[2]))
    profile = tomllib.loads((root / "requirements/profiles/dev-ansible/pyproject.toml").read_text())
    project = cast(dict[str, object], profile["project"])
    dependencies = cast(list[str], project["dependencies"])
    assert "rfc3987==1.3.8" in dependencies
    lock = tomllib.loads((root / "requirements/profiles/dev-ansible/uv.lock").read_text())
    packages = cast(list[dict[str, object]], lock["package"])
    selected = [package for package in packages if package["name"] == "rfc3987"]
    assert len(selected) == 1 and selected[0]["version"] == "1.3.8"
    wheels = cast(list[dict[str, str]], selected[0]["wheels"])
    assert any(wheel["hash"] == "sha256:10702b1e51e5658843460b189b185c0366d2cf4cff716f13111b0ea9fd2dce53"
               for wheel in wheels)


def test_native_jsonschema_selects_real_rfc3987_without_syntax_parser_startup() -> None:
    assert util.find_spec("rfc3987") is not None, "sync the locked development native validator"
    selected = import_module("rfc3987")
    assert metadata.version("rfc3987") == "1.3.8"
    native = import_module("jsonschema._format")
    assert native.__dict__.get("rfc3987") is selected
    assert "rfc3987_syntax" not in sys.modules
    assert "rfc3987_syntax.syntax_helpers" not in sys.modules
    assert "lark" not in sys.modules


@pytest.mark.parametrize("format_name,valid,invalid", [
    ("iri", "https://例え.テスト/über?q=é#λ", "https://example.org/bad space"),
    ("iri-reference", "../über?q=é#λ", "../bad%GG"),
    ("uri", "https://example.org/ok%20path?q=1#frag", "https://example.org/über"),
    ("uri-reference", "../ok%20path?q=1#frag", "../bad space"),
])
def test_native_format_checkers_remain_registered_and_reject_invalid_input(
    format_name: str, valid: str, invalid: str,
) -> None:
    native = import_module("jsonschema")
    checker = cast(_NativeFormatCheckerFactory, native.FormatChecker)()
    assert format_name in checker.checkers, "unregistered formats silently accept inputs"
    factory = cast(_NativeValidatorFactory, native.Draft202012Validator)
    validator = factory({"type": "string", "format": format_name}, format_checker=checker)
    validator.validate(valid)
    error = cast(type[Exception], import_module("jsonschema.exceptions").ValidationError)
    with pytest.raises(error):
        validator.validate(invalid)
