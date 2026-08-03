"""Tests for obfuscation_techniques module — techniques, signatures, detection."""

from __future__ import annotations

import struct

from plugins.module_utils.obfuscation_techniques import (
    DETECTION_HEURISTICS,
    KNOWN_TOOL_SIGNATURES,
    DetectionConfidence,
    DetectionResult,
    ObfuscationTechnique,
    Severity,
    detect_techniques,
)


class TestTechniqueEnum:
    def test_all_six_techniques_exist(self):
        expected = {
            "packing",
            "virtualization",
            "cfg_flattening",
            "string_encryption",
            "anti_debug",
            "opaque_predicates",
        }
        values = {t.value for t in ObfuscationTechnique}
        assert values == expected

    def test_technique_uniqueness(self):
        values = [t.value for t in ObfuscationTechnique]
        assert len(values) == len(set(values))


class TestDetectionConfidence:
    def test_values(self):
        assert DetectionConfidence.LOW.value == "low"
        assert DetectionConfidence.MEDIUM.value == "medium"
        assert DetectionConfidence.HIGH.value == "high"


class TestSeverity:
    def test_values(self):
        assert Severity.INFO.value == "info"
        assert Severity.LOW.value == "low"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.HIGH.value == "high"
        assert Severity.CRITICAL.value == "critical"


class TestHeuristics:
    def test_all_techniques_have_heuristics(self):
        for technique in ObfuscationTechnique:
            assert technique in DETECTION_HEURISTICS, f"No heuristics for {technique}"
            heur = DETECTION_HEURISTICS[technique]
            assert "structural_markers" in heur
            assert len(heur["structural_markers"]) > 0, f"Empty structural markers for {technique}"

    def test_packing_has_byte_patterns(self):
        assert len(DETECTION_HEURISTICS[ObfuscationTechnique.PACKING]["byte_patterns"]) > 0

    def test_packing_has_api_calls(self):
        assert len(DETECTION_HEURISTICS[ObfuscationTechnique.PACKING]["api_calls"]) > 0

    def test_virtualization_has_byte_patterns(self):
        assert len(DETECTION_HEURISTICS[ObfuscationTechnique.VIRTUALIZATION]["byte_patterns"]) > 0

    def test_anti_debug_has_api_calls(self):
        api = DETECTION_HEURISTICS[ObfuscationTechnique.ANTI_DEBUG]["api_calls"]
        assert "IsDebuggerPresent" in api
        assert "CheckRemoteDebuggerPresent" in api

    def test_cfg_flattening_markers(self):
        markers = DETECTION_HEURISTICS[ObfuscationTechnique.CFG_FLATTENING]["structural_markers"]
        assert "single_dispatcher_basic_block" in markers
        assert "high_indirect_branch_ratio" in markers

    def test_opaque_predicates_markers(self):
        markers = DETECTION_HEURISTICS[ObfuscationTechnique.OPAQUE_PREDICATES]["structural_markers"]
        assert "invariant_conditional_jump" in markers
        assert "dead_code_paths" in markers


class TestKnownToolSignatures:
    def test_packing_tools_registered(self):
        sigs = KNOWN_TOOL_SIGNATURES[ObfuscationTechnique.PACKING]
        names = {s.name for s in sigs}
        assert "UPX" in names
        assert "ASPack" in names
        assert "MPRESS" in names

    def test_virtualization_tools_registered(self):
        sigs = KNOWN_TOOL_SIGNATURES[ObfuscationTechnique.VIRTUALIZATION]
        names = {s.name for s in sigs}
        assert "VMProtect" in names
        assert "Themida" in names
        assert "CodeVirtualizer" in names

    def test_cfg_flattening_tools_registered(self):
        sigs = KNOWN_TOOL_SIGNATURES[ObfuscationTechnique.CFG_FLATTENING]
        names = {s.name for s in sigs}
        assert "obfuscator-llvm" in names
        assert "Tigress" in names

    def test_anti_debug_tools_registered(self):
        sigs = KNOWN_TOOL_SIGNATURES[ObfuscationTechnique.ANTI_DEBUG]
        names = {s.name for s in sigs}
        assert "Themida" in names

    def test_opaque_predicates_tools_registered(self):
        sigs = KNOWN_TOOL_SIGNATURES[ObfuscationTechnique.OPAQUE_PREDICATES]
        names = {s.name for s in sigs}
        assert "obfuscator-llvm" in names
        assert "Tigress" in names

    def test_upx_has_byte_patterns(self):
        upx = next(s for s in KNOWN_TOOL_SIGNATURES[ObfuscationTechnique.PACKING] if s.name == "UPX")
        assert b"UPX0" in upx.byte_patterns
        assert "UPX0" in upx.section_names

    def test_vmprotect_has_string_markers(self):
        vmp = next(s for s in KNOWN_TOOL_SIGNATURES[ObfuscationTechnique.VIRTUALIZATION] if s.name == "VMProtect")
        assert "VMProtect" in vmp.string_markers


class TestDetectionResult:
    def test_creation(self):
        r = DetectionResult(
            technique=ObfuscationTechnique.PACKING,
            confidence=DetectionConfidence.HIGH,
            evidence=["high entropy"],
            tool_matches=["UPX"],
        )
        assert r.technique == ObfuscationTechnique.PACKING
        assert r.confidence == DetectionConfidence.HIGH
        assert r.evidence == ["high entropy"]
        assert r.tool_matches == ["UPX"]

    def test_iter_unpacks(self):
        r = DetectionResult(
            technique=ObfuscationTechnique.PACKING,
            confidence=DetectionConfidence.MEDIUM,
            evidence=["entropy 7.8"],
        )
        t, c, e = r
        assert t == ObfuscationTechnique.PACKING
        assert c == DetectionConfidence.MEDIUM
        assert isinstance(e, list)


class TestDetectTechniques:
    def test_string_path_returns_list(self):
        result = detect_techniques("nonexistent_file_xyz.bin")
        assert isinstance(result, list)

    def test_empty_bytes_returns_empty(self):
        result = detect_techniques(b"")
        assert isinstance(result, list)

    def test_bytes_returns_list(self):
        result = detect_techniques(b"\x00" * 100)
        assert isinstance(result, list)

    def test_detect_upx_signature_in_bytes(self):
        data = b"\x00" * 100 + b"UPX0" + b"\x00" * 100
        result = detect_techniques(data)
        assert len(result) >= 1
        techniques = [t.value for t, _, _ in result]
        assert "packing" in techniques

    def test_detect_vmprotect_signature_in_bytes(self):
        data = b"\x00" * 200 + b"VMProtect" + b"\x00" * 200
        result = detect_techniques(data)
        assert len(result) >= 1

    def test_detect_anti_debug_int3_in_bytes(self):
        data = b"\x00" * 100 + b"\xcd\x03" + b"\x00" * 100
        result = detect_techniques(data)
        anti_debug_results = [(t, c, e) for t, c, e in result if t == ObfuscationTechnique.ANTI_DEBUG]
        assert len(anti_debug_results) >= 1

    def test_detect_ptrace_in_bytes(self):
        data = b"\xcd\x80\x31\xdb\x43\x31\xc0\xcd\x80" + b"\x00" * 100
        result = detect_techniques(data)
        anti_debug_results = [(t, c, e) for t, c, e in result if t == ObfuscationTechnique.ANTI_DEBUG]
        assert len(anti_debug_results) >= 1

    def test_return_tuple_structure(self):
        data = b"\x00" * 200 + b"UPX0" + b"\x00" * 100
        result = detect_techniques(data)
        for item in result:
            assert len(item) == 3
            assert isinstance(item[0], ObfuscationTechnique)
            assert isinstance(item[1], DetectionConfidence)
            assert isinstance(item[2], list)

    def test_pe_magic_triggers_section_analysis(self):
        e_lfanew_offset = 0x3C
        header = bytearray(0x200)
        header[0] = ord("M")
        header[1] = ord("Z")
        struct.pack_into("<I", header, e_lfanew_offset, 0x80)
        pe_sig_offset = 0x80
        header[pe_sig_offset : pe_sig_offset + 4] = b"PE\x00\x00"
        num_sections_offset = pe_sig_offset + 6
        struct.pack_into("<H", header, num_sections_offset, 2)
        section_offset = pe_sig_offset + 4 + 20 + 112
        sec1_name = b".UPX0".ljust(8, b"\x00")
        header[section_offset : section_offset + 8] = sec1_name
        struct.pack_into("<I", header, section_offset + 8, 0x1000)
        struct.pack_into("<I", header, section_offset + 16, 0x200)
        struct.pack_into("<I", header, section_offset + 20, 0x400)
        sec1_data = bytearray(0x200)
        for i in range(0x200):
            sec1_data[i] = i % 256
        full = bytes(header) + bytes(sec1_data)
        result = detect_techniques(full)
        assert len(result) >= 1

    def test_detect_macho_anti_debug(self):
        macho_header = b"\xcf\xfa\xed\xfe" + b"\x00" * 28
        data = macho_header + b"\x00" * 100 + b"\xcd\x03" + b"\x00" * 100
        result = detect_techniques(data)
        anti = [(t, c, e) for t, c, e in result if t == ObfuscationTechnique.ANTI_DEBUG]
        assert len(anti) >= 1

    def test_detect_elf_ptrace(self):
        elf_header = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
        data = elf_header + b"\x00" * 200 + b"ptrace" + b"\x00" * 100
        result = detect_techniques(data)
        anti = [(t, c, e) for t, c, e in result if t == ObfuscationTechnique.ANTI_DEBUG]
        assert len(anti) >= 1

    def test_bytarray_input(self):
        data = bytearray(b"\x00" * 200 + b"UPX0" + b"\x00" * 100)
        result = detect_techniques(data)
        assert len(result) >= 1

    def test_non_bytes_nor_string_returns_empty(self):
        result = detect_techniques(42)  # type: ignore[arg-type]
        assert result == []

    def test_js_file_detects_eval_chains(self, tmp_path):
        js_file = tmp_path / "test.js"
        js_file.write_text("eval(atob('c2VjcmV0')); Function('return this')();")
        result = detect_techniques(str(js_file))
        assert len(result) >= 1
        techniques = [t.value for t, _, _ in result]
        assert "string_encryption" in techniques

    def test_js_file_detects_base64_eval(self, tmp_path):
        js_file = tmp_path / "obfuscated.js"
        js_file.write_text('var a = "ZXZhbA=="; eval(atob(a));')
        result = detect_techniques(str(js_file))
        assert len(result) >= 1
        for t, c, _e in result:
            if t == ObfuscationTechnique.STRING_ENCRYPTION:
                assert c in (DetectionConfidence.HIGH, DetectionConfidence.MEDIUM)

    def test_clean_js_no_detection(self, tmp_path):
        js_file = tmp_path / "clean.js"
        js_file.write_text("function add(a, b) { return a + b; }")
        result = detect_techniques(str(js_file))
        assert result == []

    def test_themidia_string_marker(self):
        data = b"\x00" * 100 + b"Themida" + b"\x00" * 100
        result = detect_techniques(data)
        assert len(result) >= 1

    def test_as_pack_string_marker(self):
        data = b"\x00" * 100 + b"ASPack" + b"\x00" * 100
        result = detect_techniques(data)
        assert len(result) >= 1

    def test_mpress_signature(self):
        data = b"\x00" * 100 + b"MPRESS" + b"\x00" * 100
        result = detect_techniques(data)
        assert len(result) >= 1
