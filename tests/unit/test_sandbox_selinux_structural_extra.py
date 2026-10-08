"""Extended structural tests for security/sandboxes/linux_selinux.py — SELinux backend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.security.sandboxes import Capability, PermissionSpec, SandboxHandle, SandboxTarget
from general_ludd.security.sandboxes.linux_selinux import (
    SELinuxBackend,
    _fc_for,
    _is_file_family,
    _is_net_family,
    _te_for,
    render_fc,
    render_te,
)
from general_ludd.security.sandboxes.state import SandboxState


def _make_cap(resource="file:/tmp/gludd/", actions=None):
    return Capability(resource=resource, actions=set(actions or ["read"]))


def _make_spec(agent_type="test-agent", capabilities=None, denied=None):
    return PermissionSpec(agent_type=agent_type, capabilities=capabilities or [], denied=denied or [])


class TestHelpers:
    def test_is_file_family_true(self):
        assert _is_file_family(_make_cap("file:/tmp/gludd")) is True

    def test_is_file_family_false(self):
        assert _is_file_family(_make_cap("net:egress")) is False

    def test_is_net_family_true(self):
        assert _is_net_family(_make_cap("net:egress")) is True

    def test_is_net_family_false(self):
        assert _is_net_family(_make_cap("file:/tmp")) is False


class TestTeFor:
    def test_te_for_module_header(self):
        spec = _make_spec()
        te = _te_for(spec)
        assert "module gludd_test_agent 1.0" in te
        assert "require {" in te

    def test_te_for_type_name(self):
        spec = _make_spec()
        te = _te_for(spec)
        assert "type gludd_test_agent_t" in te

    def test_te_for_sanitizes_dashes(self):
        spec = _make_spec(agent_type="my-custom-agent")
        te = _te_for(spec)
        assert "module gludd_my_custom_agent 1.0" in te
        assert "type gludd_my_custom_agent_t" in te

    def test_te_for_with_file_capability(self):
        spec = _make_spec(capabilities=[_make_cap("file:/tmp/test/", {"read", "write"})])
        te = _te_for(spec)
        assert "allow gludd_test_agent_t" in te
        assert "/tmp/test/" not in te

    def test_te_for_with_net_capability(self):
        cap = _make_cap("net:egress:api.example.com:443", {"connect"})
        spec = _make_spec(capabilities=[cap])
        te = _te_for(spec)
        assert "tcp_socket name_connect" in te

    def test_te_for_with_denied(self):
        spec = _make_spec(denied=[_make_cap("file:/tmp/forbidden/", {"read"})])
        te = _te_for(spec)
        assert "dontaudit" in te

    def test_te_for_empty_spec_has_body(self):
        spec = _make_spec()
        te = _te_for(spec)
        assert len(te) > 0

    def test_te_for_multiple_file_caps(self):
        spec = _make_spec(capabilities=[
            _make_cap("file:/tmp/a/", {"read"}),
            _make_cap("file:/tmp/b/", {"read"}),
        ])
        te = _te_for(spec)
        assert te.count("allow gludd_test_agent_t usr_t:dir") == 1
        assert te.count("allow gludd_test_agent_t usr_t:file") == 1

    def test_te_for_semantic_rule_order_is_deterministic(self):
        file_cap = _make_cap("file:/tmp/a/", {"read"})
        net_cap = _make_cap("net:egress:api.example.com:443", {"connect"})

        file_first = _te_for(_make_spec(capabilities=[file_cap, net_cap]))
        net_first = _te_for(_make_spec(capabilities=[net_cap, file_cap]))

        assert file_first == net_first


class TestFcFor:
    def test_fc_for_includes_type_name(self):
        spec = _make_spec()
        fc = _fc_for(spec)
        assert "gludd_test_agent_t" in fc

    def test_fc_for_with_file_capability(self):
        spec = _make_spec(capabilities=[_make_cap("file:/tmp/test/", {"read"})])
        fc = _fc_for(spec)
        assert "gludd_test_agent_t" in fc

    def test_fc_for_deduplicates_equivalent_file_caps(self):
        spec = _make_spec(capabilities=[
            _make_cap("file:/tmp/test/", {"read"}),
            _make_cap("file:/tmp/test/", {"write"}),
        ])

        fc = _fc_for(spec)

        assert fc.count("/tmp/test/(/.*)?") == 1

    def test_fc_for_with_net_capability_skips(self):
        cap = _make_cap("net:egress", {"connect"})
        spec = _make_spec(capabilities=[cap])
        fc = _fc_for(spec)
        assert "gludd_test_agent_t" in fc

    def test_fc_for_empty_spec(self):
        spec = _make_spec()
        fc = _fc_for(spec)
        assert isinstance(fc, str)
        assert len(fc) > 0


class TestRenderFunctions:
    def test_render_te_delegates(self):
        spec = _make_spec()
        assert render_te(spec) == _te_for(spec)

    def test_render_fc_delegates(self):
        spec = _make_spec()
        assert render_fc(spec) == _fc_for(spec)


class TestBackend:
    def test_backend_name(self):
        assert SELinuxBackend.name == "selinux"

    def test_available_returns_bool(self):
        assert isinstance(SELinuxBackend.available(), bool)

    def test_available_fails_closed_when_any_tool_is_missing(self):
        with patch(
            "shutil.which",
            side_effect=["/bin/checkmodule", None],
        ):
            assert SELinuxBackend.available() is False
        with patch(
            "shutil.which",
            side_effect=["/bin/checkmodule", "/bin/semodule_package", None],
        ):
            assert SELinuxBackend.available() is False

    def test_available_uses_guarded_selinux_binding(self):
        binding = MagicMock()
        binding.is_selinux_enabled.return_value = 1
        with (
            patch("shutil.which", return_value="/bin/tool"),
            patch("importlib.import_module", return_value=binding),
        ):
            assert SELinuxBackend.available() is True
        with (
            patch("shutil.which", return_value="/bin/tool"),
            patch("importlib.import_module", side_effect=ImportError("selinux")),
        ):
            assert SELinuxBackend.available() is False

    def test_apply_returns_handle(self):
        spec = _make_spec()
        target = SandboxTarget()
        handle = SELinuxBackend.apply(spec, target)
        assert handle.backend == "selinux"
        assert isinstance(handle.applied, bool)

    def test_verify_returns_findings(self):
        spec = _make_spec()
        handle = SandboxHandle(backend="selinux", token="test", applied=True)
        findings = SELinuxBackend.verify(spec, handle)
        assert isinstance(findings, list)

    def test_verify_fails_closed_on_command_error_or_missing_module(self):
        spec = _make_spec()
        handle = SandboxHandle(backend="selinux", token="test", applied=True)

        with patch("subprocess.run", side_effect=OSError("semodule failed")):
            command_error = SELinuxBackend.verify(spec, handle)
        with patch(
            "subprocess.run",
            return_value=MagicMock(stdout=b"unrelated_module\n"),
        ):
            missing_module = SELinuxBackend.verify(spec, handle)

        assert [finding.severity for finding in command_error] == ["fail"]
        assert [finding.severity for finding in missing_module] == ["fail"]

    def test_verify_reports_missing_file_context(self):
        spec = _make_spec()
        handle = SandboxHandle(backend="selinux", token="test", applied=True)
        with patch(
            "subprocess.run",
            side_effect=[
                MagicMock(stdout=b"test\n"),
                MagicMock(stdout=b"unrelated_context\n"),
            ],
        ):
            findings = SELinuxBackend.verify(spec, handle)

        assert [finding.severity for finding in findings] == ["ok", "warn"]

    def test_release_noop_not_applied(self):
        handle = SandboxHandle(backend="selinux", token="test", applied=False)
        SELinuxBackend.release(handle)

    def test_release_contains_command_and_state_cleanup_errors(self):
        applied = SandboxHandle(backend="selinux", token="test", applied=True)
        with patch("subprocess.run", side_effect=OSError("remove failed")):
            SELinuxBackend.release(applied)

        state = object.__new__(SandboxState)
        with_state = SandboxHandle(
            backend="selinux",
            token="test",
            applied=False,
            extra={"state": state, "state_path": "/confined/test"},
        )
        with patch.object(
            SandboxState,
            "cleanup_path",
            side_effect=OSError("cleanup failed"),
        ):
            SELinuxBackend.release(with_state)
