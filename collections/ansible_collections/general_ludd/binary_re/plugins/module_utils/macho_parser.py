"""Mach-O (macOS/iOS) binary parser.

Parses Mach-O headers (32/64-bit, fat binaries), load commands, segments,
sections, and extracts architecture info.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

MH_MAGIC = 0xFEEDFACE
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM = 0xCEFAEDFE
MH_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA

CPU_TYPE_NAMES: dict[int, str] = {
    0x00000001: "VAX",
    0x00000006: "MC680x0",
    0x00000007: "x86",
    0x0000000C: "ARM",
    0x01000007: "x86_64",
    0x0100000C: "ARM64",
    0x00000012: "PowerPC",
    0x01000012: "PowerPC64",
}

FILE_TYPE_NAMES: dict[int, str] = {
    1: "MH_OBJECT",
    2: "MH_EXECUTE",
    3: "MH_FVMLIB",
    4: "MH_CORE",
    5: "MH_PRELOAD",
    6: "MH_DYLIB",
    7: "MH_DYLINKER",
    8: "MH_BUNDLE",
    9: "MH_DYLIB_STUB",
    10: "MH_DSYM",
    11: "MH_KEXT_BUNDLE",
}

FLAG_NAMES: dict[int, str] = {
    0x1: "MH_NOUNDEFS",
    0x2: "MH_INCRLINK",
    0x4: "MH_DYLDLINK",
    0x8: "MH_BINDATLOAD",
    0x10: "MH_PREBOUND",
    0x20: "MH_SPLIT_SEGS",
    0x40: "MH_LAZY_INIT",
    0x80: "MH_TWOLEVEL",
    0x100: "MH_FORCE_FLAT",
    0x200: "MH_NOMULTIDEFS",
    0x400: "MH_NOFIXPREBINDING",
    0x800: "MH_PREBINDABLE",
    0x1000: "MH_ALLMODSBOUND",
    0x2000: "MH_SUBSECTIONS_VIA_SYMBOLS",
    0x4000: "MH_CANONICAL",
    0x8000: "MH_WEAK_DEFINES",
    0x10000: "MH_BINDS_TO_WEAK",
    0x20000: "MH_ALLOW_STACK_EXECUTION",
    0x40000: "MH_ROOT_SAFE",
    0x80000: "MH_SETUID_SAFE",
    0x100000: "MH_NO_REEXPORTED_DYLIBS",
    0x200000: "MH_PIE",
    0x400000: "MH_DEAD_STRIPPABLE_DYLIB",
    0x800000: "MH_HAS_TLV_DESCRIPTORS",
    0x1000000: "MH_NO_HEAP_EXECUTION",
    0x2000000: "MH_APP_EXTENSION_SAFE",
    0x4000000: "MH_NLIST_OUTOFSYNC_WITH_DYLDINFO",
    0x8000000: "MH_SIM_SUPPORT",
    0x20000000: "MH_DYLIB_IN_CACHE",
}

LC_SEGMENT = 0x1
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_DYSYMTAB = 0xB
LC_LOAD_DYLIB = 0xC
LC_ID_DYLIB = 0xD
LC_LOAD_DYLINKER = 0xE
LC_UUID = 0x1B
LC_BUILD_VERSION = 0x32
LC_DYLD_INFO = 0x22
LC_DYLD_INFO_ONLY = 0x80000022
LC_MAIN = 0x80000028

LC_NAMES: dict[int, str] = {
    LC_SEGMENT: "LC_SEGMENT",
    LC_SEGMENT_64: "LC_SEGMENT_64",
    LC_SYMTAB: "LC_SYMTAB",
    LC_DYSYMTAB: "LC_DYSYMTAB",
    LC_LOAD_DYLIB: "LC_LOAD_DYLIB",
    LC_ID_DYLIB: "LC_ID_DYLIB",
    LC_LOAD_DYLINKER: "LC_LOAD_DYLINKER",
    LC_UUID: "LC_UUID",
    LC_BUILD_VERSION: "LC_BUILD_VERSION",
    LC_DYLD_INFO: "LC_DYLD_INFO",
    LC_DYLD_INFO_ONLY: "LC_DYLD_INFO_ONLY",
    LC_MAIN: "LC_MAIN",
}


@dataclass
class MachOHeader:
    magic: str = "unknown"
    cputype: str = "unknown"
    cpusubtype: str = "unknown"
    filetype: str = "unknown"
    ncmds: int = 0
    sizeofcmds: int = 0
    flags: list[str] = field(default_factory=list)


@dataclass
class MachOLoadCommand:
    cmd: str
    cmdsize: int = 0
    data: dict = field(default_factory=dict)


@dataclass
class MachOSection:
    name: str = ""
    segname: str = ""
    addr: int = 0
    size: int = 0
    offset: int = 0
    align: int = 1


@dataclass
class MachOSegment:
    name: str = ""
    vmaddr: int = 0
    vmsize: int = 0
    fileoff: int = 0
    filesize: int = 0
    maxprot: str = ""
    initprot: str = ""
    nsects: int = 0
    sections: list[MachOSection] = field(default_factory=list)


@dataclass
class MachOAnalysisResult:
    header: MachOHeader
    commands: list[MachOLoadCommand] = field(default_factory=list)
    segments: list[MachOSegment] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    is_fat: bool = False
    architectures: list[str] = field(default_factory=list)


def _magic_name(magic: int) -> str:
    names = {
        MH_MAGIC: "MH_MAGIC",
        MH_MAGIC_64: "MH_MAGIC_64",
        MH_CIGAM: "MH_CIGAM",
        MH_CIGAM_64: "MH_CIGAM_64",
    }
    return names.get(magic, f"MH_{magic:#x}")


def _read_macho_header(data: bytes) -> MachOHeader | None:
    if len(data) < 28:
        return None
    magic_raw = struct.unpack_from("<I", data, 0)[0]
    if magic_raw not in (MH_MAGIC, MH_MAGIC_64, MH_CIGAM, MH_CIGAM_64):
        return None
    is_64 = magic_raw in (MH_MAGIC_64, MH_CIGAM_64)
    is_le = magic_raw in (MH_MAGIC, MH_MAGIC_64)
    endian = "<" if is_le else ">"
    if is_64:
        if len(data) < 32:
            return None
        (
            _magic64,
            cputype,
            cpusubtype,
            filetype,
            ncmds,
            sizeofcmds,
            flags,
            _reserved,
        ) = struct.unpack_from(f"{endian}IIIIIIII", data, 0)
    else:
        (
            _magic32,
            cputype,
            cpusubtype,
            filetype,
            ncmds,
            sizeofcmds,
            flags,
        ) = struct.unpack_from(f"{endian}IIIIIII", data, 0)
    return MachOHeader(
        magic=_magic_name(magic_raw),
        cputype=CPU_TYPE_NAMES.get(cputype, f"CPU_TYPE_{cputype:#x}"),
        cpusubtype=f"{cpusubtype:#x}",
        filetype=FILE_TYPE_NAMES.get(filetype, f"MH_{filetype:#x}"),
        ncmds=ncmds,
        sizeofcmds=sizeofcmds,
        flags=_decode_flags(flags),
    )


def _decode_flags(flags: int) -> list[str]:
    result: list[str] = []
    for val, name in sorted(FLAG_NAMES.items()):
        if flags & val:
            result.append(name)
    return result


def _parse_fat_archs(data: bytes) -> list[str]:
    archs: list[str] = []
    try:
        if data[:4] in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            pass
        else:
            return archs
        narch = struct.unpack_from(">I", data, 4)[0]
        for i in range(narch):
            off = 8 + (i * 20)
            if off + 20 > len(data):
                break
            cputype, _cpusubtype = struct.unpack_from(">II", data, off)
            name = CPU_TYPE_NAMES.get(cputype, f"CPU_TYPE_{cputype:#x}")
            archs.append(name)
    except (struct.error, IndexError):
        pass
    return archs


def _read_load_commands(data: bytes, hdr_data: bytes) -> list[MachOLoadCommand]:
    cmds: list[MachOLoadCommand] = []
    try:
        hdr = _read_macho_header(hdr_data)
        if hdr is None:
            return cmds
        is_64 = hdr.magic in ("MH_MAGIC_64", "MH_CIGAM_64")
        header_size = 32 if is_64 else 28
        offset = header_size
        endian = "<" if hdr.magic in ("MH_MAGIC", "MH_MAGIC_64") else ">"
        for _ in range(hdr.ncmds):
            if offset + 8 > len(data):
                break
            cmd_raw, cmdsize = struct.unpack_from(f"{endian}II", data, offset)
            cmd_name = LC_NAMES.get(cmd_raw, f"LC_{cmd_raw:#x}")
            cmd_data: dict = {}
            cmds.append(MachOLoadCommand(cmd=cmd_name, cmdsize=cmdsize, data=cmd_data))
            offset += cmdsize
    except (struct.error, IndexError, ValueError):
        pass
    return cmds


def _read_segments(data: bytes, hdr_data: bytes) -> list[MachOSegment]:
    segments: list[MachOSegment] = []
    try:
        hdr = _read_macho_header(hdr_data)
        if hdr is None:
            return segments
        is_64 = hdr.magic in ("MH_MAGIC_64", "MH_CIGAM_64")
        header_size = 32 if is_64 else 28
        endian = "<" if hdr.magic in ("MH_MAGIC", "MH_MAGIC_64") else ">"
        offset = header_size
        for _ in range(hdr.ncmds):
            if offset + 8 > len(data):
                break
            cmd_raw, cmdsize = struct.unpack_from(f"{endian}II", data, offset)
            if cmd_raw in (LC_SEGMENT, LC_SEGMENT_64):
                seg = _read_segment64(data, offset, endian) if is_64 else _read_segment32(data, offset, endian)
                if seg is not None:
                    segments.append(seg)
            offset += cmdsize
    except (struct.error, IndexError, ValueError):
        pass
    return segments


def _read_segment64(data: bytes, base_off: int, endian: str) -> MachOSegment | None:
    try:
        name_bytes = data[base_off + 8 : base_off + 24]
        name = name_bytes.rstrip(b"\x00").decode("ascii", errors="ignore")
        (
            vmaddr,
            vmsize,
            fileoff,
            filesize,
            maxprot,
            initprot,
            nsects,
            _flags,
        ) = struct.unpack_from(f"{endian}QQQQIIII", data, base_off + 24)
        sections: list[MachOSection] = []
        sec_off = base_off + 72
        for _ in range(nsects):
            if sec_off + 80 > len(data):
                break
            (
                sectname_bytes,
                segname_bytes,
                addr,
                size,
                sec_offset,
                align,
                _reloff,
                _nreloc,
                _flags,
                _res1,
                _res2,
                _res3,
            ) = struct.unpack_from(f"{endian}16s16sQQIIIIIIII", data, sec_off)
            sname = sectname_bytes.rstrip(b"\x00").decode("ascii", errors="ignore")
            ssegname = segname_bytes.rstrip(b"\x00").decode("ascii", errors="ignore")
            sections.append(
                MachOSection(
                    name=sname,
                    segname=ssegname,
                    addr=addr,
                    size=size,
                    offset=sec_offset,
                    align=align,
                )
            )
            sec_off += 80
        return MachOSegment(
            name=name,
            vmaddr=vmaddr,
            vmsize=vmsize,
            fileoff=fileoff,
            filesize=filesize,
            maxprot=_prot_string(maxprot),
            initprot=_prot_string(initprot),
            nsects=nsects,
            sections=sections,
        )
    except (struct.error, IndexError, ValueError):
        return None


def _read_segment32(data: bytes, base_off: int, endian: str) -> MachOSegment | None:
    try:
        name_bytes = data[base_off + 8 : base_off + 24]
        name = name_bytes.rstrip(b"\x00").decode("ascii", errors="ignore")
        (
            vmaddr,
            vmsize,
            fileoff,
            filesize,
            maxprot,
            initprot,
            nsects,
            _flags,
        ) = struct.unpack_from(f"{endian}IIIIIIII", data, base_off + 24)
        return MachOSegment(
            name=name,
            vmaddr=vmaddr,
            vmsize=vmsize,
            fileoff=fileoff,
            filesize=filesize,
            maxprot=_prot_string(maxprot),
            initprot=_prot_string(initprot),
            nsects=nsects,
            sections=[],
        )
    except (struct.error, IndexError, ValueError):
        return None


def _prot_string(prot: int) -> str:
    parts: list[str] = []
    if prot & 1:
        parts.append("VM_PROT_READ")
    if prot & 2:
        parts.append("VM_PROT_WRITE")
    if prot & 4:
        parts.append("VM_PROT_EXECUTE")
    return "|".join(parts) if parts else "VM_PROT_NONE"


def parse_macho(
    data_or_path: str | Path | bytes | bytearray,
) -> MachOAnalysisResult:
    if isinstance(data_or_path, bytearray):
        data = bytes(data_or_path)
    elif isinstance(data_or_path, bytes):
        data = data_or_path
    elif isinstance(data_or_path, (str, Path)):
        try:
            data = Path(data_or_path).read_bytes()
        except OSError:
            return MachOAnalysisResult(header=MachOHeader())
    else:
        return MachOAnalysisResult(header=MachOHeader())

    archs = _parse_fat_archs(data)
    if archs:
        return MachOAnalysisResult(
            header=MachOHeader(),
            is_fat=True,
            architectures=archs,
        )

    hdr = _read_macho_header(data)
    if hdr is None:
        return MachOAnalysisResult(header=MachOHeader())

    segments = _read_segments(data, data)
    commands = _read_load_commands(data, data)

    return MachOAnalysisResult(
        header=hdr,
        commands=commands,
        segments=segments,
        is_fat=False,
    )
