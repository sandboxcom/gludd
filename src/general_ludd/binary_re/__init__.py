"""Binary reverse engineering package.

Typed contracts for binary analysis results from disassemblers (Ghidra,
Radare2, IDA Pro, etc.).  The ansible collection under
``collections/ansible_collections/general_ludd/binary_re/`` wraps these
typed entry points; it never duplicates contracts.
"""

from __future__ import annotations

from general_ludd.binary_re.contracts import (
    BinaryFormat,
    DisassemblyResult,
    ExportTable,
    ImportTable,
    SectionHeader,
)

__all__ = [
    "BinaryFormat",
    "DisassemblyResult",
    "ExportTable",
    "ImportTable",
    "SectionHeader",
]
