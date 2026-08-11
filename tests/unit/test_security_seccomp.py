"""Deep tests for seccomp — BPF assembly, filter introspection, deny-listing."""

from __future__ import annotations

import errno
import platform

import pytest

from general_ludd.security.seccomp import (
    _CLONE_NS_MASK,
    _SYSCALL_NUMBERS,
    DEFAULT_ALLOWED_SYSCALLS,
    DEFAULT_DENIED_SYSCALLS,
    SeccompFilter,
    _audit_arch,
    _iter_bits,
    _native_arch,
)


class TestSeccompFilterDefault:
    def test_default_filter(self) -> None:
        f = SeccompFilter.default()
        assert f.default_action == "allow"
        assert f.deny_action == "kill"
        assert f.allowed_syscalls == DEFAULT_ALLOWED_SYSCALLS
        assert f.denied_syscalls == DEFAULT_DENIED_SYSCALLS
        assert f.errno == errno.EPERM

    def test_is_denied(self) -> None:
        f = SeccompFilter.default()
        assert f.is_denied("mount")
        assert not f.is_denied("read")

    def test_is_allowed_default_allow(self) -> None:
        f = SeccompFilter.default()
        assert f.is_allowed("read")
        assert f.is_allowed("write")
        assert not f.is_allowed("mount")

    def test_is_allowed_strict_mode(self) -> None:
        f = SeccompFilter(default_action="errno")
        assert f.is_allowed("read")
        assert not f.is_allowed("nonexistent_syscall")
        assert not f.is_allowed("mount")


class TestBPFAssemblyX86_64:
    @pytest.fixture
    def filter(self) -> SeccompFilter:
        return SeccompFilter.default()

    def test_build_bpf_x86_64(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="x86_64")
        assert len(program) > 0
        for insn in program:
            assert len(insn) == 4

    def test_bpf_starts_with_arch_check(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="x86_64")
        assert program[0] == (0x20, 0, 0, 4)
        assert program[1] == (0x15, 1, 0, 0xC000003E)
        assert program[2] == (0x06, 0, 0, 0x80000000)

    def test_bpf_loads_syscall_number(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="x86_64")
        assert program[3] == (0x20, 0, 0, 0)

    def test_bpf_ends_with_default_allow(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="x86_64")
        last = program[-1]
        assert last == (0x06, 0, 0, 0x7FFF0000)

    def test_bpf_denies_mount(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="x86_64")
        found = False
        for insn in program:
            if insn[3] == 165 and insn[0] == 0x15:
                found = True
                break
        assert found

    def test_bpf_denies_unshare(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="x86_64")
        found = False
        for insn in program:
            if insn[3] == 272 and insn[0] == 0x15:
                found = True
                break
        assert found


class TestBPFAssemblyAArch64:
    @pytest.fixture
    def filter(self) -> SeccompFilter:
        return SeccompFilter.default()

    def test_build_bpf_aarch64(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="aarch64")
        assert len(program) > 0

    def test_bpf_aarch64_arch_check(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="aarch64")
        assert program[1] == (0x15, 1, 0, 0xC00000B7)

    def test_bpf_aarch64_denies_mount(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="aarch64")
        found = False
        for insn in program:
            if insn[3] == 40 and insn[0] == 0x15:
                found = True
                break
        assert found


class TestBPFStrictMode:
    def test_strict_mode_has_whitelist(self) -> None:
        f = SeccompFilter(default_action="errno")
        program = f.build_bpf(arch="x86_64")
        assert len(program) > 0

    def test_strict_mode_ends_with_errno(self) -> None:
        f = SeccompFilter(default_action="errno")
        program = f.build_bpf(arch="x86_64")
        last = program[-1]
        assert last[0] == 0x06  # BPF_RET instruction


class TestBPFErrnoDeny:
    def test_errno_deny_action(self) -> None:
        f = SeccompFilter(deny_action="errno", errno=errno.EACCES)
        program = f.build_bpf(arch="x86_64")
        found_errno = False
        for insn in program:
            code, _jt, _jf, k = insn
            if code == 0x06 and k & 0x00050000:
                found_errno = True
                break
        assert found_errno


class TestCloneNamespaceGuard:
    @pytest.fixture
    def filter(self) -> SeccompFilter:
        return SeccompFilter.default()

    def test_clone_in_bpf(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="x86_64")
        clone_insns = [i for i in program if i[3] == 56 and i[0] == 0x15]
        assert len(clone_insns) >= 1

    def test_clone_has_jsets(self, filter: SeccompFilter) -> None:
        program = filter.build_bpf(arch="x86_64")
        jset_insns = [i for i in program if i[0] == 0x45]
        assert len(jset_insns) >= 1


class TestIterBits:
    def test_empty_mask(self) -> None:
        assert _iter_bits(0) == []

    def test_single_bit(self) -> None:
        assert _iter_bits(1) == [1]

    def test_multiple_bits(self) -> None:
        assert _iter_bits(5) == [1, 4]

    def test_clone_ns_mask(self) -> None:
        bits = _iter_bits(_CLONE_NS_MASK)
        assert len(bits) >= 5


class TestSyscallNumbers:
    def test_x86_64_has_deny_list(self) -> None:
        nums = _SYSCALL_NUMBERS["x86_64"]
        for name in DEFAULT_DENIED_SYSCALLS:
            if name not in ("mount_setattr",):
                assert name in nums, f"x86_64 missing {name}"

    def test_aarch64_has_deny_list(self) -> None:
        nums = _SYSCALL_NUMBERS["aarch64"]
        for name in ("mount", "umount2", "unshare", "setns", "pivot_root", "chroot"):
            assert name in nums, f"aarch64 missing {name}"

    def test_clone_in_both(self) -> None:
        assert "clone" in _SYSCALL_NUMBERS["x86_64"]
        assert "clone" in _SYSCALL_NUMBERS["aarch64"]


class TestPlatformSupport:
    def test_is_supported_on_macos(self) -> None:
        if platform.system() == "Darwin":
            assert not SeccompFilter.is_supported()
        else:
            result = SeccompFilter.is_supported()
            assert isinstance(result, bool)


class TestNativeArch:
    def test_returns_valid_key(self) -> None:
        arch = _native_arch()
        assert arch in ("x86_64", "aarch64")

    def test_audit_arch(self) -> None:
        assert _audit_arch("x86_64") == 0xC000003E
        assert _audit_arch("aarch64") == 0xC00000B7
        assert _audit_arch("unknown") == 0xC000003E


class TestSeccompFilterCustom:
    def test_custom_allowed(self) -> None:
        f = SeccompFilter(
            allowed_syscalls=frozenset({"read", "write"}),
            default_action="errno",
        )
        assert f.is_allowed("read")
        assert not f.is_allowed("open")

    def test_custom_denied(self) -> None:
        f = SeccompFilter(
            denied_syscalls=frozenset({"bad_syscall"}),
        )
        assert f.is_denied("bad_syscall")

    def test_custom_errno(self) -> None:
        f = SeccompFilter(errno=errno.EACCES)
        assert f.errno == errno.EACCES
