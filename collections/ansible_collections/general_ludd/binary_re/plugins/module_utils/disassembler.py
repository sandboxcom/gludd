"""Multi-architecture binary disassembler via capstone (with fallback).

Supports x86, x64, ARM, ARM64, MIPS32, and MIPS64. Uses the capstone
library when available; falls back to returning an empty result otherwise
so the module is always importable.
"""

from __future__ import annotations

from dataclasses import dataclass

ARCH_X86 = "x86"
ARCH_X64 = "x64"
ARCH_ARM = "arm"
ARCH_ARM64 = "arm64"
ARCH_MIPS32 = "mips"
ARCH_MIPS64 = "mips64"

_CAPSTONE_ARCH_MAP: dict[str, int] = {
    ARCH_X86: 3,
    ARCH_X64: 3,
    ARCH_ARM: 2,
    ARCH_ARM64: 2,
    ARCH_MIPS32: 5,
    ARCH_MIPS64: 5,
}

_CAPSTONE_MODE_MAP: dict[str, int] = {
    ARCH_X86: 4,
    ARCH_X64: 4,
    ARCH_ARM: 0,
    ARCH_ARM64: 1,
    ARCH_MIPS32: 4,
    ARCH_MIPS64: 4,
}

CALL_MNEMONICS: frozenset[str] = frozenset(
    {
        "call",
        "bl",
        "blx",
        "jal",
        "jalr",
        "bal",
        "blr",
        "blra",
        "jals",
        "jalx",
    }
)

RET_MNEMONICS: frozenset[str] = frozenset(
    {
        "ret",
        "retn",
        "retf",
        "iret",
        "iretq",
        "sysret",
        "sysexit",
        "eret",
        "rfe",
    }
)

CONDITIONAL_JUMP_PREFIXES: tuple[str, ...] = (
    "j",
    "b",
)

UNCONDITIONAL_JUMP_MNEMONICS: frozenset[str] = frozenset(
    {
        "jmp",
        "ljmp",
        "b",
        "br",
    }
)

_FULL_CONDITIONAL_JUMPS: frozenset[str] = frozenset(
    {
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
        "jecxz",
        "jrcxz",
        "beq",
        "bne",
        "bgt",
        "blt",
        "bge",
        "ble",
        "bhi",
        "blo",
        "bhs",
        "bls",
        "bcs",
        "bcc",
        "bvs",
        "bvc",
        "bmi",
        "bpl",
        "cbz",
        "cbnz",
        "tbz",
        "tbnz",
    }
)


@dataclass
class Instruction:
    address: int
    mnemonic: str
    op_str: str
    size: int
    bytes: bytes


@dataclass
class DisassemblyResult:
    instructions: list[Instruction]
    arch: str
    mode: str
    base_address: int
    total_bytes: int


def _get_capstone():
    try:
        import capstone as _cs

        return _cs
    except ImportError:
        return None


def _detect_call(instr: Instruction) -> bool:
    m = instr.mnemonic.lower()
    return m in CALL_MNEMONICS or m.startswith("call")


def _detect_ret(instr: Instruction) -> bool:
    return instr.mnemonic.lower() in RET_MNEMONICS


def _detect_jump(instr: Instruction) -> bool:
    m = instr.mnemonic.lower()
    if m in _FULL_CONDITIONAL_JUMPS:
        return True
    if m in UNCONDITIONAL_JUMP_MNEMONICS:
        return True
    return m.startswith("j") or m.startswith("b")


def _classify_instruction(instr: Instruction) -> dict:
    m = instr.mnemonic.lower()
    is_call = m in CALL_MNEMONICS
    is_ret = m in RET_MNEMONICS
    is_cond = m in _FULL_CONDITIONAL_JUMPS
    is_uncond = m in UNCONDITIONAL_JUMP_MNEMONICS
    is_jump = is_cond or is_uncond
    if not is_jump and (m.startswith("j") or m.startswith("b")):
        is_jump = True
    return {
        "is_call": is_call,
        "is_ret": is_ret,
        "is_jump": is_jump,
        "is_conditional_jump": is_cond,
        "is_unconditional_jump": is_uncond,
        "mnemonic": instr.mnemonic,
        "address": instr.address,
        "size": instr.size,
    }


def disassemble(
    data: bytes,
    arch: str = ARCH_X86,
    mode: str | None = None,
    base_address: int = 0,
) -> DisassemblyResult:
    if mode is None:
        mode = _default_mode(arch)
    if not data:
        return DisassemblyResult(
            instructions=[],
            arch=arch,
            mode=mode,
            base_address=base_address,
            total_bytes=0,
        )

    cs = _get_capstone()
    if cs is None:
        return DisassemblyResult(
            instructions=[],
            arch=arch,
            mode=mode,
            base_address=base_address,
            total_bytes=len(data),
        )

    cs_arch = _CAPSTONE_ARCH_MAP.get(arch)
    cs_mode = _CAPSTONE_MODE_MAP.get(arch)
    if cs_arch is None or cs_mode is None:
        return DisassemblyResult(
            instructions=[],
            arch=arch,
            mode=mode,
            base_address=base_address,
            total_bytes=len(data),
        )

    try:
        md = cs.Cs(cs_arch, cs_mode)
        md.detail = True
        md.skipdata = True
        instructions: list[Instruction] = []
        for insn in md.disasm(data, base_address):
            instructions.append(
                Instruction(
                    address=insn.address,
                    mnemonic=insn.mnemonic,
                    op_str=insn.op_str,
                    size=insn.size,
                    bytes=bytes(insn.bytes),
                )
            )
        return DisassemblyResult(
            instructions=instructions,
            arch=arch,
            mode=mode,
            base_address=base_address,
            total_bytes=len(data),
        )
    except Exception:
        return DisassemblyResult(
            instructions=[],
            arch=arch,
            mode=mode,
            base_address=base_address,
            total_bytes=len(data),
        )


def _default_mode(arch: str) -> str:
    if arch == ARCH_X86:
        return "32"
    if arch == ARCH_X64:
        return "64"
    return "32"
