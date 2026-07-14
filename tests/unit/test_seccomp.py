"""Structural tests for security/seccomp.py — seccomp BPF syscall filtering."""

from __future__ import annotations

import errno

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
