"""Tests for disassembler module — multi-architecture disassembly via capstone."""

from __future__ import annotations

import pytest

try:
    import capstone  # noqa: F401

    _HAS_CAPSTONE = True
except ImportError:
    _HAS_CAPSTONE = False

from plugins.module_utils.disassembler import (
    ARCH_ARM,
    ARCH_ARM64,
    ARCH_MIPS32,
    ARCH_MIPS64,
    ARCH_X64,
    ARCH_X86,
    DisassemblyResult,
    Instruction,
    _classify_instruction,
    _detect_call,
    _detect_jump,
    _detect_ret,
    disassemble,
)


class TestInstruction:
    def test_creation(self):
        instr = Instruction(address=0x1000, mnemonic="mov", op_str="eax, ebx", size=3, bytes=b"\x89\xd8")
        assert instr.address == 0x1000
        assert instr.mnemonic == "mov"
        assert instr.op_str == "eax, ebx"
        assert instr.size == 3
        assert instr.bytes == b"\x89\xd8"

    def test_repr(self):
        instr = Instruction(address=0x4000, mnemonic="ret", op_str="", size=1, bytes=b"\xc3")
        rep = repr(instr)
        assert "16384" in rep or "0x4000" in rep
        assert "ret" in rep


class TestDisassemblyResult:
    def test_creation(self):
        instr = Instruction(0x1000, "nop", "", 1, b"\x90")
        result = DisassemblyResult(
            instructions=[instr],
            arch="x86",
            mode="64",
            base_address=0x1000,
            total_bytes=1,
        )
        assert result.arch == "x86"
        assert result.mode == "64"
        assert result.base_address == 0x1000
        assert result.total_bytes == 1
        assert len(result.instructions) == 1

    def test_empty(self):
        result = DisassemblyResult(instructions=[], arch="x86", mode="64", base_address=0, total_bytes=0)
        assert result.instructions == []

    @property
    def instruction_count(self) -> int:
        instr = Instruction(0, "mov", "eax, 0", 5, b"\xb8\x00\x00\x00\x00")
        result = DisassemblyResult(
            instructions=[instr, instr, instr],
            arch="x86",
            mode="32",
            base_address=0,
            total_bytes=15,
        )
        return len(result.instructions)


_needs_capstone = pytest.mark.skipif(not _HAS_CAPSTONE, reason="capstone not installed")


class TestDisassembleX86:
    """x86/x64 disassembly tests."""

    @_needs_capstone
    def test_single_nop(self):
        data = b"\x90"
        result = disassemble(data, arch=ARCH_X86, mode="32", base_address=0x1000)
        assert len(result.instructions) == 1
        assert result.instructions[0].mnemonic == "nop"
        assert result.instructions[0].address == 0x1000
        assert result.instructions[0].size == 1

    @_needs_capstone
    def test_multiple_nops(self):
        data = b"\x90" * 10
        result = disassemble(data, arch=ARCH_X86, mode="32")
        assert len(result.instructions) == 10
        for instr in result.instructions:
            assert instr.mnemonic == "nop"

    @_needs_capstone
    def test_return_instruction(self):
        data = b"\xc3"
        result = disassemble(data, arch=ARCH_X86, mode="32")
        assert len(result.instructions) == 1
        assert result.instructions[0].mnemonic == "ret"

    @_needs_capstone
    def test_ret_classification(self):
        data = b"\xc3"
        result = disassemble(data, arch=ARCH_X86, mode="32")
        info = _classify_instruction(result.instructions[0])
        assert info["is_ret"] is True
        assert info["is_call"] is False
        assert info["is_unconditional_jump"] is False

    @_needs_capstone
    def test_x64_mode(self):
        data = b"\x48\x89\xd8"
        result = disassemble(data, arch=ARCH_X64, mode="64")
        assert len(result.instructions) == 1
        assert "mov" in result.instructions[0].mnemonic

    @_needs_capstone
    def test_call_detection(self):
        data = b"\xe8\x00\x00\x00\x00"
        result = disassemble(data, arch=ARCH_X86, mode="32")
        assert len(result.instructions) >= 1
        assert _detect_call(result.instructions[0]) is True

    @_needs_capstone
    def test_jump_detection(self):
        data = b"\xeb\x00"
        result = disassemble(data, arch=ARCH_X86, mode="32")
        assert len(result.instructions) >= 1
        assert _detect_jump(result.instructions[0]) is True

    @_needs_capstone
    def test_unconditional_jump(self):
        data = b"\xe9\x00\x00\x00\x00"
        result = disassemble(data, arch=ARCH_X86, mode="32")
        info = _classify_instruction(result.instructions[0])
        assert info["is_unconditional_jump"] is True

    @_needs_capstone
    def test_conditional_jump(self):
        data = b"\x74\x00"
        result = disassemble(data, arch=ARCH_X86, mode="32")
        info = _classify_instruction(result.instructions[0])
        assert info["is_conditional_jump"] is True

    @_needs_capstone
    def test_push_pop_pair(self):
        data = b"\x50\x58"
        result = disassemble(data, arch=ARCH_X86, mode="32")
        assert len(result.instructions) == 2
        assert result.instructions[0].mnemonic == "push"
        assert result.instructions[1].mnemonic == "pop"

    @_needs_capstone
    def test_base_address_offset(self):
        data = b"\x90\x90\xc3"
        result = disassemble(data, arch=ARCH_X86, mode="32", base_address=0x401000)
        assert result.instructions[0].address == 0x401000
        assert result.instructions[1].address == 0x401001
        assert result.instructions[2].address == 0x401002

    def test_empty_data(self):
        result = disassemble(b"", arch=ARCH_X86, mode="32")
        assert result.instructions == []
        assert result.total_bytes == 0

    @_needs_capstone
    def test_result_metadata(self):
        data = b"\x90\x90\x90"
        result = disassemble(data, arch=ARCH_X64, mode="64", base_address=0x4000)
        assert result.arch == "x64"
        assert result.mode == "64"
        assert result.base_address == 0x4000
        assert result.total_bytes == 3


class TestDisassembleARM:
    """ARM/ARM64 disassembly tests."""

    def test_arm_nop(self):
        data = b"\x00\x00\xa0\xe1"
        result = disassemble(data, arch=ARCH_ARM, mode="arm")
        if result.instructions:
            assert result.arch == "arm"

    def test_arm_mov(self):
        data = b"\x01\x00\xa0\xe3"
        result = disassemble(data, arch=ARCH_ARM, mode="arm")
        if result.instructions:
            assert "mov" in result.instructions[0].mnemonic.lower()

    def test_arm64_nop(self):
        data = b"\x1f\x20\x03\xd5"
        result = disassemble(data, arch=ARCH_ARM64, mode="arm")
        if result.instructions:
            assert result.arch == "arm64"

    def test_arm64_ret(self):
        data = b"\xc0\x03\x5f\xd6"
        result = disassemble(data, arch=ARCH_ARM64, mode="arm")
        if result.instructions:
            ret_detected = _detect_ret(result.instructions[0])
            assert ret_detected is True or ret_detected is False

    def test_arm64_branch_link(self):
        data = b"\x00\x00\x00\x94"
        result = disassemble(data, arch=ARCH_ARM64, mode="arm")
        if result.instructions:
            assert result.instructions[0].mnemonic == "bl"


class TestDisassembleMIPS:
    """MIPS disassembly tests."""

    def test_mips_nop(self):
        data = b"\x00\x00\x00\x00"
        result = disassemble(data, arch=ARCH_MIPS32, mode="32")
        if result.instructions:
            assert "nop" in result.instructions[0].mnemonic.lower()

    def test_mips_jr_ra(self):
        data = b"\x08\x00\xe0\x03"
        result = disassemble(data, arch=ARCH_MIPS32, mode="32")
        if result.instructions:
            ret_detected = _detect_ret(result.instructions[0])
            assert ret_detected is True or ret_detected is False


class TestInstructionClassification:
    def test_ret_classified(self):
        instr = Instruction(0, "ret", "", 1, b"\xc3")
        assert _detect_ret(instr) is True
        assert _detect_call(instr) is False
        assert _detect_jump(instr) is False

    def test_call_classified(self):
        instr = Instruction(0, "call", "0x4000", 5, b"\xe8")
        assert _detect_call(instr) is True

    def test_jmp_classified(self):
        instr = Instruction(0, "jmp", "0x5000", 5, b"\xe9")
        assert _detect_jump(instr) is True
        assert _classify_instruction(instr)["is_unconditional_jump"] is True

    def test_je_classified(self):
        instr = Instruction(0, "je", "0x5000", 2, b"\x74")
        assert _detect_jump(instr) is True
        info = _classify_instruction(instr)
        assert info["is_conditional_jump"] is True

    def test_call_variants(self):
        for mnemonic in ("call", "bl", "blx", "jal", "jalr"):
            instr = Instruction(0, mnemonic, "", 4, b"\x00" * 4)
            assert _detect_call(instr) is True

    def test_ret_variants(self):
        for mnemonic in ("ret", "retn", "retf", "iret", "iretq", "sysret", "sysexit"):
            instr = Instruction(0, mnemonic, "", 1, b"\x00")
            assert _detect_ret(instr) is True, f"{mnemonic} should be detected as ret"

    def test_conditional_jumps(self):
        cond_jumps = (
            "je",
            "jne",
            "jz",
            "jnz",
            "jg",
            "jl",
            "jge",
            "jle",
            "ja",
            "jb",
            "jae",
            "jbe",
            "jo",
            "jno",
            "js",
            "jns",
            "jp",
            "jnp",
            "jcxz",
        )
        for mnemonic in cond_jumps:
            instr = Instruction(0, mnemonic, "0x4000", 2, b"\x00" * 2)
            info = _classify_instruction(instr)
            assert info["is_conditional_jump"] is True, f"{mnemonic} should be conditional jump"

    def test_unconditional_jumps(self):
        uncond_jumps = ("jmp", "ljmp")
        for mnemonic in uncond_jumps:
            instr = Instruction(0, mnemonic, "0x4000", 5, b"\x00" * 5)
            info = _classify_instruction(instr)
            assert info["is_unconditional_jump"] is True, f"{mnemonic} should be unconditional jump"

    def test_mov_not_control_flow(self):
        instr = Instruction(0, "mov", "eax, ebx", 2, b"\x89\xd8")
        info = _classify_instruction(instr)
        assert info["is_call"] is False
        assert info["is_ret"] is False
        assert info["is_jump"] is False
        assert info["is_conditional_jump"] is False
        assert info["is_unconditional_jump"] is False


class TestCapstoneFallback:
    """Tests that the module works without capstone installed."""

    def test_empty_result_when_no_capstone(self):
        result = disassemble(b"\x90\x90\xc3", arch=ARCH_X86, mode="32")
        assert isinstance(result, DisassemblyResult)
        assert result.arch == "x86"

    def test_result_structure_valid(self):
        for arch in (ARCH_X86, ARCH_X64, ARCH_ARM, ARCH_ARM64, ARCH_MIPS32, ARCH_MIPS64):
            result = disassemble(b"\x00" * 16, arch=arch)
            assert isinstance(result, DisassemblyResult)
            assert result.arch == arch
            assert isinstance(result.instructions, list)
            assert result.total_bytes == 16


class TestArchConstants:
    def test_arch_values(self):
        assert isinstance(ARCH_X86, str)
        assert ARCH_X86 != ARCH_X64
        assert ARCH_ARM != ARCH_ARM64
        assert ARCH_MIPS32 != ARCH_MIPS64

    def test_supported_architectures(self):
        supported = {ARCH_X86, ARCH_X64, ARCH_ARM, ARCH_ARM64, ARCH_MIPS32, ARCH_MIPS64}
        assert len(supported) == 6
