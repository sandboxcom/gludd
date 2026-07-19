"""Tests for obfuscation_techniques.py (NF.3 Binary RE)."""

from __future__ import annotations

import importlib
import struct
import sys
from pathlib import Path

import pytest

_COLLECTION_ROOT = Path(__file__).resolve().parents[2] / "collections/ansible_collections/general_ludd/binary_re"
_PLUGIN_ROOT = _COLLECTION_ROOT / "plugins"

if str(_PLUGIN_ROOT / "module_utils") not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT / "module_utils"))

try:
    obf = importlib.import_module("obfuscation_techniques")
    detect_techniques = obf.detect_techniques
    _compute_entropy = obf._compute_entropy
    _identify_file_type = obf._identify_file_type
    _read_pe_sections = obf._read_pe_sections
    _read_elf_sections = obf._read_elf_sections
    _detect_pe_techniques = obf._detect_pe_techniques
    _detect_elf_techniques = obf._detect_elf_techniques
    _detect_js_techniques = obf._detect_js_techniques
    KNOWN_TOOL_SIGNATURES = obf.KNOWN_TOOL_SIGNATURES
    DETECTION_HEURISTICS = obf.DETECTION_HEURISTICS
    ObfuscationTechnique = obf.ObfuscationTechnique
    DetectionConfidence = obf.DetectionConfidence
except ModuleNotFoundError:
    pytest.skip("obfuscation_techniques module not available", allow_module_level=True)


def _make_pe_with_section(section_name: bytes, section_data: bytes) -> bytes:
    header = bytearray(0x200)
    header[0] = ord("M")
    header[1] = ord("Z")
    struct.pack_into("<I", header, 0x3c, 0x80)
    header[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", header, 0x80 + 4, 0x014c)
    struct.pack_into("<H", header, 0x80 + 6, 1)
    optional_header_offset = 0x80 + 4 + 20
    struct.pack_into("<H", header, optional_header_offset, 0x10b)
    section_offset_base = optional_header_offset + 96
    name_field = section_name[:8].ljust(8, b"\x00")
    header[section_offset_base:section_offset_base + 8] = name_field
    struct.pack_into("<I", header, section_offset_base + 8, 0x1000)
    struct.pack_into("<I", header, section_offset_base + 16, len(section_data))
    struct.pack_into("<I", header, section_offset_base + 20, 0x200)
    return bytes(header) + section_data


class TestEntropy:
    def test_empty_returns_zero(self):
        assert _compute_entropy(b"") == 0.0

    def test_single_byte_returns_zero(self):
        assert _compute_entropy(b"\x00" * 100) == 0.0

    def test_uniform_random_is_high(self):
        import random
        random.seed(42)
        data = bytes(random.randint(0, 255) for _ in range(2048))
        entropy = _compute_entropy(data)
        assert entropy > 7.0

    def test_repetitive_is_low(self):
        entropy = _compute_entropy(b"ABABABAB" * 100)
        assert entropy < 2.0


class TestIdentifyFileType:
    def test_pe_detected(self):
        assert _identify_file_type(b"MZ\x00\x00...") == "PE"

    def test_elf_detected(self):
        assert _identify_file_type(b"\x7fELF\x00\x00") == "ELF"

    def test_macho_detected(self):
        assert _identify_file_type(b"\xfe\xed\xfa\xce") == "Mach-O"

    def test_unknown_type(self):
        assert _identify_file_type(b"\x00\x00\x00\x00") == "unknown"


class TestDetectTechniquesPacking:
    def test_detects_upx_in_bytes(self):
        data = b"\x00" * 100 + b"UPX!" + b"\x00" * 100
        results = detect_techniques(data)
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.PACKING in techniques

    def test_detects_aspack_in_bytes(self):
        data = b"\x00" * 100 + b".aspack" + b"\x00" * 100
        results = detect_techniques(data)
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.PACKING in techniques

    def test_detects_mpress(self):
        data = b"\x00" * 100 + b"MPRESS" + b"\x00" * 100
        results = detect_techniques(data)
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.PACKING in techniques


class TestDetectTechniquesVmProtect:
    def test_detects_vmp_marker(self):
        data = b"\x00" * 100 + b"VMProtect" + b"\x00" * 100
        results = detect_techniques(data)
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.VIRTUALIZATION in techniques

    def test_detects_themida_marker(self):
        data = b"\x00" * 100 + b"Themida" + b"\x00" * 100
        results = detect_techniques(data)
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.VIRTUALIZATION in techniques


class TestDetectTechniquesAntiDebug:
    def test_detects_int3_byte(self):
        data = b"\x00" * 100 + b"\xcc" + b"\x00" * 100
        results = detect_techniques(data)
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.ANTI_DEBUG in techniques

    def test_detects_ptrace_in_elf(self):
        data = b"\x7fELF" + b"\x00" * 4 + b"\x00" * 200 + b"ptrace" + b"\x00" * 100
        results = detect_techniques(data)
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.ANTI_DEBUG in techniques


class TestDetectTechniquesStringEncryption:
    def test_detects_strdecrypt_marker(self):
        data = b"\x00" * 100 + b"__strdecrypt" + b"\x00" * 100
        results = detect_techniques(data)
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.STRING_ENCRYPTION in techniques


class TestDetectTechniquesJsSource:
    def test_detects_eval_chains(self, tmp_path):
        f = tmp_path / "obf.js"
        f.write_text("eval('1'); eval('2'); eval('3');")
        results = detect_techniques(str(f))
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.STRING_ENCRYPTION in techniques

    def test_detects_hex_var_names(self, tmp_path):
        f = tmp_path / "obf.js"
        f.write_text("var _0xabc1 = 1; var _0xdef2 = 2;")
        results = detect_techniques(str(f))
        techniques = {r[0] for r in results}
        assert ObfuscationTechnique.STRING_ENCRYPTION in techniques


class TestDetectTechniquesClean:
    def test_clean_low_entropy_bytes(self):
        data = b"\x00" * 500
        results = detect_techniques(data)
        assert results == []

    def test_missing_file_returns_empty(self, tmp_path):
        results = detect_techniques(str(tmp_path / "nope.bin"))
        assert results == []


class TestReadPeSections:
    def test_parses_one_section(self):
        data = _make_pe_with_section(b".text\x00\x00\x00", b"hello world")
        sections = _read_pe_sections(data)
        assert any(name.startswith(".text") for name, _ in sections)

    def test_non_pe_returns_empty(self):
        assert _read_pe_sections(b"\x00\x00\x00\x00") == []


class TestReadElfSections:
    def test_non_elf_returns_empty(self):
        assert _read_elf_sections(b"\x00\x00\x00\x00") == []

    def test_elf_magic_identified(self):
        data = b"\x7fELF" + b"\x00" * 60
        sections = _read_elf_sections(data)
        assert sections == []


class TestDetectPeTechniques:
    def test_high_entropy_section_flagged_as_packing(self):
        high_entropy_data = bytes(i % 256 for i in range(2048))
        data = _make_pe_with_section(b".packed\x00", high_entropy_data)
        sections = _read_pe_sections(data)
        results = _detect_pe_techniques(data, sections)
        techniques = {r.technique for r in results}
        assert ObfuscationTechnique.PACKING in techniques

    def test_packer_section_name_detected(self):
        data = _make_pe_with_section(b"UPX0\x00\x00\x00\x00", b"\x00" * 100)
        sections = _read_pe_sections(data)
        results = _detect_pe_techniques(data, sections)
        packing_results = [r for r in results if r.technique == ObfuscationTechnique.PACKING]
        assert any("UPX0" in " ".join(r.evidence) for r in packing_results)


class TestDetectElfTechniques:
    def test_high_entropy_elf_section(self):
        import random
        random.seed(11)
        high_entropy = bytes(random.randint(0, 255) for _ in range(2048))
        results = _detect_elf_techniques(b"\x7fELF" + high_entropy, [(".data", high_entropy)])
        techniques = {r.technique for r in results}
        assert ObfuscationTechnique.PACKING in techniques


class TestDetectJsTechniques:
    def test_eval_and_base64_marker(self):
        src = "eval(atob('aGVsbG8='));"
        results = _detect_js_techniques(src)
        techniques = {r.technique for r in results}
        assert ObfuscationTechnique.STRING_ENCRYPTION in techniques

    def test_clean_js_no_findings(self):
        results = _detect_js_techniques("var x = 1; console.log(x);")
        assert results == []


class TestKnownToolSignatures:
    def test_packing_signatures_present(self):
        sigs = KNOWN_TOOL_SIGNATURES[ObfuscationTechnique.PACKING]
        names = {s.name for s in sigs}
        assert {"UPX", "ASPack", "MPRESS", "PECompact"}.issubset(names)

    def test_virtualization_signatures_present(self):
        sigs = KNOWN_TOOL_SIGNATURES[ObfuscationTechnique.VIRTUALIZATION]
        names = {s.name for s in sigs}
        assert {"VMProtect", "Themida", "CodeVirtualizer"}.issubset(names)


class TestDetectionHeuristics:
    def test_packing_has_required_keys(self):
        cfg = DETECTION_HEURISTICS[ObfuscationTechnique.PACKING]
        for key in ("byte_patterns", "api_calls", "structural_markers"):
            assert key in cfg

    def test_section_entropy_threshold_is_7(self):
        cfg = DETECTION_HEURISTICS[ObfuscationTechnique.PACKING]
        assert cfg["section_entropy_threshold"] == 7.0


class TestResultShape:
    def test_detect_techniques_returns_tuples(self):
        data = b"UPX!" + b"\x00" * 100
        results = detect_techniques(data)
        for technique, confidence, evidence in results:
            assert isinstance(technique, ObfuscationTechnique)
            assert isinstance(confidence, DetectionConfidence)
            assert isinstance(evidence, list)
            assert all(isinstance(e, str) for e in evidence)


class TestModuleImportability:
    def test_public_api_callable(self):
        assert callable(detect_techniques)

    def test_enums_complete(self):
        assert ObfuscationTechnique.PACKING
        assert ObfuscationTechnique.VIRTUALIZATION
        assert DetectionConfidence.HIGH
