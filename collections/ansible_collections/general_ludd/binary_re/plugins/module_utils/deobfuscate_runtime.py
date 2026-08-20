#!/usr/bin/env python3
"""Deobfuscation analysis engine — used by the deobfuscate Ansible role."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ansible_collections.general_ludd.binary_re.plugins.module_utils.obfuscation_techniques import (
    _ANTI_DEBUG_PATTERNS,
    _PACKER_SECTION_REGEX,
    DETECTION_HEURISTICS,
    DetectionConfidence,
    ObfuscationTechnique,
    _compute_entropy,
    _identify_file_type,
    _read_elf_sections,
    _read_pe_sections,
    detect_techniques,
)

PACKER_ENTROPY_THRESHOLD = 7.0


@dataclass
class PackingReport:
    file_type: str = "unknown"
    packed: bool = False
    detections: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CfgFlatteningReport:
    flattened: bool = False
    confidence: str = "none"
    markers: list[str] = field(default_factory=list)


@dataclass
class StringDeobReport:
    encrypted_strings: int = 0
    deobfuscated: int = 0
    confidence: str = "none"
    deobfuscated_strings: list[str] = field(default_factory=list)


@dataclass
class OpaquePredicateReport:
    detected: bool = False
    confidence: str = "none"
    patterns: list[dict[str, Any]] = field(default_factory=list)


def detect_packing(binary_path: str | Path) -> dict[str, Any]:
    path = Path(binary_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"error": str(exc), "packed": False, "detections": []}

    file_type = _identify_file_type(data)
    report = PackingReport(file_type=file_type)

    results = detect_techniques(data)
    packing_results = [
        {"technique": t.value, "confidence": c.value, "evidence": e}
        for t, c, e in results
        if t == ObfuscationTechnique.PACKING
    ]

    if packing_results:
        report.packed = True
        report.detections = packing_results
    else:
        sections = _read_pe_sections(data) if file_type == "PE" else _read_elf_sections(data)
        for sec_name, sec_data in sections:
            if _PACKER_SECTION_REGEX.match(sec_name):
                report.packed = True
                report.detections.append({
                    "technique": "packing",
                    "confidence": DetectionConfidence.HIGH.value,
                    "evidence": [f"packer section name: {sec_name}"],
                })
                break
            entropy = _compute_entropy(sec_data)
            if entropy > PACKER_ENTROPY_THRESHOLD:
                report.packed = True
                report.detections.append({
                    "technique": "packing",
                    "confidence": DetectionConfidence.MEDIUM.value,
                    "evidence": [f"section {sec_name} entropy {entropy:.2f} > {PACKER_ENTROPY_THRESHOLD}"],
                })
                break

    return {
        "file_type": report.file_type,
        "packed": report.packed,
        "detections": report.detections,
    }


_PT_INSTR_BYTES = (
    b"\xcd\x80\x31\xdb\x43\x31\xc0\xcd\x80",  # ptrace (self-trace check)
)
_CFG_DISPATCH_OPCODES: set[int] = {0xe9, 0xeb, 0xff, 0xe8}
_HIGH_INDIRECT_CALL_RATIO = 0.3


def _count_indirect_transfers(data: bytes, offset: int = 0, limit: int | None = None) -> tuple[int, int]:
    end = min(len(data), offset + limit) if limit else len(data)
    total = 0
    indirect = 0
    i = offset
    while i + 1 < end:
        b = data[i]
        if b in _CFG_DISPATCH_OPCODES:
            total += 1
            if b == 0xff:
                indirect += 1
        i += 1
    return total, indirect


def detect_cfg_flattening(binary_path: str | Path) -> dict[str, Any]:
    path = Path(binary_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"error": str(exc), "flattened": False, "confidence": "none", "markers": []}

    report = CfgFlatteningReport()
    markers: list[str] = []

    total, indirect = _count_indirect_transfers(data, limit=65536)
    if total > 0 and (indirect / total) > _HIGH_INDIRECT_CALL_RATIO:
        markers.append("high_indirect_branch_ratio")
        report.confidence = "high"
    elif total > 0 and indirect > (total * 0.15):
        markers.append("elevated_indirect_branch_ratio")
        report.confidence = "medium"

    results = detect_techniques(data)
    for t, c, _e in results:
        if t == ObfuscationTechnique.CFG_FLATTENING:
            markers.append(f"tool_match:{c.value}")
            if c == DetectionConfidence.HIGH:
                report.confidence = "high"
            elif report.confidence == "none":
                report.confidence = "medium"

    for pattern, desc in _ANTI_DEBUG_PATTERNS:
        if pattern in data:
            markers.append(f"anti_debug_present:{desc}")

    if len(markers) >= 2 or report.confidence == "high":
        report.flattened = True
    if report.confidence == "none":
        report.confidence = "low"
    report.markers = markers

    return {
        "flattened": report.flattened,
        "confidence": report.confidence,
        "markers": report.markers,
    }


_RESUMED_STRING_PATTERNS = (
    b"\x00",
)
_XOR_KEY_PATTERNS = bytes(range(256))
_VALID_PRINTABLE_MIN = 32
_VALID_PRINTABLE_MAX = 126


def _extract_printable_strings(data: bytes, min_len: int = 4) -> list[str]:
    strings: list[str] = []
    current: bytearray = bytearray()
    for byte in data:
        if _VALID_PRINTABLE_MIN <= byte <= _VALID_PRINTABLE_MAX:
            current.append(byte)
        else:
            if len(current) >= min_len:
                strings.append(current.decode("ascii", errors="replace"))
            current = bytearray()
    if len(current) >= min_len:
        strings.append(current.decode("ascii", errors="replace"))
    return strings


def _try_xor_deobfuscate(data: bytes, key_byte: int) -> list[str]:
    decoded = bytes(b ^ key_byte for b in data)
    strings = _extract_printable_strings(decoded, min_len=3)
    return strings


def deobfuscate_strings(binary_path: str | Path, key_hint: int | None = None) -> dict[str, Any]:
    path = Path(binary_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {
            "error": str(exc),
            "encrypted_strings": 0,
            "deobfuscated": 0,
            "confidence": "none",
            "deobfuscated_strings": [],
        }

    results = detect_techniques(data)
    has_string_encryption = any(t == ObfuscationTechnique.STRING_ENCRYPTION for t, _, _ in results)

    native_strings = _extract_printable_strings(data, min_len=4)
    native_printable_count = len(native_strings)

    encryption_markers: list[str] = []
    api_indicators = DETECTION_HEURISTICS[ObfuscationTechnique.STRING_ENCRYPTION]["api_calls"]
    for api_name in api_indicators:
        if api_name.encode("ascii") in data:
            encryption_markers.append(f"crypto_api:{api_name}")

    byte_markers = DETECTION_HEURISTICS[ObfuscationTechnique.STRING_ENCRYPTION]["byte_patterns"]
    for marker in byte_markers:
        if marker in data:
            encryption_markers.append(f"byte_marker:{marker.decode('ascii', errors='replace')}")

    deobfuscated: list[str] = []
    if key_hint is not None:
        deobfuscated = _try_xor_deobfuscate(data, key_hint)
    elif native_printable_count < 10 or has_string_encryption:
        best_count = 0
        for k in range(1, 256):
            found = _try_xor_deobfuscate(data, k)
            if len(found) > best_count and len(found) > native_printable_count:
                best_count = len(found)
                deobfuscated = found

    encrypted_count = 0
    if has_string_encryption or encryption_markers or (len(data) > 1024 and native_printable_count < 5):
        encrypted_count = max(len(encryption_markers), 1)

    confidence = "high" if has_string_encryption else "medium" if encrypted_count > 0 else "low"

    return {
        "encrypted_strings": encrypted_count,
        "deobfuscated": len(deobfuscated),
        "confidence": confidence,
        "deobfuscated_strings": deobfuscated[:50],
        "native_printable_strings": native_printable_count,
        "encryption_markers": encryption_markers,
    }


_OPAQUE_PREDICATE_PATTERNS: list[tuple[bytes, str]] = [
    (b"\x31\xc0\x74", "xor_eax_eax_jz"),    # xor eax, eax; jz (always taken)
    (b"\x33\xc0\x74", "xor_eax_eax_jz_alt"), # xor eax, eax; jz
    (b"\x85\xc0\x74", "test_eax_eax_jz"),    # test eax, eax; jz (with zero val)
    (b"\x83\xf8\x00\x74", "cmp_eax_0_jz"),   # cmp eax, 0; jz
    (b"\x48\x85\xc0\x74", "test_rax_rax_jz"), # x64 test rax, rax; jz
    (b"\x39\xc0\x74", "cmp_eax_eax_jz"),     # cmp eax, eax; jz (always true)
    (b"\xb8\x01\x00\x00\x00\x85\xc0\x74", "mov1_test_jz"),  # mov eax, 1; test eax, eax; jz
    (b"\x6a\x01\x58\x85\xc0\x74", "push1_pop_test_jz"),     # push 1; pop eax; test eax, eax; jz
]


def detect_opaque_predicates(binary_path: str | Path) -> dict[str, Any]:
    path = Path(binary_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"error": str(exc), "detected": False, "confidence": "none", "patterns": []}

    patterns_found: list[dict[str, Any]] = []
    for pattern, desc in _OPAQUE_PREDICATE_PATTERNS:
        count = data.count(pattern)
        if count > 0:
            patterns_found.append({"pattern": desc, "occurrences": count})

    results = detect_techniques(data)
    for t, _c, e in results:
        if t == ObfuscationTechnique.OPAQUE_PREDICATES:
            for ev in e:
                if any(
                    marker in ev
                    for marker in DETECTION_HEURISTICS[ObfuscationTechnique.OPAQUE_PREDICATES]["structural_markers"]
                ):
                    patterns_found.append({"pattern": ev, "occurrences": 1})

    confidence = "high" if len(patterns_found) >= 3 else "medium" if patterns_found else "low"

    return {
        "detected": len(patterns_found) > 0,
        "confidence": confidence,
        "patterns": patterns_found,
    }


_MODES: dict[str, Any] = {
    "packing": ("packing_detection.json", detect_packing),
    "cfg_flattening": ("cfg_flattening.json", detect_cfg_flattening),
    "strings": ("string_deobfuscation.json", deobfuscate_strings),
    "opaque_predicates": ("opaque_predicates.json", detect_opaque_predicates),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Deobfuscation analysis engine")
    parser.add_argument("--mode", choices=sorted(_MODES), required=True, help="Analysis mode")
    parser.add_argument("--binary", required=True, help="Path to target binary")
    parser.add_argument("--output", default="-", help="Output file path (default: stdout)")
    parser.add_argument(
        "--key-hint",
        type=lambda x: int(x, 0),
        default=None,
        help="XOR key hint for string deobfuscation",
    )
    args = parser.parse_args()

    _output_file, func = _MODES[args.mode]

    kwargs: dict[str, Any] = {}
    if args.mode == "strings" and args.key_hint is not None:
        kwargs["key_hint"] = args.key_hint

    result = func(args.binary, **kwargs)
    result["mode"] = args.mode
    result["binary"] = args.binary

    output = json.dumps(result, indent=2, default=str)

    if args.output == "-":
        print(output)
    else:
        Path(args.output).write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
