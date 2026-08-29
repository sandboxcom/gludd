"""Structural tests for security/sandboxes/macos_seatbelt.py — macOS Seatbelt backend."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from general_ludd.security.sandboxes import Capability, PermissionSpec
from general_ludd.security.sandboxes.macos_seatbelt import (
    SeatbeltBackend,
    _deny_clause,
    _file_clause,
    _is_deprecated_host,
    _is_file_family,
    _is_net_family,
    _macos_version_tuple,
    _net_clause,
    render_profile,
)


def _make_cap(
    resource: str = "file:/tmp/gludd/",
    actions: set[str] | None = None,
) -> Capability:
    selected_actions = {"read"} if actions is None else actions
    return Capability(resource=resource, actions=list(selected_actions))


def _make_spec(
    agent_type: str = "test-agent",
    capabilities: list[Capability] | None = None,
    denied: list[Capability] | None = None,
) -> PermissionSpec:
    return PermissionSpec(
        agent_type=agent_type,
        capabilities=capabilities or [],
        denied=denied or [],
    )


class TestHelpers:
    def test_is_file_family_true(self) -> None:
        assert _is_file_family(_make_cap("file:/tmp/gludd")) is True

    def test_is_file_family_false(self) -> None:
        assert _is_file_family(_make_cap("net:egress")) is False

    def test_is_net_family_true(self) -> None:
        assert _is_net_family(_make_cap("net:egress")) is True

    def test_is_net_family_false(self) -> None:
        assert _is_net_family(_make_cap("file:/tmp")) is False

    def test_macos_version_tuple_returns_tuple(self) -> None:
        result = _macos_version_tuple()
        assert isinstance(result, tuple)

    def test_is_deprecated_host_returns_bool(self) -> None:
        result = _is_deprecated_host()
        assert isinstance(result, bool)

    @pytest.mark.parametrize("raw", ["", "raise"])
    def test_macos_version_unavailable_returns_empty_tuple(
        self, raw: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if raw == "raise":
            monkeypatch.setattr(
                "general_ludd.security.sandboxes.macos_seatbelt.platform.mac_ver",
                lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
            )
        else:
            monkeypatch.setattr(
                "general_ludd.security.sandboxes.macos_seatbelt.platform.mac_ver",
                lambda: ("", ("", "", ""), ""),
            )

        assert _macos_version_tuple() == ()

    def test_unknown_macos_version_is_not_deprecated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.security.sandboxes.macos_seatbelt._macos_version_tuple",
            lambda: (),
        )

        assert _is_deprecated_host() is False


class TestFileClause:
    def test_file_clause_read(self) -> None:
        cap = _make_cap("file:/tmp/gludd/", {"read"})
        clause = _file_clause(cap)
        assert 'file-read*' in clause
        assert '/tmp/gludd/' in clause

    def test_file_clause_write(self) -> None:
        cap = _make_cap("file:/tmp/gludd/", {"write"})
        clause = _file_clause(cap)
        assert 'file-write*' in clause

    def test_file_clause_read_write(self) -> None:
        cap = _make_cap("file:/tmp/gludd/", {"read", "write"})
        clause = _file_clause(cap)
        assert 'file-read*' in clause
        assert 'file-write*' in clause

    def test_file_clause_no_actions_defaults_read(self) -> None:
        cap = _make_cap("file:/tmp/gludd/", set())
        clause = _file_clause(cap)
        assert 'file-read*' in clause

    def test_file_clause_without_prefix_uses_owned_state_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.security.sandboxes.macos_seatbelt.project_state",
            lambda: SimpleNamespace(directory=lambda *_parts: tmp_path),
        )

        clause = _file_clause(Capability(resource="file:", actions=[]))

        assert str(tmp_path) in clause
        assert "file-read*" in clause


class TestNetClause:
    def test_net_clause_with_hosts_and_ports(self) -> None:
        cap = Capability(
            resource="net:egress:api.example.com:443",
            actions=["connect"],
            constraints={
                "allowed_hosts": ["api.example.com"],
                "allowed_ports": [443],
            },
        )
        clause = _net_clause(cap)
        assert "network-outbound" in clause
        assert "api.example.com" in clause

    def test_net_clause_without_hosts(self) -> None:
        cap = _make_cap("net:egress", {"connect"})
        clause = _net_clause(cap)
        assert 'network-outbound' in clause


class TestDenyClause:
    def test_deny_clause_file(self) -> None:
        cap = _make_cap("file:/tmp/secret/", {"read"})
        clause = _deny_clause(cap)
        assert 'deny file-read*' in clause

    def test_deny_clause_net(self) -> None:
        cap = _make_cap("net:egress", {"connect"})
        clause = _deny_clause(cap)
        assert 'deny network-outbound' in clause

    def test_deny_clause_other(self) -> None:
        cap = _make_cap("other:resource", {"use"})
        clause = _deny_clause(cap)
        assert 'deny other:resource' in clause


class TestRenderProfile:
    def test_render_profile_header(self) -> None:
        spec = _make_spec()
        profile = render_profile(spec)
        assert "(version 1)" in profile
        assert "(deny default)" in profile
        assert "(allow process-fork)" in profile

    def test_render_profile_with_file_capability(self) -> None:
        spec = _make_spec(capabilities=[_make_cap("file:/tmp/gludd/", {"read"})])
        profile = render_profile(spec)
        assert '(allow file-read*' in profile
        assert '/tmp/gludd/' in profile

    def test_render_profile_with_denied_capability(self) -> None:
        spec = _make_spec(denied=[_make_cap("file:/tmp/forbidden/", {"read"})])
        profile = render_profile(spec)
        assert 'deny file-read*' in profile

    def test_render_profile_empty_spec(self) -> None:
        spec = _make_spec()
        profile = render_profile(spec)
        assert 'empty spec' in profile or profile.endswith('\n')

    def test_render_profile_with_network_capability(self) -> None:
        profile = render_profile(
            _make_spec(
                capabilities=[
                    Capability(resource="net:egress", actions=["connect"])
                ]
            )
        )

        assert "allow network-outbound" in profile


class TestBackend:
    def test_backend_name(self) -> None:
        assert SeatbeltBackend.name == "seatbelt"

    def test_available_returns_bool(self) -> None:
        result = SeatbeltBackend.available()
        assert isinstance(result, bool)

    def test_available_rejects_missing_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda _binary: None)
        monkeypatch.setattr(
            "general_ludd.security.sandboxes.macos_seatbelt.platform.mac_ver",
            lambda: ("15.4", ("", "", ""), ""),
        )

        assert SeatbeltBackend.available() is False

    def test_available_warns_below_deprecation_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/sandbox-exec")
        monkeypatch.setattr(
            "general_ludd.security.sandboxes.macos_seatbelt._is_deprecated_host",
            lambda: False,
        )
        monkeypatch.setattr(
            "general_ludd.security.sandboxes.macos_seatbelt.platform.mac_ver",
            lambda: ("14.7", ("", "", ""), ""),
        )

        assert SeatbeltBackend.available() is True
