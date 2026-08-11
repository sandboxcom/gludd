"""Source-code vulnerability scanner — detects known insecure patterns.

Scans source content or directories for hardcoded secrets, injection vectors,
unsafe deserialization, weak cryptography, and other vulnerability classes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass
class VulnFinding:
    pattern_id: str
    category: str
    severity: str
    line: int
    snippet: str = ""
    file_path: str = ""


SEVERITY_RANK: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}

PATTERNS: list[dict[str, object]] = [
    # ── hardcoded secrets ─────────────────────────────────────────────
    {
        "id": "HARDCODED_PASSWORD",
        "category": "secret",
        "severity": "critical",
        "regex": re.compile(
            r"(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{4,}['\"]",
            re.IGNORECASE,
        ),
        "description": "Hardcoded password assignment",
    },
    {
        "id": "HARDCODED_SECRET_KEY",
        "category": "secret",
        "severity": "high",
        "regex": re.compile(
            r"(?:secret[_\s]?key|SECRET_KEY|api[_\s]?secret)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
            re.IGNORECASE,
        ),
        "description": "Hardcoded secret key or API secret",
    },
    {
        "id": "HARDCODED_TOKEN",
        "category": "secret",
        "severity": "high",
        "regex": re.compile(
            r'(?:auth[_\s]?token|access[_\s]?token|bearer[_\s]?token)\s*[:=]\s*[\'"][^\'"]{10,}[\'"]',
            re.IGNORECASE,
        ),
        "description": "Hardcoded authentication token",
    },
    # ── injection ─────────────────────────────────────────────────────
    {
        "id": "OS_SYSTEM_INJECTION",
        "category": "injection",
        "severity": "critical",
        "regex": re.compile(r"\bos\.(?:system|popen)\s*\("),
        "description": "os.system or os.popen — potential command injection",
    },
    {
        "id": "SUBPROCESS_SHELL_TRUE",
        "category": "injection",
        "severity": "high",
        "regex": re.compile(r"subprocess\.\w+\s*\([^)]*shell\s*=\s*True"),
        "description": "subprocess with shell=True — command injection risk",
    },
    {
        "id": "EVAL_EXEC_CALL",
        "category": "injection",
        "severity": "critical",
        "regex": re.compile(r"\b(?:eval|exec)\s*\("),
        "description": "eval() or exec() with untrusted input",
    },
    {
        "id": "SQL_STRING_CONCAT",
        "category": "injection",
        "severity": "high",
        "regex": re.compile(
            r"(?:execute|executemany)\s*\(\s*['\"].*?\+\s*\w+\s*",
            re.IGNORECASE,
        ),
        "description": "SQL query built with string concatenation",
    },
    {
        "id": "SQL_FSTRING_INTERPOLATION",
        "category": "injection",
        "severity": "high",
        "regex": re.compile(
            r"(?:execute|executemany)\s*\(\s*f['\"]",
            re.IGNORECASE,
        ),
        "description": "SQL query built with f-string — SQL injection risk",
    },
    # ── deserialization ───────────────────────────────────────────────
    {
        "id": "PICKLE_LOADS",
        "category": "deserialization",
        "severity": "critical",
        "regex": re.compile(r"\bpickle\.(?:loads?|Unpickler)\s*\("),
        "description": "pickle deserialization — arbitrary code execution",
    },
    {
        "id": "YAML_LOAD_UNSAFE",
        "category": "deserialization",
        "severity": "high",
        "regex": re.compile(r"\byaml\.load\s*\("),
        "description": "yaml.load() — unsafe deserialization by default",
    },
    # ── weak cryptography ─────────────────────────────────────────────
    {
        "id": "MD5_HASH",
        "category": "crypto",
        "severity": "medium",
        "regex": re.compile(r"\b(?:hashlib\.)?md5\s*\(", re.IGNORECASE),
        "description": "MD5 hash — cryptographically broken",
    },
    {
        "id": "SHA1_HASH",
        "category": "crypto",
        "severity": "low",
        "regex": re.compile(r"\b(?:hashlib\.)?sha1\s*\(", re.IGNORECASE),
        "description": "SHA1 — weak, prefer SHA-256 or SHA-3",
    },
    # ── plaintext network ─────────────────────────────────────────────
    {
        "id": "HTTP_NOT_HTTPS",
        "category": "network",
        "severity": "medium",
        "regex": re.compile(
            r"['\"]http://[^\s'\"]*(?:login|auth|token|password|secret|api)[^\s'\"]*['\"]", re.IGNORECASE
        ),
        "description": "HTTP URL referencing sensitive endpoint — use HTTPS",
    },
]


def scan_content(
    content: str,
    *,
    severity_threshold: str = "low",
    file_path: str = "",
) -> list[VulnFinding]:
    threshold_rank = SEVERITY_RANK.get(severity_threshold.lower(), 0)
    findings: list[VulnFinding] = []

    for line_no, line in enumerate(content.splitlines(), start=1):
        for pat in PATTERNS:
            pat_severity = str(pat.get("severity", "low"))
            pat_rank = SEVERITY_RANK.get(pat_severity.lower(), 0)
            if pat_rank < threshold_rank:
                continue

            regex = pat["regex"]
            match = regex.search(line) if isinstance(regex, re.Pattern) else re.search(str(regex), line, re.IGNORECASE)

            if match:
                snippet = line.strip()
                if len(snippet) > 200:
                    snippet = snippet[:197] + "..."

                findings.append(
                    VulnFinding(
                        pattern_id=str(pat["id"]),
                        category=str(pat.get("category", "")),
                        severity=pat_severity,
                        line=line_no,
                        snippet=snippet,
                        file_path=file_path,
                    )
                )

    return findings


def scan_files(
    root_dir: str,
    *,
    severity_threshold: str = "low",
    scan_extensions: frozenset[str] | None = None,
) -> list[VulnFinding]:
    if scan_extensions is None:
        scan_extensions = frozenset(
            {
                ".py",
                ".js",
                ".ts",
                ".jsx",
                ".tsx",
                ".go",
                ".rs",
                ".rb",
                ".java",
                ".kt",
                ".swift",
                ".sh",
                ".bash",
                ".yml",
                ".yaml",
                ".json",
                ".tf",
                ".hcl",
                ".dockerfile",
                ".Dockerfile",
                ".cfg",
                ".ini",
                ".toml",
                ".env",
            }
        )

    all_findings: list[VulnFinding] = []

    try:
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fname in filenames:
                _, ext = os.path.splitext(fname)
                if ext.lower() not in scan_extensions and fname.lower() not in scan_extensions:
                    continue

                full_path = os.path.join(dirpath, fname)
                try:
                    with open(full_path, encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except (OSError, PermissionError):
                    continue

                findings = scan_content(
                    content,
                    severity_threshold=severity_threshold,
                    file_path=full_path,
                )
                all_findings.extend(findings)
    except (OSError, PermissionError):
        pass

    return all_findings


def findings_summary(findings: list[VulnFinding]) -> dict[str, object]:
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}

    for f in findings:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        by_category[f.category] = by_category.get(f.category, 0) + 1

    return {
        "total": len(findings),
        "by_severity": by_severity,
        "by_category": by_category,
    }


def scan_checks_passes(
    content: str,
    *,
    severity_threshold: str = "low",
) -> bool:
    return len(scan_content(content, severity_threshold=severity_threshold)) == 0
