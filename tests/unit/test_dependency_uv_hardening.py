"""Argument-injection hardening tests for the dependency manager (issue #69).

The dependency manager launches ``uv``/``pip`` via
``asyncio.create_subprocess_exec`` with package specs derived from caller
input. A malicious or buggy caller could smuggle option-injection payloads
(e.g. ``--index-url=http://evil``, ``-e .``, leading dash) or shell
metacharacters into the spec. These tests prove that such specs are rejected
*before* any subprocess is launched, while legitimate specs are accepted and
passed positionally after a ``--`` separator.

No real ``uv``/``pip`` is invoked: ``_run`` is mocked.
"""

from __future__ import annotations

import pytest

from general_ludd.dependency.manager import (
    DependencyManager,
    InvalidPackageSpecError,
    _validate_package_spec,
)


class TestValidatePackageSpec:
    """Unit tests for the spec validator."""

    @pytest.mark.parametrize(
        "spec",
        [
            "requests==2.31.0",
            "requests",
            "requests>=2.0,<3.0",
            "Django~=4.2",
            "uvicorn[standard]==0.23.0",
            "ruff==0.1.0",
            "typing-extensions",
            "ruamel.yaml==0.18.0",
        ],
    )
    def test_accepts_legitimate_specs(self, spec):
        # Should not raise and should return the spec unchanged.
        assert _validate_package_spec(spec) == spec

    @pytest.mark.parametrize(
        "spec",
        [
            "--index-url=http://evil",  # option injection
            "--index-url",
            "-e .",  # editable install / leading dash
            "-rrequirements.txt",
            "-i",
            "--upgrade",
            "foo; rm -rf /",  # shell metachars
            "foo && rm -rf /",
            "foo | cat",
            "foo`whoami`",
            "foo$(whoami)",
            "foo\nbar",  # whitespace / newline
            "foo bar",
            "./local/path",  # path-like
            "/etc/passwd",
            "../escape",
            "",  # empty
            "   ",
            "foo==2.0; --index-url=x",
        ],
    )
    def test_rejects_injection_specs(self, spec):
        with pytest.raises(InvalidPackageSpecError):
            _validate_package_spec(spec)


class TestUpdatePackageHardening:
    """Integration: update_package must validate before launching subprocess."""

    @pytest.mark.asyncio
    async def test_rejects_option_injection_before_subprocess(self, monkeypatch):
        mgr = DependencyManager()

        called = False

        async def fake_run(*args):  # pragma: no cover - must not be reached
            nonlocal called
            called = True
            return (0, "", "")

        monkeypatch.setattr(mgr, "_run", fake_run)
        monkeypatch.setattr(
            "general_ludd.dependency.manager._has_uv", lambda: True
        )

        with pytest.raises(InvalidPackageSpecError):
            await mgr.update_package("--index-url=http://evil")

        assert called is False, "subprocess must NOT launch for an unsafe spec"

    @pytest.mark.asyncio
    async def test_rejects_injection_via_version_constraint(self, monkeypatch):
        mgr = DependencyManager()

        called = False

        async def fake_run(*args):  # pragma: no cover - must not be reached
            nonlocal called
            called = True
            return (0, "", "")

        monkeypatch.setattr(mgr, "_run", fake_run)
        monkeypatch.setattr(
            "general_ludd.dependency.manager._has_uv", lambda: True
        )

        with pytest.raises(InvalidPackageSpecError):
            await mgr.update_package("requests", "; rm -rf /")

        assert called is False

    @pytest.mark.asyncio
    async def test_accepts_legit_spec_and_uses_dashdash_separator(
        self, monkeypatch
    ):
        mgr = DependencyManager()
        captured: list[tuple[str, ...]] = []

        async def fake_run(*args):
            captured.append(args)
            return (0, "Resolved 1 package", "")

        monkeypatch.setattr(mgr, "_run", fake_run)
        monkeypatch.setattr(
            "general_ludd.dependency.manager._has_uv", lambda: True
        )

        result = await mgr.update_package("requests", "==2.31.0")

        assert result.tool_used == "uv"
        assert len(captured) == 1
        argv = captured[0]
        # uv add -- requests==2.31.0  (positional separated by --)
        assert argv[0] == "uv"
        assert "--" in argv, "`--` separator must precede the package spec"
        dashdash = argv.index("--")
        assert "requests==2.31.0" in argv[dashdash + 1 :]
        # nothing after -- may look like an option
        for tok in argv[dashdash + 1 :]:
            assert not tok.startswith("-")
