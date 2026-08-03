"""PE/COFF (Portable Executable) binary analyzer.

Parses DOS header, PE file/optional headers, section tables, data directories,
import/export tables, and computes per-section entropy.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

PE_MAGIC = b"MZ"

MACHINE_TYPES: dict[int, str] = {
    0x014C: "i386",
    0x0200: "IA64",
    0x8664: "AMD64",
    0xAA64: "ARM64",
    0x01C4: "ARMNT",
    0x01C0: "ARM",
    0x01F0: "PPC",
    0x01F1: "PPCFP",
    0x0EBC: "EFI",
}

SUBSYSTEM_TYPES: dict[int, str] = {
    0: "UNKNOWN",
    1: "NATIVE",
    2: "WINDOWS_GUI",
    3: "WINDOWS_CUI",
    5: "OS2_CUI",
    7: "POSIX_CUI",
    9: "WINDOWS_CE_GUI",
    10: "EFI_APPLICATION",
    11: "EFI_BOOT_SERVICE_DRIVER",
    12: "EFI_RUNTIME_DRIVER",
    13: "EFI_ROM",
    14: "XBOX",
    16: "WINDOWS_BOOT_APPLICATION",
}

DIRECTORY_NAMES: dict[int, str] = {
    0: "EXPORT",
    1: "IMPORT",
    2: "RESOURCE",
    3: "EXCEPTION",
    4: "SECURITY",
    5: "BASERELOC",
    6: "DEBUG",
    7: "ARCHITECTURE",
    8: "GLOBALPTR",
    9: "TLS",
    10: "LOAD_CONFIG",
    11: "BOUND_IMPORT",
    12: "IAT",
    13: "DELAY_IMPORT",
    14: "COM_DESCRIPTOR",
}

CHARACTERISTIC_NAMES: dict[int, str] = {
    0x0001: "RELOCS_STRIPPED",
    0x0002: "EXECUTABLE_IMAGE",
    0x0004: "LINE_NUMS_STRIPPED",
    0x0008: "LOCAL_SYMS_STRIPPED",
    0x0010: "AGGRESSIVE_WS_TRIM",
    0x0020: "LARGE_ADDRESS_AWARE",
    0x0040: "RESERVED",
    0x0080: "BYTES_REVERSED_LO",
    0x0100: "32BIT_MACHINE",
    0x0200: "DEBUG_STRIPPED",
    0x0400: "REMOVABLE_RUN_FROM_SWAP",
    0x0800: "NET_RUN_FROM_SWAP",
    0x1000: "SYSTEM",
    0x2000: "DLL",
    0x4000: "UP_SYSTEM_ONLY",
    0x8000: "BYTES_REVERSED_HI",
}

DLL_CHARACTERISTIC_NAMES: dict[int, str] = {
    0x0040: "DYNAMIC_BASE",
    0x0080: "FORCE_INTEGRITY",
    0x0100: "NX_COMPAT",
    0x0200: "NO_ISOLATION",
    0x0400: "NO_SEH",
    0x0800: "NO_BIND",
    0x1000: "APPCONTAINER",
    0x2000: "WDM_DRIVER",
    0x4000: "GUARD_CF",
    0x8000: "TERMINAL_SERVER_AWARE",
}

SECTION_CHARACTERISTICS: dict[int, str] = {
    0x00000020: "CNT_CODE",
    0x00000040: "CNT_INITIALIZED_DATA",
    0x00000080: "CNT_UNINITIALIZED_DATA",
    0x02000000: "MEM_DISCARDABLE",
    0x04000000: "MEM_NOT_CACHED",
    0x08000000: "MEM_NOT_PAGED",
    0x10000000: "MEM_SHARED",
    0x20000000: "MEM_EXECUTE",
    0x40000000: "MEM_READ",
    0x80000000: "MEM_WRITE",
}


@dataclass
class PEDOSHeader:
    e_magic: str = ""
    e_lfanew: int = 0


@dataclass
class PEFileHeader:
    machine: str = "unknown"
    num_sections: int = 0
    timestamp: int = 0
    characteristics: list[str] = field(default_factory=list)


@dataclass
class PEOptionalHeader:
    magic: str = "unknown"
    entry_point: int = 0
    image_base: int = 0
    section_alignment: int = 0
    file_alignment: int = 0
    subsystem: str = "UNKNOWN"
    dll_characteristics: list[str] = field(default_factory=list)


@dataclass
class PEDataDirectory:
    name: str
    virtual_address: int = 0
    size: int = 0


@dataclass
class PESection:
    name: str
    virtual_address: int = 0
    virtual_size: int = 0
    raw_size: int = 0
    raw_offset: int = 0
    characteristics: list[str] = field(default_factory=list)
    entropy: float = 0.0


@dataclass
class PEImport:
    dll_name: str
    functions: list[str] = field(default_factory=list)


@dataclass
class PEExport:
    name: str
    ordinal: int = 0
    address: int = 0


@dataclass
class PEAnalysisResult:
    dos_header: PEDOSHeader
    file_header: PEFileHeader = field(default_factory=PEFileHeader)
    optional_header: PEOptionalHeader = field(default_factory=PEOptionalHeader)
    data_directories: list[PEDataDirectory] = field(default_factory=list)
    sections: list[PESection] = field(default_factory=list)
    imports: list[PEImport] = field(default_factory=list)
    exports: list[PEExport] = field(default_factory=list)
    is_32bit: bool = False
    is_64bit: bool = False
    is_dll: bool = False
    is_dotnet: bool = False
    has_debug: bool = False


def _read_dos_header(data: bytes) -> PEDOSHeader | None:
    if len(data) < 0x40:
        return None
    if data[:2] != PE_MAGIC:
        return None
    try:
        e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    except struct.error:
        return None
    if e_lfanew < 0x40:
        return None
    return PEDOSHeader(e_magic="MZ", e_lfanew=e_lfanew)


def _decode_characteristics(flags: int) -> list[str]:
    result: list[str] = []
    for val, name in CHARACTERISTIC_NAMES.items():
        if flags & val:
            result.append(name)
    return result


def _decode_dll_characteristics(flags: int) -> list[str]:
    result: list[str] = []
    for val, name in DLL_CHARACTERISTIC_NAMES.items():
        if flags & val:
            result.append(name)
    return result


def _decode_section_characteristics(flags: int) -> list[str]:
    result: list[str] = []
    for val, name in SECTION_CHARACTERISTICS.items():
        if flags & val:
            result.append(name)
    return result


def _shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / n
            entropy -= p * math.log2(p)
    return entropy


def _read_pe_header(data: bytes) -> tuple[PEFileHeader, PEOptionalHeader] | None:
    dos = _read_dos_header(data)
    if dos is None or dos.e_lfanew <= 0:
        return None
    pe_offset = dos.e_lfanew
    if pe_offset + 4 > len(data):
        return None
    if data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        return None
    coff = pe_offset + 4
    try:
        machine_raw, num_sections, timestamp, _, _, _opt_hdr_size, characteristics = struct.unpack_from(
            "<HHIIIHH", data, coff
        )
    except struct.error:
        return None
    file_hdr = PEFileHeader(
        machine=MACHINE_TYPES.get(machine_raw, f"0x{machine_raw:04X}"),
        num_sections=num_sections,
        timestamp=timestamp,
        characteristics=_decode_characteristics(characteristics),
    )
    opt_hdr = coff + 20
    if opt_hdr + 2 > len(data):
        return file_hdr, PEOptionalHeader()
    try:
        magic_raw = struct.unpack_from("<H", data, opt_hdr)[0]
    except struct.error:
        return file_hdr, PEOptionalHeader()
    if magic_raw == 0x020B:
        return file_hdr, _read_pe32plus_optional(data, opt_hdr)
    elif magic_raw == 0x010B:
        return file_hdr, _read_pe32_optional(data, opt_hdr)
    return file_hdr, PEOptionalHeader(magic=f"0x{magic_raw:04X}")


def _read_pe32plus_optional(data: bytes, opt_hdr: int) -> PEOptionalHeader:
    try:
        (
            entry_point,
            _base_of_code,
            image_base,
            section_alignment,
            file_alignment,
            _maj_os,
            _min_os,
            _maj_img,
            _min_img,
            _maj_subsys,
            _min_subsys,
            _reserved,
            _size_image,
            _size_headers,
            _checksum,
            subsystem_raw,
            dll_char_raw,
        ) = struct.unpack_from("<IIQIIHHHHHHIIIIHH", data, opt_hdr + 16)
    except struct.error:
        return PEOptionalHeader(magic="PE32+")
    return PEOptionalHeader(
        magic="PE32+",
        entry_point=entry_point,
        image_base=image_base,
        section_alignment=section_alignment,
        file_alignment=file_alignment,
        subsystem=SUBSYSTEM_TYPES.get(subsystem_raw, f"0x{subsystem_raw:04X}"),
        dll_characteristics=_decode_dll_characteristics(dll_char_raw),
    )


def _read_pe32_optional(data: bytes, opt_hdr: int) -> PEOptionalHeader:
    try:
        (
            entry_point,
            _base_of_code,
            image_base,
            section_alignment,
            file_alignment,
            _maj_os,
            _min_os,
            _maj_img,
            _min_img,
            _maj_subsys,
            _min_subsys,
            _reserved,
            _size_image,
            _size_headers,
            _checksum,
            subsystem_raw,
            dll_char_raw,
        ) = struct.unpack_from("<IIIIIHHHHHHIIIIHH", data, opt_hdr + 16)
    except struct.error:
        return PEOptionalHeader(magic="PE32")
    return PEOptionalHeader(
        magic="PE32",
        entry_point=entry_point,
        image_base=image_base,
        section_alignment=section_alignment,
        file_alignment=file_alignment,
        subsystem=SUBSYSTEM_TYPES.get(subsystem_raw, f"0x{subsystem_raw:04X}"),
        dll_characteristics=_decode_dll_characteristics(dll_char_raw),
    )


def _read_data_directories(data: bytes) -> list[PEDataDirectory]:
    dos = _read_dos_header(data)
    if dos is None:
        return []
    pe_result = _read_pe_header(data)
    if pe_result is None:
        return []
    _, opt_hdr = pe_result
    if opt_hdr.magic not in ("PE32", "PE32+"):
        return []
    pe_offset = dos.e_lfanew
    coff = pe_offset + 4
    opt_hdr_offset = coff + 20
    is_pe32plus = opt_hdr.magic == "PE32+"
    data_dir_start = opt_hdr_offset + 112 if is_pe32plus else opt_hdr_offset + 96
    num_entries = 16
    directories: list[PEDataDirectory] = []
    for i in range(num_entries):
        off = data_dir_start + (i * 8)
        if off + 8 > len(data):
            break
        try:
            rva, size = struct.unpack_from("<II", data, off)
        except struct.error:
            break
        name = DIRECTORY_NAMES.get(i, f"DIR_{i}")
        directories.append(PEDataDirectory(name=name, virtual_address=rva, size=size))
    return directories


def _rva_to_offset(sections: list[PESection], rva: int) -> int | None:
    if rva == 0:
        return None
    for sec in sections:
        if sec.virtual_address <= rva < sec.virtual_address + sec.virtual_size:
            return rva - sec.virtual_address + sec.raw_offset
    return None


def _read_pe_sections_full(data: bytes) -> list[PESection]:
    dos = _read_dos_header(data)
    if dos is None:
        return []
    pe_result = _read_pe_header(data)
    if pe_result is None:
        return []
    file_hdr, opt_hdr = pe_result
    pe_offset = dos.e_lfanew
    coff = pe_offset + 4
    opt_hdr_offset = coff + 20
    is_pe32plus = opt_hdr.magic == "PE32+"
    section_table = opt_hdr_offset + (112 if is_pe32plus else 96) + 128
    sections: list[PESection] = []
    for i in range(file_hdr.num_sections):
        hdr = section_table + (i * 40)
        if hdr + 40 > len(data):
            break
        try:
            name_bytes = data[hdr : hdr + 8]
            name = name_bytes.rstrip(b"\x00").decode("ascii", errors="ignore")
            (vsize, vaddr, raw_size, raw_offset, _reloc_ptr, _line_ptr, _reloc_count, _line_count, characteristics) = (
                struct.unpack_from("<IIIIIIHHI", data, hdr + 8)
            )
            entropy = 0.0
            if raw_offset > 0 and raw_size > 0 and raw_offset + raw_size <= len(data):
                entropy = _shannon_entropy(data[raw_offset : raw_offset + raw_size])
            sections.append(
                PESection(
                    name=name,
                    virtual_address=vaddr,
                    virtual_size=vsize,
                    raw_size=raw_size,
                    raw_offset=raw_offset,
                    characteristics=_decode_section_characteristics(characteristics),
                    entropy=entropy,
                )
            )
        except (struct.error, IndexError, ValueError):
            continue
    return sections


def _read_pe_imports(data: bytes) -> list[PEImport]:
    imports: list[PEImport] = []
    sections = _read_pe_sections_full(data)
    directories = _read_data_directories(data)
    for dd in directories:
        if dd.name not in ("IMPORT", "IAT"):
            continue
        offset = _rva_to_offset(sections, dd.virtual_address)
        if offset is None or offset + 20 > len(data):
            continue
        try:
            dll_count = 0
            while dll_count < 50:
                rva = offset + (dll_count * 20)
                if rva + 20 > len(data):
                    break
                (ilt_rva, _timestamp, _forwarder, name_rva, _iat_rva) = struct.unpack_from("<IIIII", data, rva)
                if name_rva == 0 and ilt_rva == 0:
                    break
                name_off = _rva_to_offset(sections, name_rva)
                dll_name = ""
                if name_off is not None and name_off < len(data):
                    end = data.find(b"\x00", name_off)
                    if end != -1:
                        dll_name = data[name_off:end].decode("ascii", errors="ignore")
                if dll_name:
                    imports.append(PEImport(dll_name=dll_name))
                dll_count += 1
        except (struct.error, IndexError, ValueError):
            continue
    return imports


def _read_pe_exports(data: bytes) -> list[PEExport]:
    exports: list[PEExport] = []
    sections = _read_pe_sections_full(data)
    directories = _read_data_directories(data)
    for dd in directories:
        if dd.name != "EXPORT":
            continue
        offset = _rva_to_offset(sections, dd.virtual_address)
        if offset is None or offset + 40 > len(data):
            continue
        try:
            num_names = struct.unpack_from("<I", data, offset + 24)[0]
            name_ptr_rva = struct.unpack_from("<I", data, offset + 32)[0]
            ordinal_base = struct.unpack_from("<I", data, offset + 16)[0]
            name_off = _rva_to_offset(sections, name_ptr_rva)
            if name_off is None:
                continue
            for i in range(min(num_names, 100)):
                ptr_off = name_off + (i * 4)
                if ptr_off + 4 > len(data):
                    break
                func_name_rva = struct.unpack_from("<I", data, ptr_off)[0]
                func_off = _rva_to_offset(sections, func_name_rva)
                func_name = ""
                if func_off is not None and func_off < len(data):
                    end = data.find(b"\x00", func_off)
                    if end != -1:
                        func_name = data[func_off:end].decode("ascii", errors="ignore")
                if func_name:
                    exports.append(
                        PEExport(
                            name=func_name,
                            ordinal=ordinal_base + i,
                            address=func_name_rva,
                        )
                    )
        except (struct.error, IndexError, ValueError):
            continue
    return exports


def parse_pe(
    data_or_path: str | Path | bytes | bytearray,
) -> PEAnalysisResult:
    if isinstance(data_or_path, bytearray):
        data = bytes(data_or_path)
    elif isinstance(data_or_path, bytes):
        data = data_or_path
    elif isinstance(data_or_path, (str, Path)):
        try:
            data = Path(data_or_path).read_bytes()
        except OSError:
            return PEAnalysisResult(dos_header=PEDOSHeader())
    else:
        return PEAnalysisResult(dos_header=PEDOSHeader())

    dos = _read_dos_header(data)
    if dos is None:
        return PEAnalysisResult(dos_header=PEDOSHeader())

    pe_result = _read_pe_header(data)
    if pe_result is None:
        return PEAnalysisResult(dos_header=dos)

    file_hdr, opt_hdr = pe_result
    directories = _read_data_directories(data)
    sections = _read_pe_sections_full(data)
    imports = _read_pe_imports(data)
    exports = _read_pe_exports(data)

    is_64bit = opt_hdr.magic == "PE32+"
    is_32bit = opt_hdr.magic == "PE32"
    is_dll = "DLL" in file_hdr.characteristics
    is_dotnet = any(dd.name == "COM_DESCRIPTOR" and dd.size > 0 for dd in directories)
    has_debug = any(dd.name == "DEBUG" and dd.size > 0 for dd in directories)

    return PEAnalysisResult(
        dos_header=dos,
        file_header=file_hdr,
        optional_header=opt_hdr,
        data_directories=directories,
        sections=sections,
        imports=imports,
        exports=exports,
        is_32bit=is_32bit,
        is_64bit=is_64bit,
        is_dll=is_dll,
        is_dotnet=is_dotnet,
        has_debug=has_debug,
    )
