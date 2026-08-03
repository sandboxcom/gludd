"""Tests for pe_analyzer module — PE/COFF header/section/import/export parsing."""

from __future__ import annotations

import struct

from plugins.module_utils.pe_analyzer import (
    MACHINE_TYPES,
    PE_MAGIC,
    SUBSYSTEM_TYPES,
    PEAnalysisResult,
    PEDataDirectory,
    _read_dos_header,
    _read_pe_exports,
    _read_pe_header,
    _read_pe_imports,
    _read_pe_sections_full,
    parse_pe,
)


def _make_minimal_pe(num_sections: int = 1, section_size: int = 0x200) -> bytes:
    buf = bytearray()
    buf.extend(b"MZ")
    buf.extend(b"\x00" * 0x3E)
    e_lfanew = 0x80
    struct.pack_into("<I", buf, 0x3C, e_lfanew)
    padding = e_lfanew - len(buf)
    buf.extend(b"\x00" * padding)
    buf.extend(b"PE\x00\x00")
    buf.extend(b"\x00" * 300)
    coff = e_lfanew + 4
    struct.pack_into("<H", buf, coff, 0x8664)  # machine: x86_64
    struct.pack_into("<H", buf, coff + 2, num_sections)
    struct.pack_into("<I", buf, coff + 4, 0)  # timestamp
    struct.pack_into("<I", buf, coff + 8, 0)  # symtab offset
    struct.pack_into("<I", buf, coff + 12, 0)  # num symbols
    struct.pack_into("<H", buf, coff + 16, 0xF0)  # opt hdr size
    struct.pack_into(
        "<H", buf, coff + 18, 0x202E
    )  # characteristics: EXECUTABLE_IMAGE | LARGE_ADDRESS_AWARE | DLL | LINE_NUMS_STRIPPED | LOCAL_SYMS_STRIPPED
    opt_hdr = coff + 20
    struct.pack_into("<H", buf, opt_hdr, 0x020B)  # PE32+ magic
    struct.pack_into("<B", buf, opt_hdr + 2, 0)  # linker major
    struct.pack_into("<I", buf, opt_hdr + 16, 0x1000)  # entry point
    struct.pack_into("<Q", buf, opt_hdr + 24, 0x140000000)  # image base
    struct.pack_into("<I", buf, opt_hdr + 32, 0x1000)  # section alignment
    struct.pack_into("<I", buf, opt_hdr + 36, 0x200)  # file alignment
    struct.pack_into("<H", buf, opt_hdr + 68, 2)  # subsystem: GUI
    struct.pack_into("<H", buf, opt_hdr + 70, 0x8140)  # dll characteristics
    data_dir_start = opt_hdr + 112 if True else opt_hdr + 96  # PE32+ = 112
    struct.pack_into("<I", buf, data_dir_start, 0)  # export rva
    struct.pack_into("<I", buf, data_dir_start + 4, 0)  # export size
    struct.pack_into("<I", buf, data_dir_start + 8, 0x8000)  # import rva
    struct.pack_into("<I", buf, data_dir_start + 12, 0x64)  # import size
    for i in range(16):
        off = data_dir_start + (i * 8)
        if off >= data_dir_start + 16 * 8:
            break
        if i >= 2:
            struct.pack_into("<II", buf, off, 0, 0)
    sec_table = data_dir_start + 128
    for i in range(num_sections):
        hdr = sec_table + (i * 40)
        name = (".text" + str(i)).encode("ascii") if i > 0 else b".text"
        buf.extend(b"\x00" * (hdr + 40 - len(buf)))
        name_padded = name.ljust(8, b"\x00")[:8]
        buf[hdr : hdr + 8] = name_padded
        struct.pack_into("<I", buf, hdr + 8, section_size)  # virtual size
        struct.pack_into("<I", buf, hdr + 12, 0x1000 + (i * 0x1000))  # virtual addr
        struct.pack_into("<I", buf, hdr + 16, section_size)  # raw size
        raw_offset = 0x400 + (i * section_size)
        struct.pack_into("<I", buf, hdr + 20, raw_offset)  # raw offset
        struct.pack_into("<I", buf, hdr + 28, 0x60000020)  # characteristics
    total_size = sec_table + (num_sections * 40)
    buf.extend(b"\x00" * (total_size - len(buf)))
    for i in range(num_sections):
        raw_offset = 0x400 + (i * section_size)
        padding = raw_offset - len(buf)
        buf.extend(b"\x00" * padding)
        sec_data = bytes(range(256)) * ((section_size + 255) // 256)
        buf.extend(sec_data[:section_size])
    return bytes(buf)


def _make_minimal_pe32(num_sections: int = 1) -> bytes:
    buf = bytearray()
    buf.extend(b"MZ")
    buf.extend(b"\x00" * 0x3E)
    e_lfanew = 0x80
    struct.pack_into("<I", buf, 0x3C, e_lfanew)
    buf.extend(b"\x00" * (e_lfanew - len(buf)))
    buf.extend(b"PE\x00\x00")
    buf.extend(b"\x00" * 300)
    coff = e_lfanew + 4
    struct.pack_into("<H", buf, coff, 0x014C)  # machine: i386
    struct.pack_into("<H", buf, coff + 2, num_sections)
    struct.pack_into("<I", buf, coff + 4, 0)
    struct.pack_into("<H", buf, coff + 16, 0xE0)  # opt hdr size
    struct.pack_into("<H", buf, coff + 18, 0x0102)  # characteristics: EXECUTABLE_IMAGE | 32BIT_MACHINE
    opt_hdr = coff + 20
    struct.pack_into("<H", buf, opt_hdr, 0x010B)  # PE32 magic
    struct.pack_into("<I", buf, opt_hdr + 16, 0x1000)  # entry point
    struct.pack_into("<I", buf, opt_hdr + 24, 0x400000)  # image base
    struct.pack_into("<I", buf, opt_hdr + 28, 0x1000)  # section alignment
    struct.pack_into("<I", buf, opt_hdr + 32, 0x200)  # file alignment
    struct.pack_into("<H", buf, opt_hdr + 64, 3)  # subsystem: CONSOLE
    sec_table = opt_hdr + 96  # PE32
    for i in range(num_sections):
        hdr = sec_table + (i * 40)
        name = f".sec{i}\x00\x00".encode("ascii")
        buf.extend(b"\x00" * (hdr + 40 - len(buf)))
        buf[hdr : hdr + 8] = name[:8].ljust(8, b"\x00")
        struct.pack_into("<I", buf, hdr + 8, 0x200)
        struct.pack_into("<I", buf, hdr + 12, 0x1000 + (i * 0x1000))
        struct.pack_into("<I", buf, hdr + 16, 0x200)
        struct.pack_into("<I", buf, hdr + 20, 0x400 + (i * 0x200))
        struct.pack_into("<I", buf, hdr + 28, 0x60000020)
    total_size = sec_table + (num_sections * 40)
    buf.extend(b"\x00" * (total_size - len(buf)))
    for i in range(num_sections):
        raw_offset = 0x400 + (i * 0x200)
        buf.extend(b"\x00" * (raw_offset - len(buf)))
        buf.extend(bytes(range(256)) * 2)
    return bytes(buf)


class TestConstants:
    def test_pe_magic(self):
        assert PE_MAGIC == b"MZ"

    def test_machine_types(self):
        assert MACHINE_TYPES[0x014C] == "i386"
        assert MACHINE_TYPES[0x8664] == "AMD64"
        assert MACHINE_TYPES[0xAA64] == "ARM64"

    def test_subsystem_types(self):
        assert SUBSYSTEM_TYPES[2] == "WINDOWS_GUI"
        assert SUBSYSTEM_TYPES[3] == "WINDOWS_CUI"


class TestDOSHeader:
    def test_parse_dos_header(self):
        data = b"MZ" + b"\x00" * 0x3A
        struct.pack_into("<I", bytearray(data), 0x3C, 0x80)
        hdr = _read_dos_header(bytes(data))
        assert hdr is not None
        assert hdr.e_magic == "MZ"
        assert hdr.e_lfanew == 0x80

    def test_no_magic_rejected(self):
        assert _read_dos_header(b"\x7fELF" + b"\x00" * 60) is None

    def test_too_short_rejected(self):
        assert _read_dos_header(b"M") is None


class TestPEHeader:
    def test_pe32plus_header(self):
        data = _make_minimal_pe()
        pe_hdr = _read_pe_header(data)
        assert pe_hdr is not None
        file_hdr, opt_hdr = pe_hdr
        assert file_hdr.machine == "AMD64"
        assert file_hdr.num_sections == 1
        assert "EXECUTABLE_IMAGE" in file_hdr.characteristics
        assert opt_hdr.magic == "PE32+"
        assert opt_hdr.entry_point == 0x1000
        assert opt_hdr.image_base == 0x140000000
        assert opt_hdr.subsystem == "WINDOWS_GUI"

    def test_pe32_header(self):
        data = _make_minimal_pe32()
        pe_hdr = _read_pe_header(data)
        assert pe_hdr is not None
        file_hdr, opt_hdr = pe_hdr
        assert file_hdr.machine == "i386"
        assert opt_hdr.magic == "PE32"
        assert opt_hdr.image_base == 0x400000

    def test_no_pe_signature(self):
        data = b"MZ\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * 100
        assert _read_pe_header(data) is None

    def test_dll_characteristics(self):
        data = _make_minimal_pe()
        _, opt_hdr = _read_pe_header(data)
        assert "NX_COMPAT" in opt_hdr.dll_characteristics
        assert "DYNAMIC_BASE" in opt_hdr.dll_characteristics

    def test_dll_file_detected(self):
        data = _make_minimal_pe()
        result = parse_pe(data)
        assert result.is_dll is True  # DLL characteristic set in the header


class TestPESections:
    def test_parse_sections(self):
        data = _make_minimal_pe(num_sections=3)
        sections = _read_pe_sections_full(data)
        assert len(sections) == 3
        for sec in sections:
            assert isinstance(sec.name, str)
            assert sec.virtual_address > 0
            assert sec.raw_size > 0
            assert sec.entropy >= 0.0

    def test_section_names(self):
        data = _make_minimal_pe(num_sections=2)
        sections = _read_pe_sections_full(data)
        names = {s.name for s in sections}
        assert len(names) == 2

    def test_section_entropy(self):
        data = _make_minimal_pe(num_sections=1, section_size=0x200)
        sections = _read_pe_sections_full(data)
        section = sections[0]
        assert section.entropy > 0.0
        assert section.entropy <= 8.0


class TestPEImports:
    def test_read_imports(self):
        data = _make_minimal_pe()
        imports = _read_pe_imports(data)
        assert isinstance(imports, list)

    def test_no_imports_on_empty(self):
        imports = _read_pe_imports(b"")
        assert imports == []

    def test_no_imports_on_non_pe(self):
        imports = _read_pe_imports(b"\x00" * 200)
        assert imports == []


class TestPEExports:
    def test_read_exports(self):
        data = _make_minimal_pe()
        exports = _read_pe_exports(data)
        assert isinstance(exports, list)

    def test_no_exports_on_empty(self):
        exports = _read_pe_exports(b"")
        assert exports == []


class TestPEAnalysisResult:
    def test_parse_full_pe(self):
        data = _make_minimal_pe(num_sections=2)
        result = parse_pe(data)
        assert isinstance(result, PEAnalysisResult)
        assert result.dos_header.e_magic == "MZ"
        assert result.file_header.machine == "AMD64"
        assert result.optional_header.magic == "PE32+"
        assert len(result.sections) == 2
        assert result.is_64bit is True
        assert result.is_32bit is False

    def test_parse_pe32(self):
        data = _make_minimal_pe32(num_sections=1)
        result = parse_pe(data)
        assert result.is_64bit is False
        assert result.is_32bit is True
        assert result.optional_header.magic == "PE32"

    def test_empty_data(self):
        result = parse_pe(b"")
        assert isinstance(result, PEAnalysisResult)
        assert result.dos_header.e_magic == ""

    def test_bytearray_input(self):
        data = _make_minimal_pe()
        result = parse_pe(bytearray(data))
        assert result.file_header.machine == "AMD64"

    def test_imports_property(self):
        data = _make_minimal_pe()
        result = parse_pe(data)
        assert isinstance(result.imports, list)

    def test_exports_property(self):
        data = _make_minimal_pe()
        result = parse_pe(data)
        assert isinstance(result.exports, list)

    def test_data_directories(self):
        data = _make_minimal_pe()
        result = parse_pe(data)
        for dd in result.data_directories:
            assert isinstance(dd, PEDataDirectory)
            assert isinstance(dd.name, str)
            assert dd.virtual_address >= 0
            assert dd.size >= 0


class TestMachineDetection:
    def test_i386(self):
        data = _make_minimal_pe32()
        result = parse_pe(data)
        assert result.file_header.machine == "i386"
        assert result.is_32bit is True

    def test_amd64(self):
        data = _make_minimal_pe()
        result = parse_pe(data)
        assert result.file_header.machine == "AMD64"
        assert result.is_64bit is True

    def test_characteristics_decode(self):
        data = _make_minimal_pe32()
        result = parse_pe(data)
        assert "32BIT_MACHINE" in result.file_header.characteristics


class TestNonPEData:
    def test_elf_rejected(self):
        result = parse_pe(b"\x7fELF" + b"\x00" * 200)
        assert result.dos_header.e_magic == ""

    def test_macho_rejected(self):
        result = parse_pe(b"\xfe\xed\xfa\xce" + b"\x00" * 200)
        assert result.dos_header.e_magic == ""
