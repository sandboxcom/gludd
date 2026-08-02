"""Prompt injection detection: regex, AST, encoding-aware scanning, severity scoring."""

from __future__ import annotations

import ast
import base64
import codecs
import enum
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class InjectionSeverity(enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InjectionCategory(enum.Enum):
    DIRECT_PROMPT = "direct_prompt"
    INDIRECT_PROMPT = "indirect_prompt"
    JAILBREAK = "jailbreak"
    EVAL_INJECTION = "eval_injection"
    BASE64_INJECTION = "base64_injection"
    ROLE_MANIPULATION = "role_manipulation"
    SYSTEM_OVERRIDE = "system_override"
    ENCODING_LAYERED = "encoding_layered"


PROMPT_INJECTION_PATTERNS: list[tuple[re.Pattern[str], InjectionCategory, InjectionSeverity]] = [
    (
        re.compile(
            r"ignore\s+(all\s+)?(previ(ous|ously)\s+)?instructions\b",
            re.IGNORECASE,
        ),
        InjectionCategory.DIRECT_PROMPT,
        InjectionSeverity.HIGH,
    ),
    (
        re.compile(
            r"(do\s+not\s+follow|forget|disregard)\s+(your\s+)?(previous|prior|original|above|earlier)\s+(instructions|prompt|directives|rules|guidelines)",
            re.IGNORECASE,
        ),
        InjectionCategory.DIRECT_PROMPT,
        InjectionSeverity.HIGH,
    ),
    (
        re.compile(r"you\s+are\s+now\s+(DAN|STAN|DUDE|MAN)", re.IGNORECASE),
        InjectionCategory.JAILBREAK,
        InjectionSeverity.CRITICAL,
    ),
    (
        re.compile(r"(DAN\s+mode|jailbreak\s+mode|developer\s+mode)\s+(activated|enabled|on)", re.IGNORECASE),
        InjectionCategory.JAILBREAK,
        InjectionSeverity.CRITICAL,
    ),
    (
        re.compile(r"system\s*:\s*(override|new\s+prompt|new\s+instruction|message)", re.IGNORECASE),
        InjectionCategory.SYSTEM_OVERRIDE,
        InjectionSeverity.CRITICAL,
    ),
    (
        re.compile(
            r"(you\s+are\s+now|from\s+now\s+on\s+you\s+(are|must|will|shall))",
            re.IGNORECASE,
        ),
        InjectionCategory.ROLE_MANIPULATION,
        InjectionSeverity.HIGH,
    ),
    (
        re.compile(r"pretend\s+(you\s+are|to\s+be|that)\s+(a\s+)?(different|another)", re.IGNORECASE),
        InjectionCategory.ROLE_MANIPULATION,
        InjectionSeverity.HIGH,
    ),
    (
        re.compile(r"override\s+(the\s+)?(system|original)\s+(prompt|role|instruction)", re.IGNORECASE),
        InjectionCategory.SYSTEM_OVERRIDE,
        InjectionSeverity.CRITICAL,
    ),
    (
        re.compile(r"new\s+directive\s*:?\s*you\s+must", re.IGNORECASE),
        InjectionCategory.DIRECT_PROMPT,
        InjectionSeverity.HIGH,
    ),
    (
        re.compile(
            r"(you\s+are\s+no\s+longer|you\s+have\s+been)\s+(a\s+|an\s+)?(assistant|AI|language\s+model|chatbot)",
            re.IGNORECASE,
        ),
        InjectionCategory.ROLE_MANIPULATION,
        InjectionSeverity.HIGH,
    ),
    (
        re.compile(r"respond\s+(as|like)\s+(a\s+)?(human|person|friend|buddy)", re.IGNORECASE),
        InjectionCategory.ROLE_MANIPULATION,
        InjectionSeverity.MEDIUM,
    ),
    (
        re.compile(
            r"(your\s+)?(restrictions?|limitations?|constraints?|rules?|policy)\s+(have\s+been|are)\s+(removed|lifted|gone|disabled|overridden)",
            re.IGNORECASE,
        ),
        InjectionCategory.JAILBREAK,
        InjectionSeverity.CRITICAL,
    ),
    (
        re.compile(r"<\|im_start\|>|<\|(?:system|user|assistant)\|>", re.IGNORECASE),
        InjectionCategory.JAILBREAK,
        InjectionSeverity.CRITICAL,
    ),
    (
        re.compile(r"<\|endoftext\|>|\[INST\]|\[/INST\]|<\|endofprompt\|>", re.IGNORECASE),
        InjectionCategory.JAILBREAK,
        InjectionSeverity.CRITICAL,
    ),
    (
        re.compile(r"---CUT\s+HERE---|---.*HERE---|---.*END---", re.IGNORECASE),
        InjectionCategory.DIRECT_PROMPT,
        InjectionSeverity.MEDIUM,
    ),
    (
        re.compile(r"^(system|user|assistant)\s*:", re.IGNORECASE | re.MULTILINE),
        InjectionCategory.ROLE_MANIPULATION,
        InjectionSeverity.HIGH,
    ),
    (
        re.compile(
            r"(print|display|show|reveal|output)\s+(the\s+)?(system\s+prompt|your\s+instructions|your\s+prompt|original\s+prompt)",
            re.IGNORECASE,
        ),
        InjectionCategory.DIRECT_PROMPT,
        InjectionSeverity.CRITICAL,
    ),
    (
        re.compile(r"token\s*smuggling|continuation\s*attack", re.IGNORECASE),
        InjectionCategory.JAILBREAK,
        InjectionSeverity.CRITICAL,
    ),
]


_ENCODING_DECODERS: dict[str, Any] = {
    "base64": lambda x: base64.b64decode(x, validate=True).decode("utf-8", errors="ignore"),
    "hex": lambda x: bytes.fromhex(x).decode("utf-8", errors="ignore"),
    "rot13": lambda x: codecs.decode(x, "rot_13"),
    "url": urllib.parse.unquote,
    "base32": lambda x: base64.b32decode(x, casefold=True).decode("utf-8", errors="ignore"),
}


_JS_EVAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"eval\s*\(\s*(atob|String\.fromCharCode|unescape|decodeURIComponent)"),
    re.compile(r"new\s+Function\s*\(.*?\)", re.DOTALL),
    re.compile(r"Function\s*\(\s*[\"'].*?[\"']\s*\)\s*\(\)"),
    re.compile(r"document\.write\s*\(\s*unescape\s*\(.*?\)\s*\)"),
    re.compile(r"setTimeout\s*\(\s*[\"'][^\"']+[\"']\s*,\s*\d+\s*\)"),
    re.compile(r"\[\s*[\"']constructor[\"']\s*\]\s*\[\s*[\"']constructor[\"']\s*\]"),
    re.compile(r"\(function\s*\(\s*\)\s*{\s*}.constructor\s*\(\s*[\"'].*?[\"']\s*\)\s*\(\s*\)"),
    re.compile(r"__proto__|constructor\.constructor"),
]


@dataclass
class InjectionFinding:
    category: InjectionCategory
    severity: InjectionSeverity
    match: str
    position: int
    encoding_layer: str = ""
    decoded_from: str = ""
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
            "category": self.category.value,
            "severity": self.severity.value,
            "match": self.match,
            "position": self.position,
        }
        if self.encoding_layer:
            d["encoding_layer"] = self.encoding_layer
        if self.decoded_from:
            d["decoded_from"] = self.decoded_from
        if self.source_path:
            d["source_path"] = self.source_path
        return d


@dataclass
class ScanReport:
    findings: list[InjectionFinding] = field(default_factory=list)
    overall_severity: InjectionSeverity = InjectionSeverity.INFO
    encoding_layers_detected: int = 0
    ast_findings: list[str] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
            "findings": [f.to_dict() for f in self.findings],
            "overall_severity": self.overall_severity.value,
            "encoding_layers_detected": self.encoding_layers_detected,
            "ast_findings": self.ast_findings,
            "finding_count": len(self.findings),
        }
        if self.source_path:
            d["source_path"] = self.source_path
        return d


def scan_ascii(data: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for pattern, category, severity in PROMPT_INJECTION_PATTERNS:
        for match in pattern.finditer(data):
            findings.append({
                "category": category.value,
                "severity": severity.value,
                "match": match.group(),
                "position": match.start(),
            })
    return findings


def scan_hex(data: bytes) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    try:
        ascii_data = data.decode("ascii", errors="ignore")
        findings = scan_ascii(ascii_data)
    except Exception:
        pass

    hex_str = data.hex()
    for encoding_name, decoder in _ENCODING_DECODERS.items():
        if encoding_name in ("hex",):
            continue
    try:
        decoded = bytes.fromhex(hex_str).decode("utf-8", errors="ignore")
        hex_findings = scan_ascii(decoded)
        for f in hex_findings:
            f["encoding_layer"] = "hex"
            findings.append(f)
    except (ValueError, UnicodeDecodeError):
        pass

    return findings


def scan_base64(data: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    ascii_findings = scan_ascii(data)
    findings.extend(ascii_findings)

    base64_pattern = re.compile(r"[A-Za-z0-9+/=]{8,}")
    for b64_match in base64_pattern.finditer(data):
        b64_str = b64_match.group().strip()
        try:
            decoded = base64.b64decode(b64_str, validate=True).decode("utf-8", errors="ignore")
            decoded_findings = scan_ascii(decoded)
            for f in decoded_findings:
                f["encoding"] = "base64"
                f["decoded_from"] = b64_str[:60]
                findings.append(f)
        except Exception:
            pass

    return findings


def scan_url_encoded(data: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []

    ascii_findings = scan_ascii(data)
    findings.extend(ascii_findings)

    try:
        fully_decoded = urllib.parse.unquote(data)
        if fully_decoded != data:
            decoded_findings = scan_ascii(fully_decoded)
            for f in decoded_findings:
                f["encoding"] = "url"
                findings.append(f)
    except Exception:
        pass

    url_encoded_pattern = re.compile(r"(%[0-9A-Fa-f]{2})+")
    for url_match in url_encoded_pattern.finditer(data):
        encoded_str = url_match.group()
        if len(encoded_str) < 6:
            continue
        try:
            decoded = urllib.parse.unquote(encoded_str)
            if decoded != encoded_str:
                decoded_findings = scan_ascii(decoded)
                for f in decoded_findings:
                    if f not in findings:
                        f["encoding"] = "url"
                        f["decoded_from"] = encoded_str[:60]
                        findings.append(f)
        except Exception:
            pass

    return findings


def scan_rot13(data: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    try:
        rotated = codecs.decode(data, "rot_13")
        if rotated != data:
            rot_findings = scan_ascii(rotated)
            for f in rot_findings:
                f["encoding"] = "rot13"
                findings.append(f)
    except Exception:
        pass
    return findings


def analyze_js_ast(source: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []

    for pattern in _JS_EVAL_PATTERNS:
        for match in pattern.finditer(source):
            findings.append({
                "category": InjectionCategory.EVAL_INJECTION.value,
                "severity": InjectionSeverity.HIGH.value,
                "match": match.group()[:120],
                "position": match.start(),
                "encoding_layer": "javascript",
                "pattern": pattern.pattern[:80],
            })

    try:
        import esprima
        tree = esprima.parseScript(source, {"range": True, "tolerant": True})
        findings.extend(_walk_esprima_tree(tree, source))
    except ImportError:
        pass
    except Exception:
        pass

    return findings


def _walk_esprima_tree(node: Any, source: str, depth: int = 0) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    if node is None or depth > 20:
        return findings

    if isinstance(node, dict):
        node_type = node.get("type", "")
        if node_type in ("CallExpression", "NewExpression"):
            callee = node.get("callee", {})
            callee_name = ""
            if isinstance(callee, dict):
                if callee.get("type") == "Identifier":
                    callee_name = callee.get("name", "")
                elif callee.get("type") == "MemberExpression":
                    prop = callee.get("property", {})
                    if isinstance(prop, dict):
                        callee_name = prop.get("name", "")
            if callee_name.lower() in ("eval", "function"):
                if node.get("range"):
                    start, end = node["range"]
                    snippet = source[start:end] if isinstance(source, str) else str(node)[:120]
                else:
                    snippet = str(node)[:120]
                findings.append({
                    "category": InjectionCategory.EVAL_INJECTION.value,
                    "severity": InjectionSeverity.HIGH.value,
                    "match": snippet[:120],
                    "position": node.get("range", [0])[0] if node.get("range") else 0,
                    "encoding_layer": "javascript_ast",
                })

        for _key, value in node.items():
            if isinstance(value, (dict, list)):
                findings.extend(_walk_esprima_tree(value, source, depth + 1))
    elif isinstance(node, list):
        for item in node:
            findings.extend(_walk_esprima_tree(item, source, depth + 1))

    return findings


def analyze_python_ast(source: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    try:
        tree = ast.parse(source)
        findings.extend(_walk_python_ast(tree, source))
    except SyntaxError:
        pass
    return findings


def _walk_python_ast(tree: ast.AST, source: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            func_name = ""
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr

            if func_name in ("eval", "exec", "compile"):
                evidence = f"{func_name}("
                if hasattr(node, "lineno"):
                    evidence += f" at line {node.lineno}"
                findings.append({
                    "category": InjectionCategory.EVAL_INJECTION.value,
                    "severity": InjectionSeverity.CRITICAL.value,
                    "match": f"{evidence})",
                    "position": node.lineno if hasattr(node, "lineno") else 0,
                    "encoding_layer": "python_ast",
                })

            args = getattr(node, "args", [])
            has_var_args = False
            for a in args:
                if isinstance(a, (ast.BinOp, ast.JoinedStr, ast.Call)):
                    has_var_args = True
                    break
            if func_name in ("eval", "exec") and has_var_args:
                findings.append({
                    "category": InjectionCategory.EVAL_INJECTION.value,
                    "severity": InjectionSeverity.CRITICAL.value,
                    "match": f"{func_name} with dynamic arguments at line {getattr(node, 'lineno', 0)}",
                    "position": node.lineno if hasattr(node, "lineno") else 0,
                    "encoding_layer": "python_ast_dynamic",
                })

    return findings


def score_severity(findings: list[dict[str, object]]) -> InjectionSeverity:
    if not findings:
        return InjectionSeverity.INFO

    severities = {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
        "info": 0,
    }

    max_score = 0
    has_jailbreak = False
    has_eval = False
    encoding_layers = 0

    for f in findings:
        severity_val = str(f.get("severity", "info"))
        score = severities.get(severity_val, 0)
        if score > max_score:
            max_score = score

        category = str(f.get("category", ""))
        if category in ("jailbreak",) or "jailbreak" in category:
            has_jailbreak = True
        if category in ("eval_injection", "base64_injection"):
            has_eval = True
        if f.get("encoding_layer") or f.get("encoding"):
            encoding_layers += 1

    if has_jailbreak or has_eval:
        return InjectionSeverity.CRITICAL

    if encoding_layers >= 3:
        return InjectionSeverity.CRITICAL

    if max_score >= 3:
        return InjectionSeverity.HIGH
    if max_score >= 2:
        return InjectionSeverity.MEDIUM
    if len(findings) >= 3:
        return InjectionSeverity.MEDIUM
    if max_score >= 1:
        return InjectionSeverity.LOW

    return InjectionSeverity.INFO


def scan_text(
    text: str,
    check_encodings: bool = True,
    check_js: bool = False,
    check_python: bool = False,
) -> ScanReport:
    report = ScanReport()
    import time
    start = time.time()

    for pattern, category, severity in PROMPT_INJECTION_PATTERNS:
        for match in pattern.finditer(text):
            report.findings.append(InjectionFinding(
                category=category,
                severity=severity,
                match=match.group(),
                position=match.start(),
            ))

    if check_encodings:
        encoding_findings = scan_base64(text)
        for f in encoding_findings:
            report.findings.append(InjectionFinding(
                category=InjectionCategory(
                    f.get("encoding", "base64_injection")
                    if f.get("encoding") == "base64" else f.get("category", "")
                ) if f.get("encoding") == "base64" else category,
                severity=InjectionSeverity(f.get("severity", "medium")),
                match=str(f.get("match", "")),
                position=int(f.get("position", 0)),
                encoding_layer=str(f.get("encoding", "")),
                decoded_from=str(f.get("decoded_from", "")),
            ))

        url_findings = scan_url_encoded(text)
        for f in url_findings:
            if f.get("encoding") == "url":
                report.findings.append(InjectionFinding(
                    category=InjectionCategory(
                        f.get("category", "direct_prompt")
                    ),
                    severity=InjectionSeverity(f.get("severity", "medium")),
                    match=str(f.get("match", "")),
                    position=int(f.get("position", 0)),
                    encoding_layer="url",
                    decoded_from=str(f.get("decoded_from", "")),
                ))

    if check_js:
        js_findings = analyze_js_ast(text)
        for f in js_findings:
            report.findings.append(InjectionFinding(
                category=InjectionCategory(f.get("category", "eval_injection")),
                severity=InjectionSeverity(f.get("severity", "high")),
                match=str(f.get("match", "")),
                position=int(f.get("position", 0)),
                encoding_layer=str(f.get("encoding_layer", "javascript")),
            ))

    if check_python:
        py_findings = analyze_python_ast(text)
        for f in py_findings:
            report.findings.append(InjectionFinding(
                category=InjectionCategory(f.get("category", "eval_injection")),
                severity=InjectionSeverity(f.get("severity", "critical")),
                match=str(f.get("match", "")),
                position=int(f.get("position", 0)),
                encoding_layer=str(f.get("encoding_layer", "python_ast")),
            ))

    report.scan_duration_ms = (time.time() - start) * 1000

    findings_dict = [f.to_dict() for f in report.findings]
    report.overall_severity = score_severity(findings_dict)

    return report


def scan_file(path: str | Path) -> ScanReport:
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in (".js", ".mjs", ".cjs"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        return scan_text(text, check_encodings=True, check_js=True, check_python=False)
    elif suffix in (".py", ".pyw"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        return scan_text(text, check_encodings=True, check_js=False, check_python=True)
    elif suffix in (".html", ".htm", ".xhtml"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        return scan_text(text, check_encodings=True, check_js=True, check_python=False)
    else:
        text = p.read_text(encoding="utf-8", errors="ignore")
        return scan_text(text, check_encodings=True, check_js=False, check_python=False)


def scan_binary(path: str | Path) -> ScanReport:
    p = Path(path)
    data = p.read_bytes()

    report = ScanReport()

    for sig_marker in [b"DAN", b"jailbreak", b"system prompt override",
                        b"ignore previous instructions", b"as DAN",
                        b"developer mode", b"token smuggling"]:
        if sig_marker.lower() in data.lower():
            report.findings.append(InjectionFinding(
                category=InjectionCategory.JAILBREAK,
                severity=InjectionSeverity.HIGH,
                match=sig_marker.decode("ascii", errors="ignore"),
                position=data.lower().find(sig_marker.lower()),
                encoding_layer="binary_raw",
                source_path=str(p),
            ))

    try:
        text = data.decode("utf-8", errors="ignore")
        text_report = scan_text(text, check_encodings=True, check_js=False, check_python=False)
        for f in text_report.findings:
            f.source_path = str(p)
            report.findings.append(f)
    except Exception:
        pass

    base64_pattern = re.compile(rb"[A-Za-z0-9+/]{40,}={0,2}")
    for b64_match in base64_pattern.finditer(data):
        b64_str = b64_match.group()
        try:
            decoded = base64.b64decode(b64_str, validate=True).decode("utf-8", errors="ignore")
            decoded_report = scan_text(decoded, check_encodings=True)
            for f in decoded_report.findings:
                f.encoding_layer = "binary_base64"
                f.source_path = str(p)
                report.findings.append(f)
        except Exception:
            pass

    findings_dict = [f.to_dict() for f in report.findings]
    report.overall_severity = score_severity(findings_dict)
    report.source_path = str(p)

    return report
