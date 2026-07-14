from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from general_ludd.security.permissions import Capability, PermissionSpec
from general_ludd.security.sandboxes import Finding, SandboxHandle, SandboxTarget
from general_ludd.security.sandboxes import windows_appcontainer as _wac_mod
from general_ludd.security.sandboxes.windows_appcontainer import (
    AppContainerBackend,
    _firewall_rule_name,
    _icacls_deny_all_except,
    _is_file_family,
    _is_net_family,
    _net_allow_rules,
    render_icacls,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cap(resource: str, **constraints: object) -> Capability:
    return Capability(resource=resource, constraints=dict(constraints))


def _spec(agent_type: str = "worker", capabilities: list[Capability] | None = None) -> PermissionSpec:
    return PermissionSpec(agent_type=agent_type, capabilities=capabilities or [])


def _target(directory: str | None = None) -> SandboxTarget:
    return SandboxTarget(directory=directory)


# ---------------------------------------------------------------------------
# _is_file_family
# ---------------------------------------------------------------------------


class TestIsFileFamily:
    def test_file_prefix_returns_true(self) -> None:
        assert _is_file_family(_cap("file:cwd")) is True

    def test_net_prefix_returns_false(self) -> None:
        assert _is_file_family(_cap("net:host")) is False

    def test_other_prefix_returns_false(self) -> None:
        assert _is_file_family(_cap("secret:openbao")) is False

    def test_empty_resource_returns_false(self) -> None:
        assert _is_file_family(_cap("")) is False


# ---------------------------------------------------------------------------
# _is_net_family
# ---------------------------------------------------------------------------


class TestIsNetFamily:
    def test_net_prefix_returns_true(self) -> None:
        assert _is_net_family(_cap("net:host")) is True

    def test_file_prefix_returns_false(self) -> None:
        assert _is_net_family(_cap("file:cwd")) is False

    def test_other_prefix_returns_false(self) -> None:
        assert _is_net_family(_cap("secret:openbao")) is False

    def test_empty_resource_returns_false(self) -> None:
        assert _is_net_family(_cap("")) is False


# ---------------------------------------------------------------------------
# _icacls_deny_all_except
# ---------------------------------------------------------------------------


class TestIcaclsDenyAllExcept:
    def test_returns_list_of_strings(self) -> None:
        result = _icacls_deny_all_except("C:\\sandbox", "S-1-15-2-123")
        assert isinstance(result, list)
        assert all(isinstance(x, str) for x in result)

    def test_starts_with_icacls_binary(self) -> None:
        result = _icacls_deny_all_except("C:\\sandbox", "S-1-15-2-123")
        assert result[0] == "icacls"

    def test_includes_directory_as_second_arg(self) -> None:
        result = _icacls_deny_all_except("C:\\sandbox", "S-1-15-2-123")
        assert result[1] == "C:\\sandbox"

    def test_includes_inheritance_reset(self) -> None:
        result = _icacls_deny_all_except("C:\\sandbox", "S-1-15-2-123")
        assert "/inheritance:r" in result

    def test_includes_grant_with_sid(self) -> None:
        result = _icacls_deny_all_except("C:\\sandbox", "S-1-15-2-123")
        assert "/grant:r" in result
        assert any("S-1-15-2-123" in token for token in result)

    def test_includes_deny_everyone(self) -> None:
        result = _icacls_deny_all_except("C:\\sandbox", "S-1-15-2-123")
        assert "/deny" in result
        assert any("Everyone" in token for token in result)


# ---------------------------------------------------------------------------
# _firewall_rule_name
# ---------------------------------------------------------------------------


class TestFirewallRuleName:
    def test_includes_agent_type(self) -> None:
        spec = _spec(agent_type="worker")
        name = _firewall_rule_name(spec)
        assert "worker" in name

    def test_starts_with_gludd_prefix(self) -> None:
        spec = _spec(agent_type="worker")
        name = _firewall_rule_name(spec)
        assert name.startswith("gludd-")

    def test_ends_with_egress_suffix(self) -> None:
        spec = _spec(agent_type="worker")
        name = _firewall_rule_name(spec)
        assert name.endswith("-egress")

    def test_returns_expected_format(self) -> None:
        spec = _spec(agent_type="build")
        assert _firewall_rule_name(spec) == "gludd-build-egress"


# ---------------------------------------------------------------------------
# _net_allow_rules
# ---------------------------------------------------------------------------


class TestNetAllowRules:
    def test_no_net_caps_returns_only_deny_rule(self) -> None:
        spec = _spec(agent_type="worker", capabilities=[_cap("file:cwd")])
        rules = _net_allow_rules(spec)
        assert len(rules) == 1
        assert "name=gludd-worker-egress-deny" in rules[0]

    def test_net_cap_with_host_and_port_produces_rules(self) -> None:
        spec = _spec(agent_type="worker", capabilities=[
            _cap("net:host", allowed_hosts=["10.0.0.1"], allowed_ports=[443]),
        ])
        rules = _net_allow_rules(spec)
        assert len(rules) >= 2
        assert any("name=gludd-worker-egress-10.0.0.1-443" in str(rule) for rule in rules)

    def test_every_output_rule_is_list_of_str(self) -> None:
        spec = _spec(agent_type="worker", capabilities=[
            _cap("net:host", allowed_hosts=["10.0.0.1"], allowed_ports=[443]),
        ])
        for rule in _net_allow_rules(spec):
            assert isinstance(rule, list)
            assert all(isinstance(x, str) for x in rule)

    def test_deny_rule_always_last(self) -> None:
        spec = _spec(agent_type="worker", capabilities=[
            _cap("net:host", allowed_hosts=["10.0.0.1"], allowed_ports=[443]),
        ])
        rules = _net_allow_rules(spec)
        assert "deny" in rules[-1][0] or "deny" in " ".join(rules[-1])

    def test_empty_capabilities_list(self) -> None:
        spec = _spec(agent_type="worker", capabilities=[])
        rules = _net_allow_rules(spec)
        assert len(rules) == 1
        assert "deny" in " ".join(rules[0])

    def test_multiple_hosts_multiple_ports(self) -> None:
        spec = _spec(agent_type="worker", capabilities=[
            _cap("net:host", allowed_hosts=["10.0.0.1", "10.0.0.2"], allowed_ports=[80, 443]),
        ])
        rules = _net_allow_rules(spec)
        allow_rule_count = len(rules) - 1
        assert allow_rule_count == 4

    def test_skips_non_net_caps(self) -> None:
        spec = _spec(agent_type="worker", capabilities=[
            _cap("file:cwd"),
            _cap("net:host", allowed_hosts=["10.0.0.1"], allowed_ports=[443]),
            _cap("secret:openbao"),
        ])
        rules = _net_allow_rules(spec)
        allow_rule_count = len(rules) - 1
        assert allow_rule_count == 1


# ---------------------------------------------------------------------------
# render_icacls
# ---------------------------------------------------------------------------


class TestRenderIcacls:
    def test_delegates_to_icacls_deny_all_except(self) -> None:
        spec = _spec(agent_type="worker")
        result = render_icacls(spec, "C:\\sandbox", "S-1-15-2-999")
        assert result == _icacls_deny_all_except("C:\\sandbox", "S-1-15-2-999")

    def test_returns_list_of_strings(self) -> None:
        result = render_icacls(_spec(), "C:\\dir", "S-1-15-2-1")
        assert isinstance(result, list)
        assert all(isinstance(x, str) for x in result)


# ---------------------------------------------------------------------------
# AppContainerBackend
# ---------------------------------------------------------------------------


class TestAppContainerBackendAttributes:
    def test_has_name_class_attribute(self) -> None:
        assert hasattr(AppContainerBackend, "name")
        assert AppContainerBackend.name == "appcontainer"


# ---------------------------------------------------------------------------
# AppContainerBackend.available
# ---------------------------------------------------------------------------


class TestAppContainerBackendAvailable:
    def test_non_windows_returns_false(self) -> None:
        with patch.object(sys, "platform", "darwin"):
            result = AppContainerBackend.available()
            assert result is False

    def test_linux_returns_false(self) -> None:
        with patch.object(sys, "platform", "linux"):
            result = AppContainerBackend.available()
            assert result is False

    def test_windows_with_win32security_returns_true(self) -> None:
        mock_security = MagicMock()
        with patch.object(sys, "platform", "win32"), \
             patch("importlib.import_module", return_value=mock_security):
            result = AppContainerBackend.available()
            assert result is True

    def test_windows_without_win32security_returns_false(self) -> None:
        with patch.object(sys, "platform", "win32"), \
             patch("importlib.import_module", side_effect=ImportError):
            result = AppContainerBackend.available()
            assert result is False

    def test_windows_with_any_exception_returns_false(self) -> None:
        with patch.object(sys, "platform", "win32"), \
             patch("importlib.import_module", side_effect=RuntimeError("dll load")):
            result = AppContainerBackend.available()
            assert result is False


# ---------------------------------------------------------------------------
# AppContainerBackend.apply
# ---------------------------------------------------------------------------


class TestAppContainerBackendApply:
    def test_non_windows_returns_fail_open_handle(self) -> None:
        spec = _spec(agent_type="worker")
        target = _target(directory="C:\\sandbox")
        with patch.object(sys, "platform", "darwin"):
            handle = AppContainerBackend.apply(spec, target)
            assert isinstance(handle, SandboxHandle)
            assert handle.backend == "appcontainer"
            assert handle.applied is False

    def test_non_windows_handle_contains_error(self) -> None:
        spec = _spec(agent_type="worker")
        target = _target(directory="C:\\sandbox")
        with patch.object(sys, "platform", "darwin"):
            handle = AppContainerBackend.apply(spec, target)
            assert "error" in handle.extra

    def test_success_returns_applied_true_handle(self) -> None:
        spec = _spec(agent_type="worker")
        target = _target(directory="C:\\sandbox")
        mock_api = MagicMock()
        mock_api.CreateAppContainerProfile.return_value = ("S-1-15-2-123", ["cap"])
        with patch.object(sys, "platform", "win32"), \
             patch("importlib.import_module", return_value=mock_api), \
             patch.object(_wac_mod.subprocess, "run", return_value=MagicMock(returncode=0)):
            handle = AppContainerBackend.apply(spec, target)
            assert handle.applied is True
            assert handle.backend == "appcontainer"
            assert handle.token == "S-1-15-2-123"

    def test_success_includes_agent_type_in_extra(self) -> None:
        spec = _spec(agent_type="worker")
        target = _target(directory="C:\\sandbox")
        mock_api = MagicMock()
        mock_api.CreateAppContainerProfile.return_value = ("S-1-15-2-123", ["cap"])
        with patch.object(sys, "platform", "win32"), \
             patch("importlib.import_module", return_value=mock_api), \
             patch.object(_wac_mod.subprocess, "run", return_value=MagicMock(returncode=0)):
            handle = AppContainerBackend.apply(spec, target)
            assert handle.applied is True
            assert handle.backend == "appcontainer"
            assert handle.token == "S-1-15-2-123"
            assert handle.extra.get("agent_type") == "worker"

    def test_failure_fail_open_returns_applied_false(self) -> None:
        spec = _spec(agent_type="worker")
        target = _target(directory="C:\\sandbox")
        with patch.object(sys, "platform", "win32"), \
             patch("importlib.import_module", side_effect=RuntimeError("boom")):
            handle = AppContainerBackend.apply(spec, target)
            assert handle.applied is False
            assert handle.backend == "appcontainer"

    def test_failure_handle_has_error_in_extra(self) -> None:
        spec = _spec(agent_type="worker")
        target = _target(directory="C:\\sandbox")
        with patch.object(sys, "platform", "win32"), \
             patch("importlib.import_module", side_effect=RuntimeError("boom")):
            handle = AppContainerBackend.apply(spec, target)
            assert "error" in handle.extra
            assert "boom" in str(handle.extra["error"])

    def test_no_directory_skips_icacls(self) -> None:
        spec = _spec(agent_type="worker")
        target = _target(directory=None)
        mock_api = MagicMock()
        mock_api.CreateAppContainerProfile.return_value = ("S-1-15-2-123", ["cap"])
        with patch.object(sys, "platform", "win32"), \
             patch("importlib.import_module", return_value=mock_api), \
             patch.object(_wac_mod.subprocess, "run") as mock_run:
            AppContainerBackend.apply(spec, target)
            icacls_calls = [c for c in mock_run.call_args_list if "icacls" in str(c)]
            assert len(icacls_calls) == 0


# ---------------------------------------------------------------------------
# AppContainerBackend.verify
# ---------------------------------------------------------------------------


class TestAppContainerBackendVerify:
    def test_returns_list_of_findings(self) -> None:
        spec = _spec(agent_type="worker")
        handle = SandboxHandle(backend="appcontainer", token="S-1-15-2-123", applied=True)
        findings = AppContainerBackend.verify(spec, handle)
        assert isinstance(findings, list)

    def test_returns_finding_objects(self) -> None:
        spec = _spec(agent_type="worker")
        handle = SandboxHandle(backend="appcontainer", token="S-1-15-2-123", applied=True)
        findings = AppContainerBackend.verify(spec, handle)
        for f in findings:
            assert isinstance(f, Finding)
            assert isinstance(f.severity, str)
            assert isinstance(f.message, str)

    def test_firewall_rule_present_produces_ok_finding(self) -> None:
        spec = _spec(agent_type="worker")
        handle = SandboxHandle(backend="appcontainer", token="S-1-15-2-123", applied=True)
        rule_name = _firewall_rule_name(spec)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=rule_name.encode())
            findings = AppContainerBackend.verify(spec, handle)
            ok_findings = [f for f in findings if f.severity == "ok"]
            assert len(ok_findings) >= 1

    def test_firewall_rule_absent_produces_warn_finding(self) -> None:
        spec = _spec(agent_type="worker")
        handle = SandboxHandle(backend="appcontainer", token="S-1-15-2-123", applied=True)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"")
            findings = AppContainerBackend.verify(spec, handle)
            warn_findings = [f for f in findings if f.severity == "warn"]
            assert len(warn_findings) >= 1

    def test_not_applied_handle_produces_advisory_warning(self) -> None:
        spec = _spec(agent_type="worker")
        handle = SandboxHandle(backend="appcontainer", token="gludd-worker", applied=False)
        findings = AppContainerBackend.verify(spec, handle)
        assert any("advisory" in f.message.lower() for f in findings)

    def test_subprocess_exception_second_chance(self) -> None:
        spec = _spec(agent_type="worker")
        handle = SandboxHandle(backend="appcontainer", token="S-1-15-2-123", applied=True)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = AppContainerBackend.verify(spec, handle)
            assert isinstance(findings, list)

    def test_respects_agent_type_in_firewall_check(self) -> None:
        spec = _spec(agent_type="build")
        handle = SandboxHandle(backend="appcontainer", token="S-1-15-2-123", applied=True)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=b"")
            AppContainerBackend.verify(spec, handle)
            call_args = str(mock_run.call_args)
            assert "gludd-build-egress" in call_args


# ---------------------------------------------------------------------------
# AppContainerBackend.release
# ---------------------------------------------------------------------------


class TestAppContainerBackendRelease:
    def test_not_applied_is_noop(self) -> None:
        handle = SandboxHandle(backend="appcontainer", token="gludd-worker", applied=False)
        AppContainerBackend.release(handle)

    def test_not_applied_does_not_import_win32(self) -> None:
        handle = SandboxHandle(backend="appcontainer", token="gludd-worker", applied=False)
        with patch("importlib.import_module") as mock_import:
            AppContainerBackend.release(handle)
            mock_import.assert_not_called()

    def test_applied_calls_delete_appcontainer_profile(self) -> None:
        handle = SandboxHandle(
            backend="appcontainer", token="S-1-15-2-123", applied=True,
            extra={"agent_type": "worker"},
        )
        mock_api = MagicMock()
        with patch("importlib.import_module", return_value=mock_api):
            AppContainerBackend.release(handle)
            mock_api.DeleteAppContainerProfile.assert_called_once()

    def test_release_swallows_exceptions(self) -> None:
        handle = SandboxHandle(
            backend="appcontainer", token="S-1-15-2-123", applied=True,
            extra={"agent_type": "worker"},
        )
        with patch("importlib.import_module", side_effect=RuntimeError("no pywin32")):
            AppContainerBackend.release(handle)


# ---------------------------------------------------------------------------
# Integration: full lifecycle structural test
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_apply_verify_release_structural(self) -> None:
        spec = _spec(agent_type="worker", capabilities=[
            _cap("net:host", allowed_hosts=["10.0.0.1"], allowed_ports=[443]),
        ])
        target = _target(directory="C:\\sandbox")
        mock_api = MagicMock()
        mock_api.CreateAppContainerProfile.return_value = ("S-1-15-2-123", ["cap"])
        with patch.object(sys, "platform", "win32"), \
             patch("importlib.import_module", return_value=mock_api), \
             patch.object(_wac_mod.subprocess, "run", return_value=MagicMock(returncode=0)):
            handle = AppContainerBackend.apply(spec, target)
            assert handle.applied is True
        with patch("subprocess.run", return_value=MagicMock(stdout=b"")):
            findings = AppContainerBackend.verify(spec, handle)
            assert isinstance(findings, list)
        handle_release = SandboxHandle(
            backend="appcontainer", token="S-1-15-2-123", applied=True,
            extra={"agent_type": "worker"},
        )
        AppContainerBackend.release(handle_release)
