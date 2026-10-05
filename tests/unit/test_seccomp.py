"""Structural tests for security/seccomp.py — seccomp BPF syscall filtering."""

from __future__ import annotations

import errno
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.security.seccomp import (
    DEFAULT_ALLOWED_SYSCALLS,
    DEFAULT_DENIED_SYSCALLS,
    SeccompFilter,
    _iter_bits,
    _native_arch,
)


class TestConstants:
    def test_default_allowed_is_frozenset(self) -> None:
        assert isinstance(DEFAULT_ALLOWED_SYSCALLS, frozenset)

    def test_default_denied_is_frozenset(self) -> None:
        assert isinstance(DEFAULT_DENIED_SYSCALLS, frozenset)

    def test_allowed_contains_essential(self) -> None:
        assert "read" in DEFAULT_ALLOWED_SYSCALLS
        assert "write" in DEFAULT_ALLOWED_SYSCALLS
        assert "open" in DEFAULT_ALLOWED_SYSCALLS
        assert "exit" in DEFAULT_ALLOWED_SYSCALLS

    def test_denied_contains_escape(self) -> None:
        assert "mount" in DEFAULT_DENIED_SYSCALLS
        assert "unshare" in DEFAULT_DENIED_SYSCALLS
        assert "setns" in DEFAULT_DENIED_SYSCALLS
        assert "bpf" in DEFAULT_DENIED_SYSCALLS

    def test_allowed_and_denied_are_disjoint(self) -> None:
        assert DEFAULT_ALLOWED_SYSCALLS.isdisjoint(DEFAULT_DENIED_SYSCALLS)


class TestSeccompFilterDefaults:
    def test_default_constructor(self) -> None:
        flt = SeccompFilter.default()
        assert flt.default_action == "allow"
        assert flt.deny_action == "kill"
        assert flt.errno == errno.EPERM

    def test_default_filter_has_allowed(self) -> None:
        flt = SeccompFilter()
        assert len(flt.allowed_syscalls) > 0

    def test_default_filter_has_denied(self) -> None:
        flt = SeccompFilter()
        assert len(flt.denied_syscalls) > 0


class TestSeccompFilterYAML:
    def test_from_yaml_override_deny_action(self) -> None:
        import os
        import tempfile

        yaml_content = "deny_action: errno\nerrno: 13\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            path = f.name
        try:
            flt = SeccompFilter.from_yaml(path)
            assert flt.deny_action == "errno"
            assert flt.errno == 13
        finally:
            os.unlink(path)

    @pytest.mark.parametrize(
        ("content", "field"),
        [
            ("default_action: alow\n", "default_action"),
            ("deny_action: terminate\n", "deny_action"),
        ],
    )
    def test_from_yaml_rejects_unknown_actions(
        self,
        tmp_path: Path,
        content: str,
        field: str,
    ) -> None:
        policy = tmp_path / "seccomp.yml"
        policy.write_text(content)

        with pytest.raises(ValueError, match=field):
            SeccompFilter.from_yaml(str(policy))


class TestSeccompFilterChecks:
    def test_is_denied(self) -> None:
        flt = SeccompFilter()
        assert flt.is_denied("mount") is True
        assert flt.is_denied("read") is False

    def test_is_allowed_allow_mode(self) -> None:
        flt = SeccompFilter()
        assert flt.is_allowed("read") is True
        assert flt.is_allowed("mount") is False
        assert flt.is_allowed("some_unknown_syscall") is True

    def test_is_allowed_errno_mode(self) -> None:
        flt = SeccompFilter(default_action="errno")
        assert flt.is_allowed("read") is True
        assert flt.is_allowed("mount") is False
        assert flt.is_allowed("some_unknown_syscall") is False


class TestSeccompFilterPlatform:
    def test_is_supported_returns_bool(self) -> None:
        result = SeccompFilter.is_supported()
        assert isinstance(result, bool)


class TestSeccompFilterBuildBPF:
    def test_build_bpf_default(self) -> None:
        flt = SeccompFilter()
        program = flt.build_bpf()
        assert isinstance(program, list)
        assert len(program) > 3
        for insn in program:
            assert isinstance(insn, tuple)
            assert len(insn) == 4

    def test_build_bpf_starts_with_arch_validation(self) -> None:
        flt = SeccompFilter()
        program = flt.build_bpf()
        assert program[0][0] == 0x20
        assert program[1][0] == 0x15
        assert program[2][0] == 0x06

    def test_build_bpf_ends_with_default_ret(self) -> None:
        flt = SeccompFilter()
        program = flt.build_bpf()
        assert program[-1][0] == 0x06

    def test_build_bpf_deny_action_uses_kill(self) -> None:
        flt = SeccompFilter(deny_action="kill")
        program = flt.build_bpf()
        assert len(program) > 3

    def test_build_bpf_deny_action_uses_errno(self) -> None:
        flt = SeccompFilter(deny_action="errno", errno=13)
        program = flt.build_bpf()
        assert len(program) > 3

    def test_build_bpf_strict_mode(self) -> None:
        flt = SeccompFilter(default_action="errno")
        program = flt.build_bpf()
        assert len(program) > 3

    def test_build_bpf_x86_64_explicit(self) -> None:
        flt = SeccompFilter()
        program = flt.build_bpf(arch="x86_64")
        assert len(program) > 3

    def test_build_bpf_aarch64_explicit(self) -> None:
        flt = SeccompFilter()
        program = flt.build_bpf(arch="aarch64")
        assert len(program) > 3


class TestNativeArch:
    def test_returns_string(self) -> None:
        arch = _native_arch()
        assert isinstance(arch, str)
        assert arch in ("x86_64", "aarch64")


class TestIterBits:
    def test_empty_mask(self) -> None:
        assert _iter_bits(0) == []

    def test_single_bit(self) -> None:
        assert _iter_bits(4) == [4]

    def test_multiple_bits(self) -> None:
        result = _iter_bits(10)
        assert 2 in result
        assert 8 in result
        assert len(result) == 2

    def test_clone_ns_mask_has_expected_flags(self) -> None:
        result = _iter_bits(0x02000000)
        assert 0x02000000 in result


class TestSeccompFilterApply:
    def test_apply_returns_false_on_non_linux(self) -> None:
        flt = SeccompFilter()
        result = flt.apply()
        assert isinstance(result, bool)

    def test_apply_uses_manual_fallback_when_binding_is_unavailable(self) -> None:
        flt = SeccompFilter.default()
        with (
            patch.object(SeccompFilter, "is_supported", return_value=True),
            patch.object(SeccompFilter, "_apply_libseccomp", return_value=False),
            patch.object(SeccompFilter, "_apply_manual_bpf", return_value=True),
        ):
            assert flt.apply() is True

    def test_supported_linux_host_with_loadable_libc(self) -> None:
        with patch("sys.platform", "linux"), patch("ctypes.CDLL") as load_libc:
            assert SeccompFilter.is_supported() is True
        load_libc.assert_called_once_with(None, use_errno=True)

    def test_libseccomp_import_failure_is_nonfatal(self) -> None:
        with patch("importlib.import_module", side_effect=ImportError("missing")):
            assert SeccompFilter.default()._apply_libseccomp() is False

    def test_libseccomp_binding_installs_strict_rules(self) -> None:
        installed_filter = MagicMock()
        binding = SimpleNamespace(
            ALLOW="ALLOW",
            KILL="KILL",
            MASKED_EQ="MASKED_EQ",
            ERRNO=lambda value: ("ERRNO", value),
            Arg=lambda *values: ("ARG", values),
            SyscallFilter=MagicMock(return_value=installed_filter),
        )
        flt = SeccompFilter(
            allowed_syscalls=frozenset({"read"}),
            denied_syscalls=frozenset({"mount"}),
            default_action="errno",
            deny_action="errno",
        )

        with patch("importlib.import_module", return_value=binding):
            assert flt._apply_libseccomp() is True

        binding.SyscallFilter.assert_called_once_with(
            defaction=("ERRNO", errno.EPERM),
        )
        installed_filter.load.assert_called_once_with()
        calls = installed_filter.add_rule.call_args_list
        assert any(call.args == ("ALLOW", "read") for call in calls)
        assert any(call.args == (("ERRNO", errno.EPERM), "mount") for call in calls)

    def test_manual_bpf_installs_owned_program(self) -> None:
        libc = MagicMock()
        libc.prctl.return_value = 0

        with patch("ctypes.CDLL", return_value=libc):
            assert SeccompFilter.default()._apply_manual_bpf() is True

        assert libc.prctl.call_count == 2

    def test_manual_bpf_fails_closed_when_no_new_privs_is_rejected(self) -> None:
        libc = MagicMock()
        libc.prctl.return_value = -1

        with (
            patch("ctypes.CDLL", return_value=libc),
            patch("ctypes.get_errno", return_value=errno.EPERM),
        ):
            assert SeccompFilter.default()._apply_manual_bpf() is False

        libc.prctl.assert_called_once()

    def test_manual_bpf_fails_closed_when_filter_install_is_rejected(self) -> None:
        libc = MagicMock()
        libc.prctl.side_effect = [0, -1]

        with (
            patch("ctypes.CDLL", return_value=libc),
            patch("ctypes.get_errno", return_value=errno.EACCES),
        ):
            assert SeccompFilter.default()._apply_manual_bpf() is False

        assert libc.prctl.call_count == 2

    def test_manual_bpf_import_or_assembly_error_is_nonfatal(self) -> None:
        with patch("ctypes.CDLL", side_effect=OSError("libc unavailable")):
            assert SeccompFilter.default()._apply_manual_bpf() is False
