"""ELF (Executable and Linkable Format) binary parser.

Parses ELF32/ELF64 headers, section headers, program headers, symbol tables,
dynamic entries, and extracts dependencies/imports/exports.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

ELF_MAGIC = b"\x7fELF"

EI_CLASS_32 = 1
EI_CLASS_64 = 2
EI_DATA_LE = 1
EI_DATA_BE = 2

ET_NONE = 0
ET_REL = 1
ET_EXEC = 2
ET_DYN = 3
ET_CORE = 4

ET_NAMES: dict[int, str] = {
    ET_NONE: "ET_NONE",
    ET_REL: "ET_REL",
    ET_EXEC: "ET_EXEC",
    ET_DYN: "ET_DYN",
    ET_CORE: "ET_CORE",
}

MACHINE_NAMES: dict[int, str] = {
    0: "EM_NONE",
    3: "i386",
    8: "MIPS",
    0x28: "ARM",
    0x3E: "x86_64",
    0xB7: "AArch64",
    0xF3: "RISC-V",
}

SHT_NULL = 0
SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_RELA = 4
SHT_HASH = 5
SHT_DYNAMIC = 6
SHT_NOTE = 7
SHT_NOBITS = 8
SHT_REL = 9
SHT_DYNSYM = 11

SHT_NAMES: dict[int, str] = {
    SHT_NULL: "SHT_NULL",
    SHT_PROGBITS: "SHT_PROGBITS",
    SHT_SYMTAB: "SHT_SYMTAB",
    SHT_STRTAB: "SHT_STRTAB",
    SHT_RELA: "SHT_RELA",
    SHT_HASH: "SHT_HASH",
    SHT_DYNAMIC: "SHT_DYNAMIC",
    SHT_NOTE: "SHT_NOTE",
    SHT_NOBITS: "SHT_NOBITS",
    SHT_REL: "SHT_REL",
    SHT_DYNSYM: "SHT_DYNSYM",
}

SHF_WRITE = 1
SHF_ALLOC = 2
SHF_EXECINSTR = 4
SHF_MERGE = 0x10
SHF_STRINGS = 0x20
SHF_INFO_LINK = 0x40
SHF_LINK_ORDER = 0x80

PT_NULL = 0
PT_LOAD = 1
PT_DYNAMIC = 2
PT_INTERP = 3
PT_NOTE = 4
PT_PHDR = 6

PT_NAMES: dict[int, str] = {
    PT_NULL: "PT_NULL",
    PT_LOAD: "PT_LOAD",
    PT_DYNAMIC: "PT_DYNAMIC",
    PT_INTERP: "PT_INTERP",
    PT_NOTE: "PT_NOTE",
    PT_PHDR: "PT_PHDR",
}

PF_R = 4
PF_W = 2
PF_X = 1

STB_LOCAL = 0
STB_GLOBAL = 1
STB_WEAK = 2

STB_NAMES: dict[int, str] = {
    STB_LOCAL: "STB_LOCAL",
    STB_GLOBAL: "STB_GLOBAL",
    STB_WEAK: "STB_WEAK",
}

STT_NOTYPE = 0
STT_OBJECT = 1
STT_FUNC = 2
STT_SECTION = 3
STT_FILE = 4

STT_NAMES: dict[int, str] = {
    STT_NOTYPE: "STT_NOTYPE",
    STT_OBJECT: "STT_OBJECT",
    STT_FUNC: "STT_FUNC",
    STT_SECTION: "STT_SECTION",
    STT_FILE: "STT_FILE",
}

DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
DT_SYMTAB = 6
DT_SONAME = 14


@dataclass
class ELFHeader:
    ei_class: int = 0
    ei_data: int = 0
    ei_version: int = 0
    ei_osabi: int = 0
    e_type: str = "unknown"
    e_machine: str = "unknown"
    e_entry: int = 0
    e_phoff: int = 0
    e_shoff: int = 0
    e_flags: int = 0
    e_ehsize: int = 0
    e_phentsize: int = 0
    e_phnum: int = 0
    e_shentsize: int = 0
    e_shnum: int = 0
    e_shstrndx: int = 0


@dataclass
class ELFSectionHeader:
    name: str
    type: str
    flags: list[str] = field(default_factory=list)
    addr: int = 0
    offset: int = 0
    size: int = 0
    link: int = 0
    info: int = 0
    addralign: int = 1
    entsize: int = 0


@dataclass
class ELFProgramHeader:
    type: str
    flags: list[str] = field(default_factory=list)
    offset: int = 0
    vaddr: int = 0
    paddr: int = 0
    filesz: int = 0
    memsz: int = 0
    align: int = 0


@dataclass
class ELFSymbol:
    name: str
    value: int = 0
    size: int = 0
    type: str = "STT_NOTYPE"
    bind: str = "STB_LOCAL"
    section: str = ""


@dataclass
class ELFDynamicEntry:
    tag: str
    value: int = 0


@dataclass
class ELFAnalysisResult:
    header: ELFHeader
    sections: list[ELFSectionHeader] = field(default_factory=list)
    segments: list[ELFProgramHeader] = field(default_factory=list)
    symbols: list[ELFSymbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    is_pie: bool = False
    is_stripped: bool = False
    is_statically_linked: bool = False


def _read_elf_header(data: bytes) -> ELFHeader | None:
    if len(data) < 64:
        return None
    if data[:4] != ELF_MAGIC:
        return None
    ei_class = data[4]
    ei_data = data[5]
    ei_version = data[6]
    ei_osabi = data[7]
    endian = "<" if ei_data == EI_DATA_LE else ">"
    bits = 64 if ei_class == EI_CLASS_64 else 32
    if bits == 64:
        if len(data) < 64:
            return None
        fmt = f"{endian}HHIQQQIHHHHHH"
        (
            e_type,
            e_machine,
            _e_version,
            e_entry,
            e_phoff,
            e_shoff,
            e_flags,
            e_ehsize,
            e_phentsize,
            e_phnum,
            e_shentsize,
            e_shnum,
            e_shstrndx,
        ) = struct.unpack_from(fmt, data, 16)
    else:
        if len(data) < 52:
            return None
        fmt = f"{endian}HHIIIIIHHHHHH"
        (
            e_type,
            e_machine,
            _e_version,
            e_entry,
            e_phoff,
            e_shoff,
            e_flags,
            e_ehsize,
            e_phentsize,
            e_phnum,
            e_shentsize,
            e_shnum,
            e_shstrndx,
        ) = struct.unpack_from(fmt, data, 16)
    return ELFHeader(
        ei_class=ei_class,
        ei_data=ei_data,
        ei_version=ei_version,
        ei_osabi=ei_osabi,
        e_type=ET_NAMES.get(e_type, f"ET_{e_type:#x}"),
        e_machine=MACHINE_NAMES.get(e_machine, f"EM_{e_machine:#x}"),
        e_entry=e_entry,
        e_phoff=e_phoff,
        e_shoff=e_shoff,
        e_flags=e_flags,
        e_ehsize=e_ehsize,
        e_phentsize=e_phentsize,
        e_phnum=e_phnum,
        e_shentsize=e_shentsize,
        e_shnum=e_shnum,
        e_shstrndx=e_shstrndx,
    )


def _decode_section_flags(flags: int) -> list[str]:
    result: list[str] = []
    if flags & SHF_WRITE:
        result.append("SHF_WRITE")
    if flags & SHF_ALLOC:
        result.append("SHF_ALLOC")
    if flags & SHF_EXECINSTR:
        result.append("SHF_EXECINSTR")
    return result


def _decode_phdr_flags(flags: int) -> list[str]:
    result: list[str] = []
    if flags & PF_R:
        result.append("PF_R")
    if flags & PF_W:
        result.append("PF_W")
    if flags & PF_X:
        result.append("PF_X")
    return result


def parse_elf_sections(data: bytes) -> list[ELFSectionHeader]:
    hdr = _read_elf_header(data)
    if hdr is None or hdr.e_shoff == 0 or hdr.e_shnum == 0:
        return []
    endian = "<" if hdr.ei_data == EI_DATA_LE else ">"
    bits = 64 if hdr.ei_class == EI_CLASS_64 else 32
    shoff = hdr.e_shoff
    shentsize = hdr.e_shentsize
    shnum = hdr.e_shnum
    shstrndx = hdr.e_shstrndx
    if shstrndx >= shnum:
        return []
    strtab_hdr_off = shoff + (shstrndx * shentsize)
    strtab = b""
    try:
        if bits == 64:
            if strtab_hdr_off + 64 > len(data):
                return []
            (
                sh_name,
                sh_type,
                sh_flags,
                sh_addr,
                sh_offset,
                sh_size,
                sh_link,
                sh_info,
                sh_addralign,
                sh_entsize,
            ) = struct.unpack_from(f"{endian}IIQQQQIIQQ", data, strtab_hdr_off)
        else:
            if strtab_hdr_off + 40 > len(data):
                return []
            (
                sh_name,
                sh_type,
                sh_flags,
                sh_addr,
                sh_offset,
                sh_size,
                sh_link,
                sh_info,
                sh_addralign,
                sh_entsize,
            ) = struct.unpack_from(f"{endian}IIIIIIIIII", data, strtab_hdr_off)
        if sh_type != SHT_STRTAB or sh_offset + sh_size > len(data):
            return []
        strtab = data[sh_offset : sh_offset + sh_size]
    except (struct.error, IndexError, ValueError):
        return []
    sections: list[ELFSectionHeader] = []
    for i in range(shnum):
        sec_off = shoff + (i * shentsize)
        if sec_off + shentsize > len(data):
            break
        try:
            if bits == 64:
                (
                    sh_name,
                    sh_type,
                    sh_flags,
                    sh_addr,
                    sh_offset,
                    sh_size,
                    sh_link,
                    sh_info,
                    sh_addralign,
                    sh_entsize,
                ) = struct.unpack_from(f"{endian}IIQQQQIIQQ", data, sec_off)
            else:
                (
                    sh_name,
                    sh_type,
                    sh_flags,
                    sh_addr,
                    sh_offset,
                    sh_size,
                    sh_link,
                    sh_info,
                    sh_addralign,
                    sh_entsize,
                ) = struct.unpack_from(f"{endian}IIIIIIIIII", data, sec_off)
            if sh_type == SHT_NOBITS:
                continue
            name_end = strtab.find(b"\x00", sh_name)
            name = strtab[sh_name:name_end].decode("ascii", errors="ignore") if name_end != -1 else ""
            sections.append(
                ELFSectionHeader(
                    name=name,
                    type=SHT_NAMES.get(sh_type, f"SHT_{sh_type:#x}"),
                    flags=_decode_section_flags(sh_flags),
                    addr=sh_addr,
                    offset=sh_offset,
                    size=sh_size,
                    link=sh_link,
                    info=sh_info,
                    addralign=sh_addralign,
                    entsize=sh_entsize,
                )
            )
        except (struct.error, IndexError, ValueError):
            continue
    return sections


def _parse_phdrs(data: bytes, hdr: ELFHeader) -> list[ELFProgramHeader]:
    if hdr.e_phoff == 0 or hdr.e_phnum == 0 or hdr.e_phentsize == 0:
        return []
    endian = "<" if hdr.ei_data == EI_DATA_LE else ">"
    bits = 64 if hdr.ei_class == EI_CLASS_64 else 32
    segments: list[ELFProgramHeader] = []
    for i in range(hdr.e_phnum):
        off = hdr.e_phoff + (i * hdr.e_phentsize)
        try:
            if bits == 64:
                if off + 56 > len(data):
                    break
                (
                    p_type,
                    p_flags,
                    p_offset,
                    p_vaddr,
                    p_paddr,
                    p_filesz,
                    p_memsz,
                    p_align,
                ) = struct.unpack_from(f"{endian}IIQQQQQQ", data, off)
            else:
                if off + 32 > len(data):
                    break
                (
                    p_type,
                    p_offset,
                    p_vaddr,
                    p_paddr,
                    p_filesz,
                    p_memsz,
                    p_flags,
                    p_align,
                ) = struct.unpack_from(f"{endian}IIIIIIII", data, off)
            segments.append(
                ELFProgramHeader(
                    type=PT_NAMES.get(p_type, f"PT_{p_type:#x}"),
                    flags=_decode_phdr_flags(p_flags),
                    offset=p_offset,
                    vaddr=p_vaddr,
                    paddr=p_paddr,
                    filesz=p_filesz,
                    memsz=p_memsz,
                    align=p_align,
                )
            )
        except (struct.error, IndexError, ValueError):
            continue
    return segments


def parse_elf_symbols(data: bytes) -> list[ELFSymbol]:
    hdr = _read_elf_header(data)
    if hdr is None:
        return []
    sections = parse_elf_sections(data)
    symtab = None
    strtab_data = b""
    for sec in sections:
        if sec.type in ("SHT_SYMTAB", "SHT_DYNSYM") and sec.size > 0:
            symtab = sec
    for sec in sections:
        if sec.type == "SHT_STRTAB":
            sym_linked = symtab.link if symtab else 0
            if symtab and _section_index(sections, sec) == sym_linked:
                strtab_data = data[sec.offset : sec.offset + sec.size]
                break
    if symtab is None:
        return []
    endian = "<" if hdr.ei_data == EI_DATA_LE else ">"
    bits = 64 if hdr.ei_class == EI_CLASS_64 else 32
    entsize = 24 if bits == 64 else 16
    symbols: list[ELFSymbol] = []
    for i in range(symtab.size // entsize):
        off = symtab.offset + (i * entsize)
        try:
            if bits == 64:
                (
                    st_name,
                    st_info,
                    _st_other64,
                    st_shndx,
                    st_value,
                    st_size,
                ) = struct.unpack_from(f"{endian}IBBHQQ", data, off)
            else:
                (
                    st_name,
                    st_value,
                    st_size,
                    st_info,
                    _st_other,
                    st_shndx,
                ) = struct.unpack_from(f"{endian}IIIBBH", data, off)
            bind = st_info >> 4
            sym_type = st_info & 0x0F
            end = strtab_data.find(b"\x00", st_name) if strtab_data else -1
            name = strtab_data[st_name:end].decode("ascii", errors="ignore") if end != -1 else ""
            symbols.append(
                ELFSymbol(
                    name=name,
                    value=st_value,
                    size=st_size,
                    type=STT_NAMES.get(sym_type, f"STT_{sym_type:#x}"),
                    bind=STB_NAMES.get(bind, f"STB_{bind:#x}"),
                    section=str(st_shndx),
                )
            )
        except (struct.error, IndexError, ValueError):
            continue
    return symbols


def _section_index(sections: list[ELFSectionHeader], target: ELFSectionHeader) -> int:
    for i, s in enumerate(sections):
        if s is target:
            return i
    return -1


def parse_elf_dynamic(data: bytes) -> list[ELFDynamicEntry]:
    hdr = _read_elf_header(data)
    if hdr is None:
        return []
    sections = parse_elf_sections(data)
    dynamic_sec = None
    for sec in sections:
        if sec.type == "SHT_DYNAMIC":
            dynamic_sec = sec
    if dynamic_sec is None:
        return []
    endian = "<" if hdr.ei_data == EI_DATA_LE else ">"
    bits = 64 if hdr.ei_class == EI_CLASS_64 else 32
    entry_size = 16 if bits == 64 else 8
    entries: list[ELFDynamicEntry] = []
    for i in range(dynamic_sec.size // entry_size):
        off = dynamic_sec.offset + (i * entry_size)
        try:
            if bits == 64:
                d_tag, d_val = struct.unpack_from(f"{endian}qQ", data, off)
            else:
                d_tag, d_val = struct.unpack_from(f"{endian}iI", data, off)
            tag_name = _dt_tag_name(d_tag)
            entries.append(ELFDynamicEntry(tag=tag_name, value=d_val))
            if d_tag == DT_NULL:
                break
        except (struct.error, IndexError, ValueError):
            continue
    return entries


def _dt_tag_name(tag: int) -> str:
    known: dict[int, str] = {
        DT_NULL: "DT_NULL",
        DT_NEEDED: "DT_NEEDED",
        2: "DT_PLTRELSZ",
        3: "DT_PLTGOT",
        4: "DT_HASH",
        DT_STRTAB: "DT_STRTAB",
        DT_SYMTAB: "DT_SYMTAB",
        7: "DT_RELA",
        8: "DT_RELASZ",
        9: "DT_RELAENT",
        10: "DT_STRSZ",
        11: "DT_SYMENT",
        12: "DT_INIT",
        13: "DT_FINI",
        DT_SONAME: "DT_SONAME",
        15: "DT_RPATH",
        16: "DT_SYMBOLIC",
        17: "DT_REL",
        18: "DT_RELSZ",
        19: "DT_RELENT",
        20: "DT_PLTREL",
        21: "DT_DEBUG",
        22: "DT_TEXTREL",
        23: "DT_JMPREL",
        24: "DT_BIND_NOW",
        25: "DT_INIT_ARRAY",
        26: "DT_FINI_ARRAY",
        27: "DT_INIT_ARRAYSZ",
        28: "DT_FINI_ARRAYSZ",
        32: "DT_PREINIT_ARRAY",
        33: "DT_PREINIT_ARRAYSZ",
    }
    return known.get(tag, f"DT_{tag:#x}")


def parse_elf(
    data_or_path: str | Path | bytes | bytearray,
) -> ELFAnalysisResult:
    if isinstance(data_or_path, bytearray):
        data = bytes(data_or_path)
    elif isinstance(data_or_path, bytes):
        data = data_or_path
    elif isinstance(data_or_path, (str, Path)):
        try:
            data = Path(data_or_path).read_bytes()
        except OSError:
            return ELFAnalysisResult(header=ELFHeader())
    else:
        return ELFAnalysisResult(header=ELFHeader())

    hdr = _read_elf_header(data)
    if hdr is None:
        return ELFAnalysisResult(header=ELFHeader())

    sections = parse_elf_sections(data)
    segments = _parse_phdrs(data, hdr)
    symbols = parse_elf_symbols(data)
    dynamic = parse_elf_dynamic(data)

    dependencies: list[str] = []
    for entry in dynamic:
        if entry.tag == "DT_NEEDED":
            dependencies.append(f"lib_{entry.value}")

    imports: list[str] = []
    for sym in symbols:
        if sym.bind == "STB_GLOBAL" and sym.value == 0 and sym.name:
            imports.append(sym.name)

    exports: list[str] = []
    for sym in symbols:
        if sym.bind == "STB_GLOBAL" and sym.value != 0 and sym.name:
            exports.append(sym.name)

    is_pie = hdr.e_type == "ET_DYN"
    is_stripped = len(sections) == 0 and hdr.e_shoff == 0
    is_statically_linked = len(dynamic) == 0
    if sections:
        has_dynamic_sec = any(s.type == "SHT_DYNAMIC" for s in sections)
        if has_dynamic_sec:
            is_statically_linked = False

    return ELFAnalysisResult(
        header=hdr,
        sections=sections,
        segments=segments,
        symbols=symbols,
        imports=imports,
        exports=exports,
        dependencies=dependencies,
        is_pie=is_pie,
        is_stripped=is_stripped,
        is_statically_linked=is_statically_linked,
    )
