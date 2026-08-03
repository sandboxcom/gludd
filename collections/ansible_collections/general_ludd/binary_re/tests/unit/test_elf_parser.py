"""Tests for elf_parser module — ELF binary header/section/segment/symbol parsing."""

from __future__ import annotations

import struct

from plugins.module_utils.elf_parser import (
    EI_CLASS_32,
    EI_CLASS_64,
    EI_DATA_BE,
    EI_DATA_LE,
    ELF_MAGIC,
    ET_DYN,
    ET_EXEC,
    ET_REL,
    MACHINE_NAMES,
    ELFAnalysisResult,
    _read_elf_header,
    parse_elf,
    parse_elf_dynamic,
    parse_elf_sections,
    parse_elf_symbols,
)


def _make_minimal_elf64(sections: bool = True) -> bytes:
    buf = bytearray()
    buf.extend(b"\x7fELF")
    buf.append(2)  # ei_class = 64-bit
    buf.append(1)  # ei_data = little-endian
    buf.append(1)  # ei_version (e_ident)
    buf.append(0)  # ei_osabi
    buf.append(0)  # ei_abiversion
    buf.extend(b"\x00" * 7)  # padding
    p64 = struct.Struct("<Q")
    buf.extend(struct.pack("<HHI", 2, 0x3E, 1))  # ET_EXEC, x86_64, ev=1
    buf.extend(p64.pack(0x400000))  # e_entry
    buf.extend(p64.pack(0))  # e_phoff
    buf.extend(p64.pack(0))  # e_shoff
    buf.extend(struct.pack("<I", 0))  # e_flags
    buf.extend(struct.pack("<H", 64))  # e_ehsize
    buf.extend(struct.pack("<H", 0))  # e_phentsize
    buf.extend(struct.pack("<H", 0))  # e_phnum
    buf.extend(struct.pack("<H", 0))  # e_shentsize
    buf.extend(struct.pack("<H", 0))  # e_shnum
    buf.extend(struct.pack("<H", 0))  # e_shstrndx
    return bytes(buf)


def _make_elf32_sections(data_offset: int = 0x100, num_sections: int = 2) -> bytes:
    buf = bytearray()
    buf.extend(b"\x7fELF")
    buf.append(1)  # ei_class = 32-bit
    buf.append(1)  # ei_data = little-endian
    buf.append(1)  # ei_version (e_ident)
    buf.append(0)  # ei_osabi
    buf.append(0)  # ei_abiversion
    buf.extend(b"\x00" * 7)  # padding
    buf.extend(struct.pack("<HHI", 2, 0x03, 1))  # ET_EXEC, i386, ev=1
    buf.extend(struct.pack("<I", 0x08048000))  # e_entry
    buf.extend(struct.pack("<I", 0))  # e_phoff
    shoff_marker = len(buf)
    buf.extend(struct.pack("<I", 0))  # shoff placeholder
    buf.extend(struct.pack("<I", 0))  # e_flags
    buf.extend(struct.pack("<H", 52))  # e_ehsize
    buf.extend(struct.pack("<H", 0))  # e_phentsize
    buf.extend(struct.pack("<H", 0))  # e_phnum
    buf.extend(struct.pack("<H", 40))  # e_shentsize
    buf.extend(struct.pack("<H", num_sections))  # e_shnum
    buf.extend(struct.pack("<H", num_sections - 1))  # e_shstrndx
    sec_headers_start = data_offset
    strtab_data = b"\x00.shstrtab\x00.text\x00"
    strtab_offset = sec_headers_start + (num_sections * 40)
    struct.pack_into("<I", buf, shoff_marker, sec_headers_start)
    padding = sec_headers_start - len(buf)
    buf.extend(b"\x00" * padding)
    assert len(buf) == sec_headers_start
    for i in range(num_sections):
        name_offset_in_strtab = 0 if i == 0 else (1 if i == 1 else 11)
        sec_type = 3 if i == num_sections - 1 else 1
        flags = 0
        addr = 0
        sec_offset = 0
        sec_size = len(strtab_data) if i == num_sections - 1 else 0
        buf.extend(
            struct.pack(
                "<IIIIIIIIII",
                name_offset_in_strtab,
                sec_type,
                flags,
                addr,
                sec_offset if i != num_sections - 1 else strtab_offset,
                sec_size,
                0,
                0,
                0,
                0,
            )
        )
    buf.extend(strtab_data)
    return bytes(buf)


def _make_elf_with_phdr() -> bytes:
    """Minimal ELF64 with one PT_LOAD program header."""
    data = bytearray()
    data.extend(b"\x7fELF")
    data.append(2)  # 64-bit
    data.append(1)  # LE
    data.append(1)  # version (e_ident)
    data.append(0)  # osabi
    data.append(0)  # abiversion
    data.extend(b"\x00" * 7)  # padding
    data.extend(struct.pack("<HHI", 2, 0x3E, 1))  # ET_EXEC, x86_64
    data.extend(struct.pack("<Q", 0x400000))  # entry
    phoff = 64
    data.extend(struct.pack("<Q", phoff))  # e_phoff
    data.extend(struct.pack("<Q", 0))  # e_shoff
    data.extend(struct.pack("<I", 0))  # e_flags
    data.extend(struct.pack("<H", 64))  # e_ehsize
    data.extend(struct.pack("<H", 56))  # e_phentsize
    data.extend(struct.pack("<H", 1))  # e_phnum
    data.extend(struct.pack("<H", 64))  # e_shentsize
    data.extend(struct.pack("<H", 0))  # e_shnum
    data.extend(struct.pack("<H", 0))  # e_shstrndx
    assert len(data) == phoff
    data.extend(struct.pack("<II", 1, 5))  # PT_LOAD, PF_R|PF_X
    data.extend(struct.pack("<QQ", 0, 0x400000))  # offset, vaddr
    data.extend(struct.pack("<QQ", 0, 0x1000))  # paddr, filesz
    data.extend(struct.pack("<QQ", 0x2000, 0x1000))  # memsz, align
    data.extend(b"\x00" * (56 - 48))  # pad to 56
    return bytes(data)


def _make_elf64_with_dynamic() -> bytes:
    data = bytearray()
    data.extend(b"\x7fELF\x02\x01\x01\x00\x00" + b"\x00" * 7)
    data.extend(struct.pack("<HHI", 2, 0x3E, 1))  # ET_EXEC, x86_64
    data.extend(struct.pack("<Q", 0x400000))  # e_entry
    data.extend(struct.pack("<Q", 0))  # e_phoff
    shoff = 64
    data.extend(struct.pack("<Q", shoff))  # e_shoff
    data.extend(struct.pack("<I", 0))  # e_flags
    data.extend(struct.pack("<H", 64))  # e_ehsize
    data.extend(struct.pack("<H", 0))  # e_phentsize
    data.extend(struct.pack("<H", 0))  # e_phnum
    data.extend(struct.pack("<H", 64))  # e_shentsize
    data.extend(struct.pack("<H", 3))  # e_shnum
    data.extend(struct.pack("<H", 2))  # e_shstrndx
    assert len(data) == shoff
    strtab_data = b"\x00.dynamic\x00.shstrtab\x00"
    strtab_offset = shoff + (3 * 64)
    sec0 = struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    dyn_data = struct.pack("<qQ", 1, 0x400000) + struct.pack("<qQ", 0, 0)
    data.extend(sec0)
    data.extend(struct.pack("<IIQQQQIIQQ", 1, 6, 0, 0x5000, strtab_offset + 20, len(dyn_data), 0, 0, 0, 8))
    data.extend(struct.pack("<IIQQQQIIQQ", 13, 3, 0, 0, strtab_offset, len(strtab_data), 0, 0, 0, 1))
    data.extend(b"\x00" * (strtab_offset - len(data)))
    data.extend(strtab_data)
    data.extend(dyn_data)
    return bytes(data)


class TestELFMagic:
    def test_magic_bytes(self):
        assert ELF_MAGIC == b"\x7fELF"

    def test_valid_elf_magic_detected(self):
        hdr = _read_elf_header(b"\x7fELF\x02\x01\x01\x00\x00" + b"\x00" * 7 + b"\x00" * 48)
        assert hdr is not None

    def test_invalid_magic_returns_none(self):
        assert _read_elf_header(b"MZ\x90\x00" + b"\x00" * 60) is None

    def test_too_short_returns_none(self):
        assert _read_elf_header(b"\x7fELF") is None


class TestELFConstants:
    def test_ei_class(self):
        assert EI_CLASS_32 == 1
        assert EI_CLASS_64 == 2

    def test_ei_data(self):
        assert EI_DATA_LE == 1
        assert EI_DATA_BE == 2

    def test_et_constants(self):
        assert ET_EXEC == 2
        assert ET_DYN == 3
        assert ET_REL == 1

    def test_machine_names(self):
        assert MACHINE_NAMES[0x03] == "i386"
        assert MACHINE_NAMES[0x3E] == "x86_64"
        assert MACHINE_NAMES[0x28] == "ARM"
        assert MACHINE_NAMES[0xB7] == "AArch64"


class TestELFHeader:
    def test_elf64_header(self):
        data = _make_minimal_elf64()
        hdr = _read_elf_header(data)
        assert hdr is not None
        assert hdr.ei_class == EI_CLASS_64
        assert hdr.ei_data == EI_DATA_LE
        assert hdr.e_type == "ET_EXEC"
        assert hdr.e_machine == "x86_64"
        assert hdr.e_entry == 0x400000

    def test_elf32_header(self):
        data = _make_elf32_sections()
        hdr = _read_elf_header(data)
        assert hdr is not None
        assert hdr.ei_class == EI_CLASS_32
        assert hdr.ei_data == EI_DATA_LE
        assert hdr.e_machine == "i386"

    def test_module_exported_parse(self):
        data = _make_minimal_elf64()
        result = parse_elf(data)
        assert result.header.ei_class == EI_CLASS_64
        assert result.header.e_machine == "x86_64"


class TestELFSectionParsing:
    def test_parse_elf_with_sections(self):
        data = _make_elf32_sections()
        result = parse_elf_sections(data)
        assert len(result) >= 1

    def test_no_sections_on_empty(self):
        sections = parse_elf_sections(b"")
        assert sections == []

    def test_no_sections_on_no_magic(self):
        sections = parse_elf_sections(b"\x00" * 200)
        assert sections == []

    def test_section_header_fields(self):
        data = _make_elf32_sections()
        sections = parse_elf_sections(data)
        if sections:
            sh = sections[0]
            assert isinstance(sh.name, str)
            assert isinstance(sh.type, str)
            assert isinstance(sh.flags, list)

    def test_presence_of_strtab(self):
        data = _make_elf32_sections()
        sections = parse_elf_sections(data)
        has_strtab = any(".shstrtab" in s.name for s in sections)
        assert has_strtab


class TestELFProgramHeaderParsing:
    def test_parse_phdr(self):
        data = _make_elf_with_phdr()
        result = parse_elf(data)
        assert len(result.segments) == 1
        seg = result.segments[0]
        assert seg.type == "PT_LOAD"
        assert "PF_R" in seg.flags or "PF_X" in seg.flags

    def test_no_phdr_on_stripped(self):
        data = _make_minimal_elf64()
        result = parse_elf(data)
        assert result.segments == []


class TestELFSymbolParsing:
    def test_parse_symbols(self):
        data = _make_elf32_sections()
        symbols = parse_elf_symbols(data)
        assert isinstance(symbols, list)

    def test_no_symbols_on_empty(self):
        symbols = parse_elf_symbols(b"")
        assert symbols == []


class TestELFDynamicParsing:
    def test_parse_dynamic(self):
        data = _make_elf64_with_dynamic()
        entries = parse_elf_dynamic(data)
        assert isinstance(entries, list)

    def test_no_dynamic_on_empty(self):
        entries = parse_elf_dynamic(b"")
        assert entries == []


class TestELFAnalysisResult:
    def test_full_parse(self):
        data = _make_elf64_with_dynamic()
        result = parse_elf(data)
        assert isinstance(result, ELFAnalysisResult)
        assert result.header.ei_class == EI_CLASS_64
        assert result.header.e_type == "ET_EXEC"

    def test_pie_detection(self):
        data = bytearray(_make_minimal_elf64())
        data[16:18] = struct.pack("<H", 3)  # ET_DYN
        data = bytes(data)
        result = parse_elf(data)
        assert result.is_pie is True

    def test_stripped_detection(self):
        data = _make_minimal_elf64()
        result = parse_elf(data)
        assert result.is_stripped is True

    def test_empty_data(self):
        result = parse_elf(b"")
        assert isinstance(result, ELFAnalysisResult)
        assert result.header.e_type == "unknown"

    def test_dependencies_detection(self):
        data = _make_elf64_with_dynamic()
        result = parse_elf(data)
        assert isinstance(result.dependencies, list)
        assert isinstance(result.imports, list)

    def test_bytearray_input(self):
        data = _make_minimal_elf64()
        result = parse_elf(bytearray(data))
        assert result.header.e_machine == "x86_64"
