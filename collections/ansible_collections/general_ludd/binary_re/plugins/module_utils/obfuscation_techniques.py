"""Obfuscation technique enums, signatures, and detection heuristics."""

from __future__ import annotations

import enum
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path


class ObfuscationTechnique(enum.Enum):
    PACKING = "packing"
    VIRTUALIZATION = "virtualization"
    CFG_FLATTENING = "cfg_flattening"
    STRING_ENCRYPTION = "string_encryption"
    ANTI_DEBUG = "anti_debug"
    OPAQUE_PREDICATES = "opaque_predicates"


class DetectionConfidence(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Severity(enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ToolSignature:
    name: str
    description: str
    byte_patterns: list[bytes] = field(default_factory=list)
    section_names: list[str] = field(default_factory=list)
    string_markers: list[str] = field(default_factory=list)
    import_features: list[str] = field(default_factory=list)


@dataclass
class DetectionResult:
    technique: ObfuscationTechnique
    confidence: DetectionConfidence
    evidence: list[str]
    tool_matches: list[str] = field(default_factory=list)

    def __iter__(self):
        return iter((self.technique, self.confidence, self.evidence))


KNOWN_TOOL_SIGNATURES: dict[ObfuscationTechnique, list[ToolSignature]] = {
    ObfuscationTechnique.PACKING: [
        ToolSignature(
            name="UPX",
            description="Ultimate Packer for eXecutables",
            byte_patterns=[b"UPX0", b"UPX1", b"UPX!"],
            section_names=["UPX0", "UPX1", ".upx"],
            string_markers=["$Info: This file is packed with the UPX", "UPX! "],
        ),
        ToolSignature(
            name="ASPack",
            description="ASPack software protector",
            byte_patterns=[b".aspack", b".adata"],
            section_names=[".aspack", ".adata"],
            string_markers=["ASPack"],
        ),
        ToolSignature(
            name="MPRESS",
            description="MPRESS executable compressor",
            byte_patterns=[b"MPRESS"],
            section_names=[".MPRESS1", ".MPRESS2"],
            string_markers=["MPRESS"],
        ),
        ToolSignature(
            name="PECompact",
            description="PECompact executable compressor",
            byte_patterns=[b"PEC2", b"pec2"],
            section_names=[".pec"],
            string_markers=["PECompact"],
        ),
    ],
    ObfuscationTechnique.VIRTUALIZATION: [
        ToolSignature(
            name="VMProtect",
            description="VMProtect code virtualization",
            byte_patterns=[b".vmp0", b".vmp1", b"VMProtect"],
            section_names=[".vmp0", ".vmp1", ".vmp2"],
            string_markers=["VMProtect", "VMProtectMarker", "vmp_"],
            import_features=["FindResourceA", "GetProcAddress"],
        ),
        ToolSignature(
            name="Themida",
            description="Themida/WinLicense protection",
            byte_patterns=[b"Themida", b"WinLicense"],
            string_markers=["Themida", "WinLicense", "3XProt"],
            import_features=["CreateThread", "VirtualProtect", "GetModuleHandleA"],
        ),
        ToolSignature(
            name="CodeVirtualizer",
            description="Oreans CodeVirtualizer",
            byte_patterns=[b"CV", b"CODEV"],
            string_markers=["CodeVirtualizer", "oreans"],
        ),
    ],
    ObfuscationTechnique.CFG_FLATTENING: [
        ToolSignature(
            name="obfuscator-llvm",
            description="LLVM-based obfuscation passes",
            string_markers=["__obfuscated_main"],
            import_features=[],
        ),
        ToolSignature(
            name="Tigress",
            description="Tigress C diversifier/obfuscator",
            string_markers=["tigress", "Tigress_", "__tigress"],
        ),
    ],
    ObfuscationTechnique.STRING_ENCRYPTION: [
        ToolSignature(
            name="obfuscator-llvm",
            description="LLVM string encryption pass",
            string_markers=["__strdecrypt", "decrypt_string"],
        ),
    ],
    ObfuscationTechnique.ANTI_DEBUG: [
        ToolSignature(
            name="Themida",
            description="Themida anti-debugging",
            import_features=["IsDebuggerPresent", "CheckRemoteDebuggerPresent", "NtQueryInformationProcess"],
        ),
    ],
    ObfuscationTechnique.OPAQUE_PREDICATES: [
        ToolSignature(
            name="obfuscator-llvm",
            description="LLVM bogus control flow pass",
        ),
        ToolSignature(
            name="Tigress",
            description="Tigress opaque predicate injection",
        ),
    ],
}


DETECTION_HEURISTICS: dict[ObfuscationTechnique, dict] = {
    ObfuscationTechnique.PACKING: {
        "byte_patterns": [
            b"UPX0", b"UPX1", b"UPX!", b"MPRESS", b"PEC2", b"pec2",
            b".aspack", b".adata",
        ],
        "api_calls": [
            "LoadLibraryA", "GetProcAddress", "VirtualAlloc",
            "VirtualProtect", "VirtualFree", "GetModuleHandleA",
            "GlobalAlloc",
        ],
        "structural_markers": [
            "high_entropy_sections",
            "small_import_table",
            "write_execute_sections",
            "fewer_than_5_imports",
            "section_raw_size_mismatch",
            "entry_point_in_writable_section",
        ],
        "section_entropy_threshold": 7.0,
        "import_count_threshold": 5,
    },
    ObfuscationTechnique.VIRTUALIZATION: {
        "byte_patterns": [
            b".vmp0", b".vmp1", b"VMProtect",
            b"Themida", b"WinLicense", b"CV", b"CODEV",
        ],
        "api_calls": [
            "FindResourceA", "SizeofResource", "LockResource",
            "CreateThread", "ResumeThread", "SuspendThread",
            "VirtualProtect", "NtAllocateVirtualMemory",
        ],
        "structural_markers": [
            "custom_vm_handler_loop",
            "large_dispatcher_block",
            "switch_based_interpreter",
            "high_indirect_call_ratio",
            "obfuscated_import_resolution",
        ],
    },
    ObfuscationTechnique.CFG_FLATTENING: {
        "byte_patterns": [],
        "api_calls": [],
        "structural_markers": [
            "single_dispatcher_basic_block",
            "high_indirect_branch_ratio",
            "switch_block_for_dispatch",
            "state_variable_controlling_flow",
            "no_natural_loops_detected",
        ],
    },
    ObfuscationTechnique.STRING_ENCRYPTION: {
        "byte_patterns": [
            b"__strdecrypt", b"decrypt_string", b"xstr",
        ],
        "api_calls": [
            "CryptDecrypt", "CryptStringToBinaryA",
            "RtlDecryptMemory",
        ],
        "structural_markers": [
            "no_printable_strings",
            "string_decryption_routine",
            "xor_string_loop",
            "encoded_string_table",
            "strings_only_decoded_at_runtime",
        ],
    },
    ObfuscationTechnique.ANTI_DEBUG: {
        "byte_patterns": [
            b"\x0f\x0b", b"\xcd\x01",
        ],
        "api_calls": [
            "IsDebuggerPresent", "CheckRemoteDebuggerPresent",
            "NtQueryInformationProcess", "NtSetInformationThread",
            "OutputDebugStringA", "ZwQueryInformationProcess",
            "DebugBreak", "RtlRemoteCall",
            "BlockInput", "NtYieldExecution",
            "SetDebugPrivilege", "GetTickCount",
        ],
        "structural_markers": [
            "ptrace_self_check",
            "int3_breakpoint_scan",
            "timestamp_checks",
            "trap_flag_set",
            "debug_register_check",
            "nanomites",
            "hardware_breakpoint_detection",
        ],
    },
    ObfuscationTechnique.OPAQUE_PREDICATES: {
        "byte_patterns": [
            b"\x33\xc0",  # xor eax, eax
        ],
        "api_calls": [],
        "structural_markers": [
            "invariant_conditional_jump",
            "dead_code_paths",
            "always_taken_branch",
            "always_not_taken_branch",
            "math_identity_condition",
            "pointer_range_check_always_true",
            "redundant_compare_with_zero",
        ],
    },
}


_ANTI_DEBUG_PATTERNS: list[tuple[bytes, str]] = [
    (b"\xcd\x80\x31\xdb\x43\x31\xc0\xcd\x80", "ptrace_self_check_linux_x86"),
    (b"\x64\xa1\x30\x00\x00\x00", "peb_being_debugged_x86"),
    (b"\x64\x8b\x0d\x30\x00\x00\x00", "peb_being_debugged_x64"),
    (b"\x0f\x31\x31\xd2", "rdtsc_timing_check"),
    (b"\xcd\x03", "int3_breakpoint"),
    (b"\xcc", "int3_byte"),
    (b"\xcd\x01", "int1_trap_flag"),
]

_PACKER_SECTION_REGEX = re.compile(
    r"^(\.?UPX\d|\.vmp\d|\.aspack|\.adata|\.MPRESS\d|\.pec\d?|"
    r"\.enigma|\.petite|\.wlc|\.stub|\.nsp\d|\.sforce)",
    re.IGNORECASE,
)

_BASE64_INJECTION_MARKERS = [
    b"eval(", b"exec(", b"__import__(", b"compile(",
]
_BASE64_REGEX = re.compile(rb"[A-Za-z0-9+/]{4,}={0,2}")

_ELF_MAGIC = b"\x7fELF"
_PE_MAGIC = b"MZ"
_MACHO_MAGICS = [
    b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
]


def _read_elf_sections(data: bytes) -> list[tuple[str, bytes]]:
    sections: list[tuple[str, bytes]] = []
    try:
        if data[:4] != _ELF_MAGIC:
            return sections
        if struct.calcsize("H") == 2:
            bitness = "32" if data[4] == 1 else "64"
        else:
            return sections
        shoff: int
        shentsize: int
        shnum: int
        shstrndx: int
        if bitness == "64":
            (
                _e_type, _e_machine, _e_version,
                _e_entry, _e_phoff,
                shoff, _e_flags, _e_ehsize, _e_phentsize,
                _e_phnum, shentsize, shnum, shstrndx,
            ) = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
        else:
            (
                _e_type, _e_machine, _e_version,
                _e_entry, _e_phoff,
                shoff, _e_flags, _e_ehsize, _e_phentsize,
                _e_phnum, shentsize, shnum, shstrndx,
            ) = struct.unpack_from("<HHIIIIIHHHHH", data, 16)
        if shoff <= 0:
            return sections
        strtab_offset = shoff + (shstrndx * shentsize)
        if strtab_offset + shentsize > len(data):
            return sections
        if bitness == "64":
            _sh_name, sh_type, _sh_flags, _sh_addr, sh_offset, sh_size = struct.unpack_from(
                "<IIQQQQ", data, strtab_offset
            )
        else:
            _sh_name, sh_type, _sh_flags, _sh_addr, sh_offset, sh_size = struct.unpack_from(
                "<IIIII", data, strtab_offset
            )
        if sh_type != 3:
            return sections
        strtab_data = data[sh_offset : sh_offset + sh_size]
        if not strtab_data:
            return sections
        for i in range(shnum):
            sec_offset = shoff + (i * shentsize)
            if sec_offset + shentsize > len(data):
                break
            if bitness == "64":
                sec_name_idx, sec_type, sec_flags, _sec_addr, sec_off, sec_size = struct.unpack_from(
                    "<IIQQQQ", data, sec_offset
                )
            else:
                sec_name_idx, sec_type, sec_flags, _sec_addr, sec_off, sec_size = struct.unpack_from(
                    "<IIIII", data, sec_offset
                )
            if sec_type == 8:
                continue
            name_end = strtab_data.find(b"\x00", sec_name_idx)
            sec_name_bytes = strtab_data[sec_name_idx:name_end] if name_end != -1 else b""
            try:
                sec_name = sec_name_bytes.decode("ascii", errors="ignore")
            except Exception:
                sec_name = ""
            section_data = data[sec_off : sec_off + sec_size]
            if section_data:
                sections.append((sec_name, section_data))
    except (struct.error, IndexError, ValueError):
        pass
    return sections


def _read_pe_sections(data: bytes) -> list[tuple[str, bytes]]:
    sections: list[tuple[str, bytes]] = []
    try:
        if data[:2] != b"MZ":
            return sections
        pe_offset = struct.unpack_from("<I", data, 0x3c)[0]
        if pe_offset <= 0 or pe_offset + 4 > len(data):
            return sections
        sig = data[pe_offset : pe_offset + 4]
        if sig != b"PE\x00\x00":
            return sections
        coff_header_offset = pe_offset + 4
        num_sections = struct.unpack_from("<H", data, coff_header_offset + 2)[0]
        optional_header_offset = coff_header_offset + 20
        magic = struct.unpack_from("<H", data, optional_header_offset)[0]
        if magic == 0x20b:
            section_offset_base = optional_header_offset + 112
        else:
            section_offset_base = optional_header_offset + 96
        for i in range(num_sections):
            sec_hdr = section_offset_base + (i * 40)
            if sec_hdr + 40 > len(data):
                break
            sec_name = data[sec_hdr : sec_hdr + 8].rstrip(b"\x00").decode("ascii", errors="ignore")
            sec_vsize = struct.unpack_from("<I", data, sec_hdr + 8)[0]
            sec_offset = struct.unpack_from("<I", data, sec_hdr + 20)[0]
            sec_size = struct.unpack_from("<I", data, sec_hdr + 16)[0]
            read_size = min(sec_size, sec_vsize)
            if sec_offset > 0 and sec_offset + read_size <= len(data):
                sections.append((sec_name, data[sec_offset : sec_offset + read_size]))
    except (struct.error, IndexError, ValueError):
        pass
    return sections


def _identify_file_type(data: bytes) -> str:
    if data[:4] == _ELF_MAGIC:
        return "ELF"
    if data[:2] == _PE_MAGIC:
        return "PE"
    for magic in _MACHO_MAGICS:
        if data[:4] == magic:
            return "Mach-O"
    return "unknown"


def _compute_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    import math
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / n
            entropy -= p * math.log2(p)
    return entropy


def _detect_pe_techniques(data: bytes, sections: list[tuple[str, bytes]]) -> list[DetectionResult]:
    results: list[DetectionResult] = []

    import_count = data.count(b"GetProcAddress") + data.count(b"LoadLibrary")
    for sec_name, sec_data in sections:
        entropy = _compute_entropy(sec_data)
        if entropy > DETECTION_HEURISTICS[ObfuscationTechnique.PACKING]["section_entropy_threshold"]:
            evidence = [f"section {sec_name} entropy {entropy:.2f} > 7.0"]
            if import_count < DETECTION_HEURISTICS[ObfuscationTechnique.PACKING]["import_count_threshold"]:
                evidence.append(f"low import count ({import_count})")
            results.append(DetectionResult(
                technique=ObfuscationTechnique.PACKING,
                confidence=DetectionConfidence.HIGH if import_count < 5 else DetectionConfidence.MEDIUM,
                evidence=evidence,
            ))
            break

    for sec_name, _ in sections:
        if _PACKER_SECTION_REGEX.match(sec_name):
            found = False
            for r in results:
                if r.technique == ObfuscationTechnique.PACKING:
                    r.evidence.append(f"packer section: {sec_name}")
                    r.confidence = DetectionConfidence.HIGH
                    found = True
                    break
            if not found:
                results.append(DetectionResult(
                    technique=ObfuscationTechnique.PACKING,
                    confidence=DetectionConfidence.HIGH,
                    evidence=[f"packer section: {sec_name}"],
                ))
            break

    for sig_group in KNOWN_TOOL_SIGNATURES.values():
        for sig in sig_group:
            for pattern in sig.byte_patterns:
                if pattern in data:
                    technique = [t for t, sigs in KNOWN_TOOL_SIGNATURES.items()
                                 if sig in sigs][0] if isinstance(sig_group, list) else None
                    if technique:
                        tool_match_already = any(
                            r.technique == technique and sig.name in r.tool_matches
                            for r in results
                        )
                        if not tool_match_already:
                            results.append(DetectionResult(
                                technique=technique,
                                confidence=DetectionConfidence.HIGH,
                                evidence=[f"detected {sig.name}: {sig.description}"],
                                tool_matches=[sig.name],
                            ))

    for pattern, desc in _ANTI_DEBUG_PATTERNS:
        if pattern in data:
            results.append(DetectionResult(
                technique=ObfuscationTechnique.ANTI_DEBUG,
                confidence=DetectionConfidence.MEDIUM,
                evidence=[f"anti-debug pattern: {desc}"],
            ))

    return results


def _detect_elf_techniques(data: bytes, sections: list[tuple[str, bytes]]) -> list[DetectionResult]:
    results: list[DetectionResult] = []

    for sec_name, sec_data in sections:
        entropy = _compute_entropy(sec_data)
        if entropy > DETECTION_HEURISTICS[ObfuscationTechnique.PACKING]["section_entropy_threshold"]:
            evidence = [f"section {sec_name} entropy {entropy:.2f} > 7.0"]
            results.append(DetectionResult(
                technique=ObfuscationTechnique.PACKING,
                confidence=DetectionConfidence.HIGH if entropy > 7.5 else DetectionConfidence.MEDIUM,
                evidence=evidence,
            ))
            break

    for pattern, desc in _ANTI_DEBUG_PATTERNS:
        if pattern in data:
            results.append(DetectionResult(
                technique=ObfuscationTechnique.ANTI_DEBUG,
                confidence=DetectionConfidence.MEDIUM,
                evidence=[f"anti-debug pattern: {desc}"],
            ))

    if b"ptrace" in data:
        results.append(DetectionResult(
            technique=ObfuscationTechnique.ANTI_DEBUG,
            confidence=DetectionConfidence.MEDIUM,
            evidence=["ptrace anti-debug call detected"],
        ))

    return results


def _detect_js_techniques(source: str) -> list[DetectionResult]:
    results: list[DetectionResult] = []
    eval_count = source.count("eval(")
    constructor_count = source.count("Function(")
    if eval_count >= 3 or constructor_count >= 1:
        results.append(DetectionResult(
            technique=ObfuscationTechnique.STRING_ENCRYPTION,
            confidence=DetectionConfidence.MEDIUM,
            evidence=[
                f"eval chains: {eval_count} eval() calls",
                f"Function constructors: {constructor_count}",
            ],
        ))
    if _BASE64_REGEX.search(source.encode("ascii", errors="ignore")):
        for marker in _BASE64_INJECTION_MARKERS:
            if marker in source.encode("ascii", errors="ignore"):
                results.append(DetectionResult(
                    technique=ObfuscationTechnique.STRING_ENCRYPTION,
                    confidence=DetectionConfidence.HIGH,
                    evidence=["base64-encoded dynamic code execution detected"],
                ))
                break
    if "_0x" in source:
        results.append(DetectionResult(
            technique=ObfuscationTechnique.STRING_ENCRYPTION,
            confidence=DetectionConfidence.LOW,
            evidence=["hex-encoded variable names detected"],
        ))
    return results


def detect_techniques(
    binary_path_or_bytes: str | Path | bytes | bytearray,
) -> list[tuple[ObfuscationTechnique, DetectionConfidence, list[str]]]:
    if isinstance(binary_path_or_bytes, bytearray):
        data = bytes(binary_path_or_bytes)
    elif isinstance(binary_path_or_bytes, bytes):
        data = binary_path_or_bytes
    elif isinstance(binary_path_or_bytes, (str, Path)):
        path = Path(binary_path_or_bytes)
        if path.suffix in (".js", ".mjs", ".cjs"):
            source = path.read_text(encoding="utf-8", errors="ignore")
            return [
                (r.technique, r.confidence, r.evidence)
                for r in _detect_js_techniques(source)
            ]
        try:
            data = path.read_bytes()
        except OSError:
            return []
    else:
        return []

    file_type = _identify_file_type(data)
    results: list[DetectionResult] = []

    if file_type == "PE":
        sections = _read_pe_sections(data)
        results = _detect_pe_techniques(data, sections)
    elif file_type == "ELF":
        sections = _read_elf_sections(data)
        results = _detect_elf_techniques(data, sections)
    else:
        for pattern, desc in _ANTI_DEBUG_PATTERNS:
            if pattern in data:
                results.append(DetectionResult(
                    technique=ObfuscationTechnique.ANTI_DEBUG,
                    confidence=DetectionConfidence.MEDIUM,
                    evidence=[f"anti-debug pattern: {desc}"],
                ))

    for technique, sigs in KNOWN_TOOL_SIGNATURES.items():
        for sig in sigs:
            for pattern in sig.byte_patterns:
                if pattern in data:
                    already = any(r.technique == technique and sig.name in r.tool_matches for r in results)
                    if not already:
                        results.append(DetectionResult(
                            technique=technique,
                            confidence=DetectionConfidence.HIGH,
                            evidence=[f"detected {sig.name}: {sig.description}"],
                            tool_matches=[sig.name],
                        ))
                        break
            for marker in sig.string_markers:
                encoded = marker.encode("ascii", errors="ignore")
                if encoded and encoded in data:
                    technique2 = _find_technique_for_signature(sig)
                    if technique2:
                        already = any(r.technique == technique2 and sig.name in r.tool_matches for r in results)
                        if not already:
                            results.append(DetectionResult(
                                technique=technique2,
                                confidence=DetectionConfidence.HIGH,
                                evidence=[f"string marker: {marker}"],
                                tool_matches=[sig.name],
                            ))

    return [(r.technique, r.confidence, r.evidence) for r in results]


def _find_technique_for_signature(sig: ToolSignature) -> ObfuscationTechnique | None:
    for technique, sigs in KNOWN_TOOL_SIGNATURES.items():
        if sig in sigs:
            return technique
    return None
