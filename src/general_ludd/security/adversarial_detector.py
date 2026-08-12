"""Adversarial code detector — fail-closed scanning for deliberate capability degradation.

Detects when code changes (from models or otherwise) attempt to:
  1. Weaken gludd's own guardrails, security, or test infrastructure (self-sabotage)
  2. Insert backdoors, exfiltration channels, or logic bombs
  3. Leak credentials or hardcode secrets
  4. Degrade capabilities (disable features, remove error handling, weaken algorithms)

Follows the SSRF guard pattern: single canonical predicate, no I/O, fail-closed.
All call sites delegate here — blocklist drift is structurally prevented.

Categories:
  SELF_SABOTAGE  — changes that degrade gludd itself (guardrails, tests, validation)
  BACKDOOR       — deliberate access channels (eval, exec, shell injection)
  CREDENTIAL_LEAK — hardcoded secrets, API keys, tokens in plaintext
  LOGIC_DEGRADE  — algorithm weakening, assertion removal, error-swallowing
  DEPENDENCY_ATTACK — unpinned/compromised dependencies, supply-chain attacks
  OBFUSCATION    — encoded payloads, base64 exec chains, hidden code
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from general_ludd.security.fix_not_disable import ActionIntent
from general_ludd.security.sanitize import (
    confine_path,
    confine_path_multi,
    workspace_roots,
)


class Severity(StrEnum):
    CRITICAL = "critical"  # immediate block, no model review
    HIGH = "high"  # require operator confirmation
    MEDIUM = "medium"  # flag in review, escalate if confirmed
    LOW = "low"  # note for audit trail
    INFO = "info"  # informational only


class Category(StrEnum):
    SELF_SABOTAGE = "self_sabotage"
    BACKDOOR = "backdoor"
    CREDENTIAL_LEAK = "credential_leak"
    LOGIC_DEGRADE = "logic_degrade"
    DEPENDENCY_ATTACK = "dependency_attack"
    OBFUSCATION = "obfuscation"


@dataclass(frozen=True)
class AdversarialPattern:
    """One adversarial pattern: regex + category + severity + remediation hint."""

    id: str
    category: Category
    severity: Severity
    description: str
    pattern: re.Pattern[str]
    remediation: str
    confidence: float = 1.0  # 0.0-1.0 likelihood of being adversarial
    multi_line: bool = False


@dataclass
class AdversarialFinding:
    """A single adversarial finding from the scan."""

    pattern_id: str
    category: Category
    severity: Severity
    description: str
    match_text: str
    file_path: str | None = None
    line_number: int | None = None
    confidence: float = 1.0
    remediation: str = ""
    context: str = ""  # surrounding lines for context


@dataclass
class AdversarialScanResult:
    """Aggregated results from an adversarial scan."""

    findings: list[AdversarialFinding] = field(default_factory=list)
    high_confidence: bool = False
    scanned_files: int = 0
    lines_scanned: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def blocked(self) -> bool:
        return any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in self.findings)

    @property
    def summary(self) -> str:
        if not self.findings:
            return "No adversarial patterns detected"
        cats: dict[str, int] = {}
        for f in self.findings:
            cats.setdefault(f.category.value, 0)
            cats[f.category.value] += 1
        parts = [f"{v} {k}" for k, v in sorted(cats.items())]
        return f"Adversarial detected: {', '.join(parts)}"


# ---------------------------------------------------------------------------
# Pattern definitions — single canonical registry
# ---------------------------------------------------------------------------

SELF_SABOTAGE_PATTERNS: list[AdversarialPattern] = [
    AdversarialPattern(
        id="guardrail_disable_registry",
        category=Category.SELF_SABOTAGE,
        severity=Severity.CRITICAL,
        description="Code that disables or empties a guardrail enforcement hook",
        pattern=re.compile(
            r'(?:throw\s+new\s+Error|raise\s+\w+Error)\s*\(\s*\)|'  # emptied throw
            r'\.decision\s*=\s*"allow"\s*//\s*was|'  # flipped block → allow
            r'enabled\s*[:=]\s*false.*(?:guardrail|enforce|security|hook|plugin)|'
            r'return\s*\{\s*permissionDecision\s*:\s*"allow"\s*\}.*bypass',
            re.IGNORECASE,
        ),
        remediation="Restore enforcement; guardrails must block, not bypass",
    ),
    AdversarialPattern(
        id="security_constant_weakened",
        category=Category.SELF_SABOTAGE,
        severity=Severity.CRITICAL,
        description=(
            "Security-sensitive constants weakened "
            "(shorter keys, higher limits, longer timeouts)"
        ),
        pattern=re.compile(
            r'(?:MAX_TOOL_ITERATIONS|MAX_ATTEMPTS|MAX_RETRIES|TOKEN_LIMIT|'
            r'PASSWORD_MIN_LENGTH|KEY_LENGTH|HMAC_KEY)\s*[:=]\s*([0-]+|\d{1,2}(?!\d))|'
            r'(?:TIMEOUT|INTERVAL|DELAY|DEADLINE)\s*[:=]\s*(?:999999|86400\d+|float\("inf"\))',
            re.IGNORECASE,
        ),
        remediation="Restore security-appropriate values; document rationale for any change",
    ),
    AdversarialPattern(
        id="test_coverage_lowered",
        category=Category.SELF_SABOTAGE,
        severity=Severity.HIGH,
        description="Test coverage threshold lowered or coverage check removed",
        pattern=re.compile(
            r'(?:fail_under|--cov-fail-under|coverage.*threshold)\s*[:=]\s*([0-9]|1[0-9]|20)\b|'
            r'#\s*(?:no\s*cover|pragma:\s*no\s*cover).*(?:all|entire|module|file)',
            re.IGNORECASE,
        ),
        remediation="Restore coverage threshold; each exempted line needs documented reason",
    ),
    AdversarialPattern(
        id="assertion_removal",
        category=Category.SELF_SABOTAGE,
        severity=Severity.HIGH,
        description="Mass assertion removal or replacement with pass/no-op",
        pattern=re.compile(
            r'^\s*-\s*assert\s+\S.*\n\s*\+\s*pass\s*$|'
            r'#\s*(?:remove|delete|skip).*assert|'
            r'assert\s+False\s*,\s*"(?:TODO|FIXME|skip)"',
            re.MULTILINE | re.IGNORECASE,
        ),
        remediation="Assertions are safety nets — remove only with replacement validation",
    ),
    AdversarialPattern(
        id="error_handler_silenced",
        category=Category.SELF_SABOTAGE,
        severity=Severity.MEDIUM,
        description="Error handling replaced with silent swallow",
        pattern=re.compile(
            r'except\s+(?:\w+Error|Exception|BaseException)\s*:\s*\n\s+(?:pass|#\s*silent)\s*$|'
            r'except\s+\w+.*:\s*logger\.(?:debug|info)\(.*exc_info\s*=\s*False',
            re.MULTILINE | re.IGNORECASE,
        ),
        remediation="Errors must be logged at appropriate level; never silently swallowed",
    ),
]

BACKDOOR_PATTERNS: list[AdversarialPattern] = [
    AdversarialPattern(
        id="eval_on_input",
        category=Category.BACKDOOR,
        severity=Severity.CRITICAL,
        description="eval/exec called on potentially untrusted input",
        pattern=re.compile(
            r'(?:eval|exec|compile)\s*\(\s*(?:request|input|data|body|params|'
            r'json|payload|query|form|user|message|content|text|raw)\b',
            re.IGNORECASE,
        ),
        remediation="Never eval/exec on untrusted input; use ast.literal_eval or structured parsing",
    ),
    AdversarialPattern(
        id="shell_injection_subprocess",
        category=Category.BACKDOOR,
        severity=Severity.CRITICAL,
        description="subprocess with shell=True on user-controllable input",
        pattern=re.compile(
            r'subprocess\.(?:run|Popen|call|check_output|check_call)\s*\('
            r'(?=[^)]*\bshell\s*=\s*True)'
            r'(?=[^)]*\b(?:input|request|user|data|body|params|json|query|form)\b)'
            r'[^)]*\)',
            re.IGNORECASE | re.DOTALL,
        ),
        remediation="Use shell=False with list-based args; never shell=True on untrusted input",
    ),
    AdversarialPattern(
        id="socket_exfiltration",
        category=Category.BACKDOOR,
        severity=Severity.CRITICAL,
        description="Raw socket connection to external host (potential data exfiltration)",
        pattern=re.compile(
            r'socket\.(?:socket|connect)\s*\(.*\)|'
            r'urllib\.request\.urlopen\s*\(\s*(?:f["\']|".*%|["\'].*\.format).*(?:'
            r'secret|key|token|password|credential|auth)\b|'
            r'sendall\s*\(\s*(?:.*secret|.*key|.*token)',
            re.IGNORECASE,
        ),
        remediation="Exfiltration channels must never carry secrets; use SSRF-guarded authenticated clients",
    ),
    AdversarialPattern(
        id="os_system_injection",
        category=Category.BACKDOOR,
        severity=Severity.CRITICAL,
        description="os.system or os.popen with string interpolation (command injection)",
        pattern=re.compile(
            r'os\.(?:system|popen)\s*\(\s*(?:f["\']|".*%s|".*\{|".*format|".*\+)',
            re.IGNORECASE,
        ),
        remediation="Use subprocess.run with list args and shell=False",
    ),
    AdversarialPattern(
        id="pickle_deserialize_untrusted",
        category=Category.BACKDOOR,
        severity=Severity.CRITICAL,
        description="pickle.loads on untrusted data (arbitrary code execution)",
        pattern=re.compile(
            r'pickle\.(?:loads?|Unpickler)\s*\(.*(?:request|input|data|body|'
            r'network|remote|download|untrusted|user|external)',
            re.IGNORECASE,
        ),
        remediation="Use json or a safe serialization format for untrusted data",
    ),
    AdversarialPattern(
        id="yaml_unsafe_load",
        category=Category.BACKDOOR,
        severity=Severity.CRITICAL,
        description="yaml.load with unsafe Loader (arbitrary code execution)",
        pattern=re.compile(
            r'yaml\.load\s*\([^)]*(?:Loader\s*=\s*yaml\.(?:Loader|FullLoader|UnsafeLoader)|'
            r'(?!.*Loader\s*=\s*yaml\.(?:Safe|CSafe)Loader))',
            re.IGNORECASE,
        ),
        remediation="Use yaml.safe_load or yaml.load with SafeLoader",
    ),
]

CREDENTIAL_PATTERNS: list[AdversarialPattern] = [
    AdversarialPattern(
        id="hardcoded_api_key",
        category=Category.CREDENTIAL_LEAK,
        severity=Severity.CRITICAL,
        description="Hardcoded API key or credential in source code",
        pattern=re.compile(
            r'(?:api_key|secret_key|access_key|private_key|password|token|credential)\s*[:=]\s*'
            r'["\'](?!(?:\$\{|os\.environ|getenv|config\[))[A-Za-z0-9_\-+/=]{20,}["\']',
            re.IGNORECASE,
        ),
        remediation="Use environment variables or secrets manager; never hardcode credentials",
    ),
    AdversarialPattern(
        id="aws_key_hardcoded",
        category=Category.CREDENTIAL_LEAK,
        severity=Severity.CRITICAL,
        description="AWS access/secret key pattern in source",
        pattern=re.compile(
            r'(?:AKIA[0-9A-Z]{16}|["\'][0-9a-zA-Z/+]{40}["\'].*secret)',
        ),
        remediation="Use IAM roles or environment variables for AWS credentials",
    ),
    AdversarialPattern(
        id="private_key_armor",
        category=Category.CREDENTIAL_LEAK,
        severity=Severity.CRITICAL,
        description="PEM private key armor in source code",
        pattern=re.compile(
            r'-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)\s+PRIVATE\s+KEY-----',
        ),
        remediation="Private keys must never appear in source; use key management service",
    ),
    AdversarialPattern(
        id="github_token",
        category=Category.CREDENTIAL_LEAK,
        severity=Severity.CRITICAL,
        description="GitHub personal access token pattern",
        pattern=re.compile(
            r'(?:gh[pousr]_[A-Za-z0-9_]{36,}|github_pat_[A-Za-z0-9_]{22,})',
        ),
        remediation="Use GITHUB_TOKEN from Actions or gh CLI auth; never commit PATs",
    ),
]

LOGIC_DEGRADE_PATTERNS: list[AdversarialPattern] = [
    AdversarialPattern(
        id="compare_by_identity_not_value",
        category=Category.LOGIC_DEGRADE,
        severity=Severity.HIGH,
        description="Security check degraded from value comparison to identity check (is vs ==)",
        pattern=re.compile(
            r'(?:if\s+\w+\s+is\s+(?:True|False|None)\s*:|is\s+authorized|'
            r'token\s+is\s+["\'][^"\']*["\'])',
            re.IGNORECASE,
        ),
        remediation="Use == for value comparison; 'is' checks identity, not equality",
    ),
    AdversarialPattern(
        id="default_allow_auth",
        category=Category.LOGIC_DEGRADE,
        severity=Severity.HIGH,
        description="Authentication check defaulting to allow on error (fail-open)",
        pattern=re.compile(
            r'(?:if\s+not\s+\w+\s*:\s*return\s+True|'
            r'except\s+\w+\s*:\s*(?:return\s+True|pass)\s*#\s*allow)',
            re.IGNORECASE,
        ),
        remediation="Auth must fail-closed: deny on exception, only allow on explicit success",
    ),
    AdversarialPattern(
        id="hash_downgrade",
        category=Category.LOGIC_DEGRADE,
        severity=Severity.HIGH,
        description="Hash algorithm downgraded (SHA-256→MD5, bcrypt→SHA-1)",
        pattern=re.compile(
            r'(?:hashlib\.(?:md5|sha1)\s*\()|'
            r'(?:hashlib\.\w+).*["\'](?:md5|sha1)["\']',
            re.IGNORECASE,
        ),
        remediation="Use SHA-256 or stronger for integrity; bcrypt/argon2 for passwords",
    ),
    AdversarialPattern(
        id="validation_removed",
        category=Category.LOGIC_DEGRADE,
        severity=Severity.HIGH,
        description="Input validation function replaced with pass-through or always-true",
        pattern=re.compile(
            r'(?:def\s+\w*valid\w*\s*\([^)]*\)\s*:\s*\n\s+return\s+True|'
            r'def\s+\w*sanitize\w*\s*\([^)]*\)\s*:\s*\n\s+return\s+\w+)',
            re.IGNORECASE | re.MULTILINE,
        ),
        remediation="Validation must actually check input; never stub as always-true",
    ),
]

DEPENDENCY_PATTERNS: list[AdversarialPattern] = [
    AdversarialPattern(
        id="unpinned_dependency",
        category=Category.DEPENDENCY_ATTACK,
        severity=Severity.MEDIUM,
        description="Dependency specified without version pin (>=, no ==)",
        pattern=re.compile(
            r'(?:install_requires|dependencies)\s*=\s*\[[^]]*"[^"]+(?:>=|>)[^"]*"',
            re.IGNORECASE,
        ),
        remediation="Pin dependencies with == or >= lower bound + < upper bound",
    ),
    AdversarialPattern(
        id="typosquatted_import",
        category=Category.DEPENDENCY_ATTACK,
        severity=Severity.HIGH,
        description="Import that resembles a common package but has a typo (typosquatting indicator)",
        pattern=re.compile(
            r'(?:import\s+(?:requsts|requestes|pyhtest|bs4|beatifulsoup4|'
            r'sqlalchmy|cryptograpy|nmpy|panda|matplotlb|tensorflw|pythorch|'
            r'djngo|flsk|fastpi|starllete|uvcorn|pydentic|sqlmodel|'
            r'alembc|celry|redis-py|pytz|datetmie|coloredlogs|rich-click))\b',
            re.IGNORECASE,
        ),
        remediation="Verify package name against PyPI; use hash-checking with pip",
    ),
    AdversarialPattern(
        id="git_dependency_unpinned",
        category=Category.DEPENDENCY_ATTACK,
        severity=Severity.HIGH,
        description="Git dependency without commit hash pin",
        pattern=re.compile(
            r'(?:git\+https?://[^@]*|git\+ssh://[^@]*)(?:\s|$|"|\')',
            re.IGNORECASE,
        ),
        remediation="Pin git dependencies to specific commit hashes: git+https://...#<sha>",
    ),
]

OBFUSCATION_PATTERNS: list[AdversarialPattern] = [
    AdversarialPattern(
        id="base64_exec",
        category=Category.OBFUSCATION,
        severity=Severity.CRITICAL,
        description="base64-decoded string passed to eval/exec (obfuscated code execution)",
        pattern=re.compile(
            r'(?:eval|exec|compile)\s*\(\s*base64\.b64decode\s*\(|'
            r'(?:eval|exec|compile)\s*\(\s*.*\.decode\s*\(\s*["\']base64["\']',
            re.IGNORECASE,
        ),
        remediation="Remove obfuscated code execution; all executed code must be auditable",
    ),
    AdversarialPattern(
        id="hex_encoded_string",
        category=Category.OBFUSCATION,
        severity=Severity.HIGH,
        description="Hex-encoded string that decodes to suspicious content",
        pattern=re.compile(
            r'(?:bytes\.fromhex|codecs\.decode).*["\'][0-9a-fA-F]{64,}["\']',
            re.IGNORECASE,
        ),
        remediation="Avoid hex-encoded payloads; use plaintext for auditable code",
    ),
    AdversarialPattern(
        id="rot13_obfuscation",
        category=Category.OBFUSCATION,
        severity=Severity.MEDIUM,
        description="ROT13/codecs-based string obfuscation",
        pattern=re.compile(
            r'codecs\.(?:encode|decode)\s*\(\s*\w+\s*,\s*["\']rot_?13["\']',
            re.IGNORECASE,
        ),
        remediation="Use plaintext identifiers; ROT13 offers zero security and only obfuscates",
    ),
]

ALL_PATTERNS: list[AdversarialPattern] = (
    SELF_SABOTAGE_PATTERNS
    + BACKDOOR_PATTERNS
    + CREDENTIAL_PATTERNS
    + LOGIC_DEGRADE_PATTERNS
    + DEPENDENCY_PATTERNS
    + OBFUSCATION_PATTERNS
)


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

class AdversarialCodeDetector:
    """Fail-closed scanner for adversarial patterns in code changes.

    Single canonical predicate — all call sites delegate here. Blocklist
    drift is structurally prevented because patterns are defined once and
    the scanner enforces them at every vetting point.

    Usage:
        detector = AdversarialCodeDetector()
        result = detector.scan_text(diff_content, file_path="src/foo.py")
        if result.blocked:
            raise SecurityError(result.summary)
    """

    def __init__(self, extra_patterns: list[AdversarialPattern] | None = None) -> None:
        self._patterns = list(ALL_PATTERNS)
        if extra_patterns:
            self._patterns.extend(extra_patterns)
        self._patterns_by_category: dict[Category, list[AdversarialPattern]] = {}
        for p in self._patterns:
            self._patterns_by_category.setdefault(p.category, []).append(p)

    # ---- public API ----

    def create_action_intent(self, finding: AdversarialFinding) -> ActionIntent:
        return ActionIntent(
            action_type="fix",
            target=finding.pattern_id,
            reason=finding.remediation or "Fix the adversarial pattern",
        )

    def scan_text(
        self, text: str, file_path: str | None = None, base_line: int = 1
    ) -> AdversarialScanResult:
        """Scan a text buffer (diff, code, or prompt) for adversarial patterns.

        Returns an AdversarialScanResult.  High-confidence findings indicate
        patterns that should block further processing without model review.
        """
        findings: list[AdversarialFinding] = []
        lines = text.split("\n")
        for i, line in enumerate(lines):
            lineno = base_line + i
            for pattern in self._patterns:
                if pattern.multi_line:
                    continue
                for match in pattern.pattern.finditer(line):
                    if self._is_seed_marker(line):
                        continue
                    findings.append(
                        AdversarialFinding(
                            pattern_id=pattern.id,
                            category=pattern.category,
                            severity=pattern.severity,
                            description=pattern.description,
                            match_text=match.group(0).strip()[:120],
                            file_path=file_path,
                            line_number=lineno,
                            confidence=pattern.confidence,
                            remediation=pattern.remediation,
                            context=self._extract_context(lines, i),
                        )
                    )
        # Multi-line patterns
        for pattern in self._patterns:
            if not pattern.multi_line:
                continue
            for match in pattern.pattern.finditer(text):
                findings.append(
                    AdversarialFinding(
                        pattern_id=pattern.id,
                        category=pattern.category,
                        severity=pattern.severity,
                        description=pattern.description,
                        match_text=match.group(0)[:120],
                        file_path=file_path,
                        confidence=pattern.confidence,
                        remediation=pattern.remediation,
                    )
                )

        high_conf = any(
            f.confidence >= 0.9 and f.severity in (Severity.CRITICAL, Severity.HIGH)
            for f in findings
        )
        return AdversarialScanResult(
            findings=findings,
            high_confidence=high_conf,
            scanned_files=1 if file_path else 0,
            lines_scanned=len(lines),
        )

    def scan_diff(self, diff_text: str) -> AdversarialScanResult:
        """Scan a git diff for adversarial patterns in ADDED lines only.

        Only + lines (additions) are scanned — removals are not flagged
        because correctly removing adversarial code is beneficial.
        """
        findings: list[AdversarialFinding] = []
        lines = diff_text.split("\n")
        current_file = ""
        for i, line in enumerate(lines):
            if line.startswith("+++ ") or line.startswith("--- "):
                current_file = line[4:].split("\t")[0].strip()
                continue
            if not line.startswith("+"):
                continue
            stripped = line[1:]  # remove + prefix
            for pattern in self._patterns:
                if pattern.multi_line:
                    continue
                for match in pattern.pattern.finditer(stripped):
                    if self._is_seed_marker(stripped):
                        continue
                    findings.append(
                        AdversarialFinding(
                            pattern_id=pattern.id,
                            category=pattern.category,
                            severity=pattern.severity,
                            description=pattern.description,
                            match_text=match.group(0).strip()[:120],
                            file_path=current_file,
                            line_number=i + 1,
                            confidence=pattern.confidence,
                            remediation=pattern.remediation,
                            context=stripped[:200],
                        )
                    )
        high_conf = any(
            f.confidence >= 0.9 and f.severity in (Severity.CRITICAL, Severity.HIGH)
            for f in findings
        )
        return AdversarialScanResult(findings=findings, high_confidence=high_conf)

    def scan_task_return(self, task_return: Any) -> AdversarialScanResult:
        """Scan a TaskReturn for adversarial patterns in its result_summary and logs_ref.

        This is the integration point for the review pipeline — called before
        model review to catch adversarial output early.
        """
        all_findings: list[AdversarialFinding] = []
        text_parts: list[tuple[str, str]] = []

        if hasattr(task_return, "result_summary") and task_return.result_summary:
            text_parts.append((task_return.result_summary, "task_return.result_summary"))

        if hasattr(task_return, "diff_ref") and task_return.diff_ref:
            text_parts.append((task_return.diff_ref, "task_return.diff_ref"))

        if hasattr(task_return, "logs_ref") and task_return.logs_ref:
            text_parts.append((task_return.logs_ref, "task_return.logs_ref"))

        for text, label in text_parts:
            result = self.scan_text(text, file_path=label)
            all_findings.extend(result.findings)

        high_conf = any(
            f.confidence >= 0.9 and f.severity in (Severity.CRITICAL, Severity.HIGH)
            for f in all_findings
        )
        return AdversarialScanResult(findings=all_findings, high_confidence=high_conf)

    def scan_file(
        self, file_path: str, allowed_root: str | None = None
    ) -> AdversarialScanResult:
        """Scan a file on disk for adversarial patterns.

        ``file_path`` is jailed with the same realpath+commonpath confinement
        used by the other admin path-confined endpoints (integrity, models,
        skills routers) — :func:`general_ludd.security.sanitize.confine_path` /
        :func:`general_ludd.security.sanitize.confine_path_multi` — so this is
        safe to expose to a caller-controlled (PSK-gated) HTTP request without
        becoming an arbitrary-file-read primitive.

        When ``allowed_root`` is ``None``, the default allowlist is
        :func:`general_ludd.security.sanitize.workspace_roots` (process CWD +
        system temp dir). When ``allowed_root`` IS specified (escape hatch for
        tests and legitimate internal callers that need to scan a path outside
        the default), ONLY that root is checked — the default roots are NOT
        appended, so symlink escapes from the specified root are correctly
        rejected even if the resolved target happens to lie inside a default
        root.

        Raises:
            PermissionError: ``file_path`` does not resolve inside any allowed
                root (path traversal, absolute-path escape, or symlink escape).
        """
        if allowed_root is not None:
            confined = confine_path(file_path, allowed_root)
            if confined is None:
                raise PermissionError(
                    f"scan_file: path {file_path!r} escapes the allowed root "
                    f"{allowed_root!r}"
                )
        else:
            roots = workspace_roots()
            confined = confine_path_multi(file_path, roots)
            if confined is None:
                raise PermissionError(
                    f"scan_file: path {file_path!r} escapes the allowed roots "
                    f"{roots!r}"
                )

        try:
            with open(confined, encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError, PermissionError):
            return AdversarialScanResult()

        return self.scan_text(content, file_path=confined)

    def get_patterns_by_category(self, category: Category) -> list[AdversarialPattern]:
        """Return all registered patterns for a given category."""
        return self._patterns_by_category.get(category, [])

    def get_all_categories(self) -> list[Category]:
        """Return all categories with registered patterns."""
        return sorted(self._patterns_by_category.keys(), key=lambda c: c.value)

    # ---- internal ----

    @staticmethod
    def _is_seed_marker(line: str) -> bool:
        return "bug-class-seed" in line and ":exclude" in line

    @staticmethod
    def _extract_context(lines: list[str], idx: int, window: int = 2) -> str:
        start = max(0, idx - window)
        end = min(len(lines), idx + window + 1)
        return "\n".join(lines[start:end])


def default_adversarial_detector() -> AdversarialCodeDetector:
    return AdversarialCodeDetector()
