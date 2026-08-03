"""Tests for macho_parser module — Mach-O binary header/commands/segments/symbols parsing."""

from __future__ import annotations

import struct

from plugins.module_utils.macho_parser import (
    CPU_TYPE_NAMES,
    FAT_CIGAM,
    FAT_MAGIC,
    FILE_TYPE_NAMES,
    MH_CIGAM,
    MH_CIGAM_64,
    MH_MAGIC,
    MH_MAGIC_64,
    MachOAnalysisResult,
    _read_load_commands,
    _read_macho_header,
    parse_macho,
)

_HEADER64_LE = struct.pack("<IIIIIIII", MH_MAGIC_64, 0x01000007, 0x80000003, 2, 1, 100, 0x00200085, 0)


def _make_macho64_header(cputype: int = 0x01000007, filetype: int = 2, ncmds: int = 1) -> bytes:
    return struct.pack("<IIIIIIII", MH_MAGIC_64, cputype, 0x80000003, filetype, ncmds, 100, 0x00200085, 0)


def _make_macho32_header(cputype: int = 0x00000007, filetype: int = 2) -> bytes:
    return struct.pack("<IIIIIII", MH_MAGIC, cputype, 0x00000003, filetype, 1, 100, 0x00200085)


def _make_segment_command64(name: str, nsects: int = 0) -> bytes:
    name_bytes = name.encode("ascii").ljust(16, b"\x00")[:16]
    data = bytearray()
    data.extend(struct.pack("<II", 0x19, 72 + (nsects * 80)))  # LC_SEGMENT_64
    data.extend(name_bytes)
    data.extend(struct.pack("<QQ", 0, 0x1000))  # vmaddr, vmsize
    data.extend(struct.pack("<QQ", 0, 0x1000))  # fileoff, filesize
    data.extend(struct.pack("<II", 7, 7))  # maxprot, initprot
    data.extend(struct.pack("<I", nsects))
    data.extend(struct.pack("<I", 0))  # flags
    return bytes(data)


def _make_section64(sectname: str = "__text", segname: str = "__TEXT") -> bytes:
    name_bytes = sectname.encode("ascii").ljust(16, b"\x00")[:16]
    seg_bytes = segname.encode("ascii").ljust(16, b"\x00")[:16]
    return struct.pack(
        "<16s16sQQIIIIIIII",
        name_bytes,
        seg_bytes,
        0x1000,
        0x200,
        0x1000,
        0x3,
        0,
        0,
        0,
        0,
        0,
        0,
    )


def _make_fat_header(narch: int = 1) -> bytes:
    data = bytearray()
    data.extend(struct.pack(">I", FAT_MAGIC))
    data.extend(struct.pack(">I", narch))
    return bytes(data)


def _make_fat_arch(cputype: int = 0x01000007) -> bytes:
    return struct.pack(">IIIII", cputype, 0x80000003, 0, 0x1000, 0)


class TestMachOConstants:
    def test_magic_values(self):
        assert MH_MAGIC == 0xFEEDFACE
        assert MH_MAGIC_64 == 0xFEEDFACF
        assert MH_CIGAM == 0xCEFAEDFE
        assert MH_CIGAM_64 == 0xCFFAEDFE
        assert FAT_MAGIC == 0xCAFEBABE
        assert FAT_CIGAM == 0xBEBAFECA

    def test_cpu_type_names(self):
        assert CPU_TYPE_NAMES[0x01000007] == "x86_64"
        assert CPU_TYPE_NAMES[0x0100000C] == "ARM64"

    def test_file_type_names(self):
        assert FILE_TYPE_NAMES[2] == "MH_EXECUTE"
        assert FILE_TYPE_NAMES[6] == "MH_DYLIB"


class TestMachOHeader:
    def test_parse_header64(self):
        data = _make_macho64_header(filetype=2)
        hdr = _read_macho_header(data)
        assert hdr is not None
        assert hdr.magic == "MH_MAGIC_64"
        assert hdr.cputype == "x86_64"
        assert hdr.filetype == "MH_EXECUTE"
        assert hdr.ncmds == 1
        assert hdr.sizeofcmds == 100
        assert "MH_PIE" in hdr.flags

    def test_parse_header32(self):
        data = _make_macho32_header()
        hdr = _read_macho_header(data)
        assert hdr is not None
        assert hdr.magic == "MH_MAGIC"
        assert hdr.cputype == "x86"

    def test_reject_non_macho(self):
        assert _read_macho_header(b"\x7fELF" + b"\x00" * 60) is None
        assert _read_macho_header(b"MZ" + b"\x00" * 60) is None
        assert _read_macho_header(b"") is None

    def test_header_arm64(self):
        data = _make_macho64_header(cputype=0x0100000C)
        hdr = _read_macho_header(data)
        assert hdr.cputype == "ARM64"

    def test_header_dylib(self):
        data = _make_macho64_header(filetype=6)
        hdr = _read_macho_header(data)
        assert hdr.filetype == "MH_DYLIB"

    def test_header_dylinker(self):
        data = _make_macho64_header(filetype=7)
        hdr = _read_macho_header(data)
        assert hdr.filetype == "MH_DYLINKER"


class TestMachOLoadCommands:
    def test_read_load_commands(self):
        hdr_data = _make_macho64_header()
        seg_cmd = _make_segment_command64("__PAGEZERO")
        data = hdr_data + seg_cmd
        cmds = _read_load_commands(data, hdr_data)
        assert len(cmds) >= 1

    def test_no_commands_on_short_data(self):
        cmds = _read_load_commands(b"", _HEADER64_LE)
        assert cmds == []

    def test_segment_command_parsed(self):
        hdr_data = _make_macho64_header()
        seg_cmd = _make_segment_command64("__TEXT", nsects=1)
        sect = _make_section64("__text", "__TEXT")
        data = hdr_data + seg_cmd + sect
        result = parse_macho(data)
        assert len(result.segments) >= 1
        seg = result.segments[0]
        assert seg.name == "__TEXT"

    def test_multiple_segments(self):
        hdr_data = _make_macho64_header(ncmds=2)
        seg1 = _make_segment_command64("__TEXT")
        seg2 = _make_segment_command64("__DATA")
        data = hdr_data + seg1 + seg2
        result = parse_macho(data)
        assert len(result.segments) == 2
        assert result.segments[0].name == "__TEXT"
        assert result.segments[1].name == "__DATA"


class TestFatBinary:
    def test_fat_header_detected(self):
        data = _make_fat_header(narch=2)
        arch1 = _make_fat_arch(0x01000007)  # x86_64
        arch2 = _make_fat_arch(0x0100000C)  # ARM64
        data += arch1 + arch2
        result = parse_macho(data)
        assert result.is_fat is True
        assert len(result.architectures) == 2
        assert "x86_64" in result.architectures
        assert "ARM64" in result.architectures

    def test_non_fat_binary(self):
        data = _make_macho64_header()
        result = parse_macho(data)
        assert result.is_fat is False


class TestMachOAnalysisResult:
    def test_empty_data(self):
        result = parse_macho(b"")
        assert isinstance(result, MachOAnalysisResult)
        assert result.header is not None

    def test_bytearray_input(self):
        data = _make_macho64_header()
        result = parse_macho(bytearray(data))
        assert result.header.cputype == "x86_64"

    def test_imports_detected(self):
        data = _make_macho64_header()
        result = parse_macho(data)
        assert isinstance(result.imports, list)
        assert isinstance(result.exports, list)
