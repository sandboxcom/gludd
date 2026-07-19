"""Centralized binary RE pattern database for NF.3.

Consolidates known malware/signature patterns into a single queryable store:
- packer signatures (UPX, ASPack, VMProtect, Themida, ...)
- anti-debug patterns (PEB BeingDebugged, ptrace self-check, int3, rdtsc timing, ...)
- common shellcode patterns (NOP sleds, Metasploit stagers, egghunters, ...)
- obfuscation signatures (CFG flattening, string encryption, opaque predicates)
- malware family markers (common ransomware/credential-stealer strings)

Existing patterns defined in :mod:`obfuscation_techniques` are imported and
re-exposed as :class:`PatternEntry` records so the whole collection has one
source of truth. New categories (shellcode, malware families) are added here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterable, Optional


class PatternCategory(enum.Enum):
    PACKER = "packer"
    ANTI_DEBUG = "anti_debug"
    SHELLCODE = "shellcode"
    OBFUSCATION = "obfuscation"
    MALWARE_FAMILY = "malware_family"


class Severity(enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class PatternPlatform(enum.Enum):
    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"
    CROSS_PLATFORM = "cross_platform"


@dataclass(frozen=True)
class PatternEntry:
    id: str
    category: PatternCategory
    name: str
    byte_patterns: tuple[bytes, ...] = ()
    string_markers: tuple[str, ...] = ()
    severity: Severity = Severity.MEDIUM
    platform: PatternPlatform = PatternPlatform.CROSS_PLATFORM
    description: str = ""
    references: tuple[str, ...] = ()

    def matches_bytes(self, data: bytes) -> bool:
        for pat in self.byte_patterns:
            if pat and pat in data:
                return True
        for marker in self.string_markers:
            if marker:
                if marker.encode("ascii", errors="ignore") in data:
                    return True
        return False


@dataclass(frozen=True)
class ScanMatch:
    entry: PatternEntry
    offset: int
    matched_pattern: bytes


_OBFUSCATION_TECHNIQUES_AVAILABLE = False
try:
    from plugins.module_utils.obfuscation_techniques import (  # type: ignore
        KNOWN_TOOL_SIGNATURES,
        _ANTI_DEBUG_PATTERNS,
        ObfuscationTechnique,
    )
    _OBFUSCATION_TECHNIQUES_AVAILABLE = True
except Exception:  # pragma: no cover - exercised when sibling module is absent
    KNOWN_TOOL_SIGNATURES = None  # type: ignore
    _ANTI_DEBUG_PATTERNS = None  # type: ignore
    ObfuscationTechnique = None  # type: ignore


def _consolidated_packer_patterns() -> list[PatternEntry]:
    """Consolidate packer/protector signatures from obfuscation_techniques.

    The same tool may appear under multiple obfuscation techniques
    (e.g. obfuscator-llvm under CFG flattening, string encryption, and
    opaque predicates). We collapse to one PatternEntry per unique tool
    name, unioning byte patterns, string markers, and import features
    across techniques and picking the highest-severity occurrence.
    """
    if not _OBFUSCATION_TECHNIQUES_AVAILABLE:
        return []

    _platform_by_name = {
        "VMProtect": PatternPlatform.WINDOWS,
        "Themida": PatternPlatform.WINDOWS,
        "CodeVirtualizer": PatternPlatform.WINDOWS,
        "ASPack": PatternPlatform.WINDOWS,
        "MPRESS": PatternPlatform.WINDOWS,
        "PECompact": PatternPlatform.WINDOWS,
    }
    _severity_by_tech = {
        ObfuscationTechnique.VIRTUALIZATION: Severity.HIGH,
        ObfuscationTechnique.PACKING: Severity.MEDIUM,
        ObfuscationTechnique.CFG_FLATTENING: Severity.HIGH,
        ObfuscationTechnique.STRING_ENCRYPTION: Severity.MEDIUM,
        ObfuscationTechnique.OPAQUE_PREDICATES: Severity.MEDIUM,
        ObfuscationTechnique.ANTI_DEBUG: Severity.MEDIUM,
    }

    merged: dict[str, dict[str, object]] = {}
    for technique, signatures in KNOWN_TOOL_SIGNATURES.items():
        for sig in signatures:
            slot = merged.setdefault(
                sig.name,
                {
                    "byte_patterns": set(),
                    "string_markers": set(),
                    "severity": Severity.LOW,
                    "techniques": set(),
                    "description": sig.description,
                },
            )
            slot["byte_patterns"].update(sig.byte_patterns)
            slot["string_markers"].update(sig.string_markers)
            slot["string_markers"].update(sig.import_features)
            sev = _severity_by_tech.get(technique, Severity.MEDIUM)
            if _SEVERITY_ORDER[sev] > _SEVERITY_ORDER[slot["severity"]]:
                slot["severity"] = sev
            slot["techniques"].add(technique.value)

    entries: list[PatternEntry] = []
    for name, slot in merged.items():
        entries.append(
            PatternEntry(
                id=f"packer-{name.lower().replace(' ', '-')}",
                category=PatternCategory.PACKER,
                name=name,
                byte_patterns=tuple(sorted(slot["byte_patterns"])),
                string_markers=tuple(sorted(slot["string_markers"])),
                severity=slot["severity"],
                platform=_platform_by_name.get(name, PatternPlatform.CROSS_PLATFORM),
                description=str(slot["description"]),
            )
        )
    return entries


def _consolidated_anti_debug_patterns() -> list[PatternEntry]:
    entries: list[PatternEntry] = []
    if not _OBFUSCATION_TECHNIQUES_AVAILABLE or not _ANTI_DEBUG_PATTERNS:
        return entries

    _platform_hints = {
        "peb_being_debugged_x86": PatternPlatform.WINDOWS,
        "peb_being_debugged_x64": PatternPlatform.WINDOWS,
        "ptrace_self_check_linux_x86": PatternPlatform.LINUX,
        "rdtsc_timing_check": PatternPlatform.CROSS_PLATFORM,
        "int3_breakpoint": PatternPlatform.CROSS_PLATFORM,
        "int3_byte": PatternPlatform.CROSS_PLATFORM,
        "int1_trap_flag": PatternPlatform.CROSS_PLATFORM,
    }
    for pat, desc in _ANTI_DEBUG_PATTERNS:
        platform = _platform_hints.get(desc, PatternPlatform.CROSS_PLATFORM)
        entries.append(
            PatternEntry(
                id=f"anti-debug-{desc}",
                category=PatternCategory.ANTI_DEBUG,
                name=desc,
                byte_patterns=(pat,),
                severity=Severity.HIGH,
                platform=platform,
                description=f"Anti-debug byte sequence: {desc}",
            )
        )
    return entries


def _consolidated_obfuscation_markers() -> list[PatternEntry]:
    """Lift structural obfuscation markers that are not packer-specific into entries."""
    if not _OBFUSCATION_TECHNIQUES_AVAILABLE:
        return []
    return [
        PatternEntry(
            id="obf-cfg-flattening-dispatcher",
            category=PatternCategory.OBFUSCATION,
            name="CFG flattening dispatcher",
            string_markers=("__obfuscated_main",),
            severity=Severity.MEDIUM,
            description="Single-dispatcher basic block pattern from obfuscator-llvm / Tigress",
        ),
        PatternEntry(
            id="obf-string-encryption-runtime",
            category=PatternCategory.OBFUSCATION,
            name="Runtime string decryption",
            string_markers=("__strdecrypt", "decrypt_string"),
            byte_patterns=(b"__strdecrypt", b"decrypt_string"),
            severity=Severity.MEDIUM,
            description="Strings only resolved at runtime via dedicated decryption routine",
        ),
        PatternEntry(
            id="obf-tigress",
            category=PatternCategory.OBFUSCATION,
            name="Tigress diversifier",
            string_markers=("tigress", "Tigress_", "__tigress"),
            severity=Severity.MEDIUM,
            description="Tigress C diversifier/obfuscator output markers",
        ),
    ]


SHELLCODE_PATTERNS: list[PatternEntry] = [
    PatternEntry(
        id="shellcode-nop-sled",
        category=PatternCategory.SHELLCODE,
        name="NOP sled",
        byte_patterns=(b"\x90\x90\x90\x90\x90\x90\x90\x90",),
        severity=Severity.LOW,
        platform=PatternPlatform.CROSS_PLATFORM,
        description="Long run of x86 NOP (0x90) instructions — classic shellcode preamble",
    ),
    PatternEntry(
        id="shellcode-metasploit-stager",
        category=PatternCategory.SHELLCODE,
        name="Metasploit stager",
        byte_patterns=(
            b"\xfc\xe8\x89\x00\x00\x00\x60",
            b"\xfc\xe8\x82\x00\x00\x00\x60",
            b"\x31\xc9\x64\x8b\x41\x30\x8b\x40\x0c",
        ),
        severity=Severity.CRITICAL,
        platform=PatternPlatform.WINDOWS,
        description="Metasploit Framework windows reverse TCP stager prologue",
        references=("https://docs.metasploit.com/docs/development/developing-modules.html",),
    ),
    PatternEntry(
        id="shellcode-egghunter",
        category=PatternCategory.SHELLCODE,
        name="Egghunter",
        byte_patterns=(
            b"\x66\x81\xca\xff\x0f\x42\x52",
            b"\x66\x81\xcc\xff\x0f\x42\x52",
        ),
        severity=Severity.HIGH,
        platform=PatternPlatform.CROSS_PLATFORM,
        description="Corelan-style egghunter loop searching for a 4-byte egg tag",
    ),
    PatternEntry(
        id="shellcode-xor-decode-loop",
        category=PatternCategory.SHELLCODE,
        name="XOR self-decoding loop",
        byte_patterns=(
            b"\xeb\x10\x5b\x4b\x33\xc9",
            b"\xeb\x0c\x5b\x33\xc9\x8a\x07",
        ),
        severity=Severity.HIGH,
        platform=PatternPlatform.CROSS_PLATFORM,
        description="JMP-CALL-POP shellcode decoder that XORs its payload at runtime",
    ),
    PatternEntry(
        id="shellcode-execve-bin-sh",
        category=PatternCategory.SHELLCODE,
        name="Linux execve(\"/bin/sh\")",
        byte_patterns=(
            b"\x31\xc0\x50\x68\x2f\x2f\x73\x68\x68\x2f\x62\x69\x6e",
            b"\x48\x31\xd2\x48\xbb\x2f\x2f\x62\x69\x6e\x2f\x73\x68",
        ),
        severity=Severity.CRITICAL,
        platform=PatternPlatform.LINUX,
        description="Classic x86/x86_64 Linux shellcode invoking execve on /bin/sh",
    ),
    PatternEntry(
        id="shellcode-bindshell-port-4444",
        category=PatternCategory.SHELLCODE,
        name="Default bind shell port marker",
        byte_patterns=(b"\x11\x5c",),
        string_markers=("/bin/sh\x00",),
        severity=Severity.HIGH,
        platform=PatternPlatform.LINUX,
        description="bind() on port 4444 (0x115c) followed by /bin/sh — default Metasploit bind TCP",
    ),
]


MALWARE_FAMILY_PATTERNS: list[PatternEntry] = [
    PatternEntry(
        id="malware-wannacry",
        category=PatternCategory.MALWARE_FAMILY,
        name="WannaCry ransomware",
        string_markers=("wnry", "@WanaDecryptor@", "Bitcoin", "mssecsvc.exe"),
        severity=Severity.CRITICAL,
        platform=PatternPlatform.WINDOWS,
        description="WannaCry worm / ransomware string markers",
    ),
    PatternEntry(
        id="malware-mimikatz",
        category=PatternCategory.MALWARE_FAMILY,
        name="Mimikatz credential dumper",
        string_markers=("mimikatz", "sekurlsa", "kerberos", "gentilkiwi"),
        severity=Severity.HIGH,
        platform=PatternPlatform.WINDOWS,
        description="Mimikatz LSASS credential dumping utility",
    ),
    PatternEntry(
        id="malware-cobaltstrike",
        category=PatternCategory.MALWARE_FAMILY,
        name="Cobalt Strike beacon",
        string_markers=("CobaltStrike", "beacon.dll", "ReflectiveLoader"),
        byte_patterns=(b"%s as Service",),
        severity=Severity.CRITICAL,
        platform=PatternPlatform.WINDOWS,
        description="Cobalt Strike team-server / beacon artifacts",
    ),
    PatternEntry(
        id="malware-emotet",
        category=PatternCategory.MALWARE_FAMILY,
        name="Emotet banking trojan",
        string_markers=("Emotet", "exe.dll", "pem", "sysmsvc.exe"),
        severity=Severity.HIGH,
        platform=PatternPlatform.WINDOWS,
        description="Emotet loader / banking trojan string indicators",
    ),
    PatternEntry(
        id="malware-lockergoga",
        category=PatternCategory.MALWARE_FAMILY,
        name="LockerGoga ransomware",
        string_markers=("LockerGoga", "encrypted", "How_Decrypt"),
        severity=Severity.CRITICAL,
        platform=PatternPlatform.WINDOWS,
        description="LockerGoga ransomware note markers",
    ),
    PatternEntry(
        id="malware-linux-coinminer",
        category=PatternCategory.MALWARE_FAMILY,
        name="Linux cryptominer",
        string_markers=("stratum+tcp://", "xmrig", "donate-level", "cryptonight"),
        severity=Severity.HIGH,
        platform=PatternPlatform.LINUX,
        description="XMRig / CoinMiner stratum pool markers",
    ),
]


PACKER_PATTERNS: list[PatternEntry] = _consolidated_packer_patterns()
ANTI_DEBUG_PATTERNS: list[PatternEntry] = _consolidated_anti_debug_patterns()
OBFUSCATION_PATTERNS: list[PatternEntry] = _consolidated_obfuscation_markers()


class PatternDatabase:
    """In-memory lookup table over every PatternEntry in the collection."""

    _instance: Optional["PatternDatabase"] = None

    def __new__(cls) -> "PatternDatabase":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False  # type: ignore[attr-defined]
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        entries: list[PatternEntry] = []
        entries.extend(PACKER_PATTERNS)
        entries.extend(ANTI_DEBUG_PATTERNS)
        entries.extend(OBFUSCATION_PATTERNS)
        entries.extend(SHELLCODE_PATTERNS)
        entries.extend(MALWARE_FAMILY_PATTERNS)
        self._entries: tuple[PatternEntry, ...] = tuple(entries)
        self._by_id: dict[str, PatternEntry] = {e.id: e for e in entries}
        self._initialized = True  # type: ignore[attr-defined]

    def all_entries(self) -> list[PatternEntry]:
        return list(self._entries)

    def by_category(self, category: PatternCategory) -> list[PatternEntry]:
        return [e for e in self._entries if e.category == category]

    def by_severity(self, severity: Severity) -> list[PatternEntry]:
        return [e for e in self._entries if e.severity == severity]

    def by_platform(self, platform: PatternPlatform) -> list[PatternEntry]:
        return [
            e for e in self._entries
            if e.platform == platform or e.platform == PatternPlatform.CROSS_PLATFORM
        ]

    def get(self, pattern_id: str) -> Optional[PatternEntry]:
        return self._by_id.get(pattern_id)

    def scan_bytes(
        self,
        data: bytes,
        *,
        categories: Optional[Iterable[PatternCategory]] = None,
        min_severity: Optional[Severity] = None,
    ) -> list[ScanMatch]:
        if not data:
            return []
        cat_filter = set(categories) if categories is not None else None
        min_order = _SEVERITY_ORDER[min_severity] if min_severity is not None else None
        matches: list[ScanMatch] = []
        for entry in self._entries:
            if cat_filter is not None and entry.category not in cat_filter:
                continue
            if min_order is not None and _SEVERITY_ORDER[entry.severity] < min_order:
                continue
            for pat in entry.byte_patterns:
                if not pat:
                    continue
                idx = data.find(pat)
                while idx != -1:
                    matches.append(ScanMatch(entry=entry, offset=idx, matched_pattern=pat))
                    idx = data.find(pat, idx + 1)
            for marker in entry.string_markers:
                if not marker:
                    continue
                encoded = marker.encode("ascii", errors="ignore")
                if not encoded:
                    continue
                idx = data.find(encoded)
                while idx != -1:
                    matches.append(ScanMatch(entry=entry, offset=idx, matched_pattern=encoded))
                    idx = data.find(encoded, idx + 1)
        matches.sort(key=lambda m: (m.offset, m.entry.id))
        return matches

    def summary(self) -> dict[str, object]:
        by_cat: dict[str, int] = {cat.value: 0 for cat in PatternCategory}
        for e in self._entries:
            by_cat[e.category.value] += 1
        return {"total": len(self._entries), "by_category": by_cat}


DATABASE = PatternDatabase()
