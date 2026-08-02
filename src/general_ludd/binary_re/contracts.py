"""Typed contracts for binary reverse engineering analysis results.

Defines the immutable data shapes for disassembly output across backends
(Ghidra, Radare2, IDA Pro, etc.):

  - :class:`BinaryFormat` — PE, ELF, Mach-O, COFF, WASM, or UNKNOWN
  - :class:`SectionHeader` — one named section in a binary image
  - :class:`ImportTable` — imported symbols from a shared library
  - :class:`ExportTable` — exported symbols from a shared library
  - :class:`DisassemblyResult` — full disassembly output for one binary

All mutating constructors validate contract-level invariants: missing or
empty names, out-of-range entropy, negative sizes, and mis-typed tuple
elements raise ``ValueError`` rather than producing a silently malformed
record.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

_VALID_PERMISSIONS: frozenset[str] = frozenset(["", "r", "rx", "rw", "rwx", "r_shared"])

_MAX_ENTROPY = 8.0


class BinaryFormat(enum.StrEnum):
    """Binary file format identifiers."""

    PE = "PE"
    ELF = "ELF"
    MACH_O = "Mach-O"
    COFF = "COFF"
    WASM = "WASM"
    UNKNOWN = "UNKNOWN"


def _require_nonempty_str(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_non_negative(value: int, field_name: str) -> None:
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0, got {value}")


def _require_entropy_range(value: float, field_name: str) -> None:
    if not (0.0 <= value <= _MAX_ENTROPY):
        raise ValueError(f"{field_name} must be in [0.0, {_MAX_ENTROPY}], got {value}")


def _require_permissions(value: str, field_name: str) -> None:
    if value not in _VALID_PERMISSIONS:
        raise ValueError(f"{field_name} must be one of {sorted(_VALID_PERMISSIONS)}, got {value!r}")


@dataclass(frozen=True)
class SectionHeader:
    """Metadata for one named section in a binary image.

    Models the common fields across PE sections, ELF program headers,
    and Mach-O segments.
    """

    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int
    permissions: str = "rx"
    entropy: float = 0.0
    content_hash: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_str(self.name, "name")
        _require_non_negative(self.virtual_address, "virtual_address")
        _require_non_negative(self.virtual_size, "virtual_size")
        _require_non_negative(self.raw_offset, "raw_offset")
        _require_non_negative(self.raw_size, "raw_size")
        _require_permissions(self.permissions, "permissions")
        _require_entropy_range(self.entropy, "entropy")


@dataclass(frozen=True)
class ImportTable:
    """Imported symbols from a shared library.

    If ``ordinal_only`` is ``True``, the binary imports by ordinal only
    and ``symbols`` will be empty.
    """

    library: str
    symbols: tuple[str, ...] = ()
    ordinal_only: bool = False

    def __post_init__(self) -> None:
        _require_nonempty_str(self.library, "library")


@dataclass(frozen=True)
class ExportTable:
    """Exported symbols from a shared library.

    ``ordinal_base`` is the starting ordinal for the first exported symbol.
    """

    library: str
    symbols: tuple[str, ...] = ()
    ordinal_base: int = 0

    def __post_init__(self) -> None:
        _require_nonempty_str(self.library, "library")
        _require_non_negative(self.ordinal_base, "ordinal_base")


@dataclass(frozen=True)
class DisassemblyResult:
    """Complete disassembly output for one binary.

    Aggregates section headers, import/export tables, and entry-point
    metadata from a single disassembler run.
    """

    binary_path: str
    binary_format: BinaryFormat
    architecture: str
    sections: tuple[SectionHeader, ...] = ()
    imports: tuple[ImportTable, ...] = ()
    exports: tuple[ExportTable, ...] = ()
    entry_point: int = 0
    function_count: int = 0
    disassembly_tool: str = "unknown"
    timestamp: int = 0

    def __post_init__(self) -> None:
        _require_nonempty_str(self.binary_path, "binary_path")
        _require_nonempty_str(self.architecture, "architecture")
        if not isinstance(self.binary_format, BinaryFormat):
            raise ValueError(f"binary_format must be a BinaryFormat instance, got {self.binary_format!r}")
        _require_non_negative(self.entry_point, "entry_point")
        _require_non_negative(self.function_count, "function_count")
        _require_non_negative(self.timestamp, "timestamp")
        for s in self.sections:
            if not isinstance(s, SectionHeader):
                raise ValueError(f"sections must contain SectionHeader instances, got {type(s)}")
        for imp in self.imports:
            if not isinstance(imp, ImportTable):
                raise ValueError(f"imports must contain ImportTable instances, got {type(imp)}")
        for exp in self.exports:
            if not isinstance(exp, ExportTable):
                raise ValueError(f"exports must contain ExportTable instances, got {type(exp)}")


__all__ = [
    "BinaryFormat",
    "DisassemblyResult",
    "ExportTable",
    "ImportTable",
    "SectionHeader",
]
