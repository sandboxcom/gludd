"""Tests for binary_re contracts: BinaryFormat, SectionHeader,
ImportTable, ExportTable, DisassemblyResult."""

from __future__ import annotations

import pytest

from general_ludd.binary_re.contracts import (
    BinaryFormat,
    DisassemblyResult,
    ExportTable,
    ImportTable,
    SectionHeader,
)


class TestBinaryFormat:
    def test_has_pe_elf_macho_coff_wasm_unknown(self):
        members = set(BinaryFormat)
        assert members == {
            BinaryFormat.PE,
            BinaryFormat.ELF,
            BinaryFormat.MACH_O,
            BinaryFormat.COFF,
            BinaryFormat.WASM,
            BinaryFormat.UNKNOWN,
        }

    def test_from_string_case_sensitive(self):
        assert BinaryFormat("PE") == BinaryFormat.PE
        assert BinaryFormat("ELF") == BinaryFormat.ELF

    def test_from_string_raises_valueerror_on_bogus(self):
        with pytest.raises(ValueError):
            BinaryFormat("not-a-format")

    def test_str_value_roundtrips(self):
        for fmt in BinaryFormat:
            assert BinaryFormat(str(fmt)) is fmt

    def test_is_str_enum(self):
        assert isinstance(BinaryFormat.PE.value, str)


class TestSectionHeader:
    def test_defaults_and_required(self):
        sh = SectionHeader(
            name=".text",
            virtual_address=0x1000,
            virtual_size=0x200,
            raw_offset=0x400,
            raw_size=0x200,
        )
        assert sh.name == ".text"
        assert sh.virtual_address == 0x1000
        assert sh.virtual_size == 0x200
        assert sh.raw_offset == 0x400
        assert sh.raw_size == 0x200
        assert sh.permissions == "rx"
        assert sh.entropy == 0.0
        assert sh.content_hash == ""

    def test_permissions_must_be_valid(self):
        SectionHeader(
            name=".text",
            virtual_address=0x1000,
            virtual_size=0x100,
            raw_offset=0x200,
            raw_size=0x100,
            permissions="rwx",
        )

    def test_permissions_bogus_raises_valueerror(self):
        with pytest.raises(ValueError, match="permissions"):
            SectionHeader(
                name=".text",
                virtual_address=0x1000,
                virtual_size=0x100,
                raw_offset=0x200,
                raw_size=0x100,
                permissions="execute",
            )

    def test_empty_name_raises_valueerror(self):
        with pytest.raises(ValueError, match="name"):
            SectionHeader(
                name="",
                virtual_address=0,
                virtual_size=1,
                raw_offset=0,
                raw_size=1,
            )

    def test_only_whitespace_name_raises_valueerror(self):
        with pytest.raises(ValueError, match="name"):
            SectionHeader(
                name="   ",
                virtual_address=0,
                virtual_size=1,
                raw_offset=0,
                raw_size=1,
            )

    def test_negative_virtual_size_raises_valueerror(self):
        with pytest.raises(ValueError, match="virtual_size"):
            SectionHeader(
                name=".data",
                virtual_address=0,
                virtual_size=-1,
                raw_offset=0,
                raw_size=10,
            )

    def test_negative_raw_size_raises_valueerror(self):
        with pytest.raises(ValueError, match="raw_size"):
            SectionHeader(
                name=".data",
                virtual_address=0,
                virtual_size=10,
                raw_offset=0,
                raw_size=-5,
            )

    def test_negative_raw_offset_raises_valueerror(self):
        with pytest.raises(ValueError, match="raw_offset"):
            SectionHeader(
                name=".data",
                virtual_address=0,
                virtual_size=10,
                raw_offset=-1,
                raw_size=10,
            )

    def test_entropy_not_in_range_raises_valueerror(self):
        with pytest.raises(ValueError, match="entropy"):
            SectionHeader(
                name=".data",
                virtual_address=0,
                virtual_size=10,
                raw_offset=0,
                raw_size=10,
                entropy=9.0,
            )

    def test_entropy_negative_raises_valueerror(self):
        with pytest.raises(ValueError, match="entropy"):
            SectionHeader(
                name=".data",
                virtual_address=0,
                virtual_size=10,
                raw_offset=0,
                raw_size=10,
                entropy=-0.5,
            )

    def test_entropy_at_maximum_is_ok(self):
        sh = SectionHeader(
            name=".data",
            virtual_address=0,
            virtual_size=10,
            raw_offset=0,
            raw_size=10,
            entropy=8.0,
        )
        assert sh.entropy == 8.0

    def test_permissions_empty_string_allowed(self):
        sh = SectionHeader(
            name=".bss",
            virtual_address=0,
            virtual_size=10,
            raw_offset=0,
            raw_size=10,
            permissions="",
        )
        assert sh.permissions == ""

    def test_frozen_prevents_mutation(self):
        sh = SectionHeader(
            name=".text",
            virtual_address=0x1000,
            virtual_size=0x100,
            raw_offset=0x200,
            raw_size=0x100,
        )
        with pytest.raises(AttributeError):
            sh.name = ".data"  # type: ignore[misc]

    def test_repr_includes_name(self):
        sh = SectionHeader(
            name=".text",
            virtual_address=0x1000,
            virtual_size=0x200,
            raw_offset=0x400,
            raw_size=0x200,
        )
        r = repr(sh)
        assert ".text" in r
        assert "SectionHeader" in r


class TestImportTable:
    def test_defaults_and_required(self):
        it = ImportTable(library="kernel32.dll", symbols=("VirtualAlloc", "WriteFile"))
        assert it.library == "kernel32.dll"
        assert it.symbols == ("VirtualAlloc", "WriteFile")
        assert it.ordinal_only is False

    def test_empty_library_raises_valueerror(self):
        with pytest.raises(ValueError, match="library"):
            ImportTable(library="", symbols=())

    def test_only_whitespace_library_raises_valueerror(self):
        with pytest.raises(ValueError, match="library"):
            ImportTable(library="   ", symbols=("func",))

    def test_ordinal_only_true(self):
        it = ImportTable(library="ntdll.dll", symbols=(), ordinal_only=True)
        assert it.ordinal_only is True

    def test_empty_symbols_allowed(self):
        it = ImportTable(library="somelib.so", symbols=())
        assert it.symbols == ()

    def test_frozen_prevents_mutation(self):
        it = ImportTable(library="foo.dll", symbols=("bar",))
        with pytest.raises(AttributeError):
            it.library = "changed.dll"  # type: ignore[misc]

    def test_repr_includes_library(self):
        it = ImportTable(library="user32.dll", symbols=("MessageBoxA",))
        r = repr(it)
        assert "user32.dll" in r
        assert "ImportTable" in r


class TestExportTable:
    def test_defaults_and_required(self):
        et = ExportTable(
            library="mylib.dll",
            symbols=("DoWork", "Init"),
            ordinal_base=1,
        )
        assert et.library == "mylib.dll"
        assert et.symbols == ("DoWork", "Init")
        assert et.ordinal_base == 1

    def test_empty_library_raises_valueerror(self):
        with pytest.raises(ValueError, match="library"):
            ExportTable(library="", symbols=())

    def test_only_whitespace_library_raises_valueerror(self):
        with pytest.raises(ValueError, match="library"):
            ExportTable(library="   ", symbols=("x",))

    def test_empty_symbols_allowed(self):
        et = ExportTable(library="emptylib", symbols=())
        assert et.symbols == ()

    def test_negative_ordinal_base_raises_valueerror(self):
        with pytest.raises(ValueError, match="ordinal_base"):
            ExportTable(library="foo", symbols=("a",), ordinal_base=-1)

    def test_ordinal_base_default_is_zero(self):
        et = ExportTable(library="lib", symbols=("f",))
        assert et.ordinal_base == 0

    def test_frozen_prevents_mutation(self):
        et = ExportTable(library="lib", symbols=("f",))
        with pytest.raises(AttributeError):
            et.library = "x"  # type: ignore[misc]

    def test_repr_includes_library(self):
        et = ExportTable(library="exports.so", symbols=("Run",))
        r = repr(et)
        assert "exports.so" in r
        assert "ExportTable" in r


class TestDisassemblyResult:
    def test_defaults_and_required(self):
        dr = DisassemblyResult(
            binary_path="/bin/true",
            binary_format=BinaryFormat.ELF,
            architecture="x86-64",
        )
        assert dr.binary_path == "/bin/true"
        assert dr.binary_format == BinaryFormat.ELF
        assert dr.architecture == "x86-64"
        assert dr.sections == ()
        assert dr.imports == ()
        assert dr.exports == ()
        assert dr.entry_point == 0
        assert dr.function_count == 0
        assert dr.disassembly_tool == "unknown"
        assert dr.timestamp == 0

    def test_empty_binary_path_raises_valueerror(self):
        with pytest.raises(ValueError, match="binary_path"):
            DisassemblyResult(
                binary_path="",
                binary_format=BinaryFormat.ELF,
                architecture="x86",
            )

    def test_only_whitespace_binary_path_raises_valueerror(self):
        with pytest.raises(ValueError, match="binary_path"):
            DisassemblyResult(
                binary_path="   ",
                binary_format=BinaryFormat.ELF,
                architecture="x86",
            )

    def test_empty_architecture_raises_valueerror(self):
        with pytest.raises(ValueError, match="architecture"):
            DisassemblyResult(
                binary_path="/bin/true",
                binary_format=BinaryFormat.ELF,
                architecture="",
            )

    def test_binary_format_must_be_binaryformat_instance(self):
        with pytest.raises(ValueError, match="binary_format"):
            DisassemblyResult(
                binary_path="/bin/true",
                binary_format="ELF",  # type: ignore[arg-type]
                architecture="x86",
            )

    def test_with_sections(self):
        sections = (
            SectionHeader(name=".text", virtual_address=0x1000, virtual_size=0x200, raw_offset=0x400, raw_size=0x200),
            SectionHeader(name=".data", virtual_address=0x2000, virtual_size=0x100, raw_offset=0x600, raw_size=0x100),
        )
        dr = DisassemblyResult(
            binary_path="/bin/true",
            binary_format=BinaryFormat.ELF,
            architecture="x86-64",
            sections=sections,
        )
        assert len(dr.sections) == 2

    def test_with_imports(self):
        imports = (
            ImportTable(library="kernel32.dll", symbols=("GetProcAddress",)),
            ImportTable(library="user32.dll", symbols=("MessageBoxA",)),
        )
        dr = DisassemblyResult(
            binary_path="/bin/true",
            binary_format=BinaryFormat.PE,
            architecture="x86",
            imports=imports,
        )
        assert len(dr.imports) == 2

    def test_with_exports(self):
        exports = (ExportTable(library="mylib.dll", symbols=("Start", "Stop", "Query")),)
        dr = DisassemblyResult(
            binary_path="/bin/true",
            binary_format=BinaryFormat.PE,
            architecture="x86",
            exports=exports,
        )
        assert len(dr.exports) == 1

    def test_entry_point_and_function_count(self):
        dr = DisassemblyResult(
            binary_path="/bin/echo",
            binary_format=BinaryFormat.MACH_O,
            architecture="ARM64",
            entry_point=0x100000,
            function_count=42,
            disassembly_tool="ghidra",
            timestamp=1720000000,
        )
        assert dr.entry_point == 0x100000
        assert dr.function_count == 42
        assert dr.disassembly_tool == "ghidra"
        assert dr.timestamp == 1720000000

    def test_negative_function_count_raises_valueerror(self):
        with pytest.raises(ValueError, match="function_count"):
            DisassemblyResult(
                binary_path="/bin/true",
                binary_format=BinaryFormat.ELF,
                architecture="x86",
                function_count=-1,
            )

    def test_negative_entry_point_raises_valueerror(self):
        with pytest.raises(ValueError, match="entry_point"):
            DisassemblyResult(
                binary_path="/bin/true",
                binary_format=BinaryFormat.ELF,
                architecture="x86",
                entry_point=-42,
            )

    def test_negative_timestamp_raises_valueerror(self):
        with pytest.raises(ValueError, match="timestamp"):
            DisassemblyResult(
                binary_path="/bin/true",
                binary_format=BinaryFormat.ELF,
                architecture="x86",
                timestamp=-100,
            )

    def test_section_tuples_must_be_sectionheader(self):
        with pytest.raises(ValueError, match="sections"):
            DisassemblyResult(
                binary_path="/bin/true",
                binary_format=BinaryFormat.ELF,
                architecture="x86",
                sections=(object(),),  # type: ignore[arg-type]
            )

    def test_import_tuples_must_be_importtable(self):
        with pytest.raises(ValueError, match="imports"):
            DisassemblyResult(
                binary_path="/bin/true",
                binary_format=BinaryFormat.PE,
                architecture="x86",
                imports=(object(),),  # type: ignore[arg-type]
            )

    def test_export_tuples_must_be_exporttable(self):
        with pytest.raises(ValueError, match="exports"):
            DisassemblyResult(
                binary_path="/bin/true",
                binary_format=BinaryFormat.PE,
                architecture="x86",
                exports=(object(),),  # type: ignore[arg-type]
            )

    def test_frozen_prevents_mutation(self):
        dr = DisassemblyResult(
            binary_path="/bin/true",
            binary_format=BinaryFormat.ELF,
            architecture="x86-64",
        )
        with pytest.raises(AttributeError):
            dr.binary_path = "changed"  # type: ignore[misc]

    def test_repr_includes_path_and_format(self):
        dr = DisassemblyResult(
            binary_path="/bin/true",
            binary_format=BinaryFormat.ELF,
            architecture="x86-64",
        )
        r = repr(dr)
        assert "/bin/true" in r
        assert "ELF" in r
        assert "DisassemblyResult" in r


_VALID_PERMISSIONS = frozenset(["", "r", "rx", "rw", "rwx", "r_shared"])


class TestSectionHeaderPermissionsExhaustive:
    def test_all_valid_permissions_create_without_error(self):
        for perm in _VALID_PERMISSIONS:
            sh = SectionHeader(
                name=".test",
                virtual_address=0,
                virtual_size=1,
                raw_offset=0,
                raw_size=1,
                permissions=perm,
            )
            assert sh.permissions == perm

    def test_near_valid_permissions_rejected(self):
        for bogus in ("R", "RX", "RW", "RWX", "wx", "x", "w", "R__SHARED"):
            with pytest.raises(ValueError):
                SectionHeader(
                    name=".test",
                    virtual_address=0,
                    virtual_size=1,
                    raw_offset=0,
                    raw_size=1,
                    permissions=bogus,
                )
