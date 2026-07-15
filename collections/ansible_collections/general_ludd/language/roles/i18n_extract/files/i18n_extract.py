#!/usr/bin/env python3
"""i18n_extract — Extract translatable strings from source code.

Scans source files for gettext-wrapped strings, generates .pot template,
optionally pseudolocalizes, and lints for hardcoded user-facing strings.
"""

from __future__ import annotations

import argparse
import glob as glob_mod
import json
import os
import re
import sys


GETTEXT_RE = re.compile(
    r"(?:_|gettext|ngettext|pgettext)\s*\(\s*[\"'](.+?)[\"']", re.DOTALL
)
HARDCODED_RE = re.compile(
    r"[\"']([A-Z][a-z].*?)[\"']", re.DOTALL
)
PLACEHOLDER_RE = re.compile(
    r"(?<!%)%(?:\(.*?\))?[sdfg]|\{[^}]*\}"
)


def _collect_strings(source_dir: str, patterns: list[str]) -> tuple[list[dict[str, str]], int]:
    strings: list[dict[str, str]] = []
    files_scanned = 0
    for pat in patterns:
        for fpath in glob_mod.glob(os.path.join(source_dir, "**", pat), recursive=True):
            files_scanned += 1
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    content = f.read()
                for match in GETTEXT_RE.findall(content):
                    strings.append({"file": fpath, "string": match})
            except OSError:
                pass
    return strings, files_scanned


def _write_pot(strings: list[dict[str, str]], pot_path: str) -> None:
    seen: set[str] = set()
    with open(pot_path, "w", encoding="utf-8") as f:
        f.write('msgid ""\nmsgstr ""\n')
        f.write('"Content-Type: text/plain; charset=UTF-8\\n"\n\n')
        for entry in strings:
            s = entry["string"]
            if s not in seen:
                seen.add(s)
                f.write(f'#: {entry["file"]}\n')
                f.write(f'msgid "{s}"\nmsgstr ""\n\n')


def _write_pseudoloc(strings: list[dict[str, str]], po_path: str) -> None:
    seen: set[str] = set()
    with open(po_path, "w", encoding="utf-8") as f:
        f.write("# Pseudolocalized by general_ludd.language.i18n_extract\n\n")
        for entry in strings:
            s = entry["string"]
            if s not in seen:
                seen.add(s)
                pseudo = "[" + s + "]"
                f.write(f'#: {entry["file"]}\n')
                f.write(f'msgid "{s}"\nmsgstr "{pseudo}"\n\n')


def _lint_hardcoded(source_dir: str, patterns: list[str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for pat in patterns:
        for fpath in glob_mod.glob(os.path.join(source_dir, "**", pat), recursive=True):
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    content = f.read()
                for match in HARDCODED_RE.finditer(content):
                    s = match.group(1)
                    if len(s) > 5 and " " in s:
                        line = content[:match.start()].count("\n") + 1
                        findings.append({
                            "file": fpath,
                            "line": line,
                            "string": s[:60],
                            "issue": "Possible hardcoded user-facing string not wrapped in gettext",
                        })
            except OSError:
                pass
    return findings


def extract(args: argparse.Namespace) -> dict[str, object]:
    source_dir = args.source_dir
    output_dir = args.output_dir
    pot_file = args.pot_file

    result: dict[str, object] = {
        "string_count": 0,
        "files_scanned": 0,
        "pot_path": "",
        "strings": [],
    }

    if not source_dir or not os.path.isdir(source_dir):
        result["error"] = f"Source directory not found: {source_dir}"
        return result

    patterns = args.source_patterns
    strings, files_scanned = _collect_strings(source_dir, patterns)
    result["files_scanned"] = files_scanned
    result["string_count"] = len(strings)
    result["strings"] = strings[:500]

    pot_path = os.path.join(output_dir, pot_file)
    os.makedirs(os.path.dirname(pot_path) or ".", exist_ok=True)
    _write_pot(strings, pot_path)
    result["pot_path"] = pot_path

    lint_findings: list[dict[str, object]] = []

    if args.lint_placeholders:
        for entry in strings:
            if PLACEHOLDER_RE.search(entry["string"]):
                lint_findings.append({
                    "file": entry["file"],
                    "string": entry["string"],
                    "issue": "Format placeholder detected in translatable string",
                })

    if args.pseudolocalize:
        pseudoloc_locale = args.pseudoloc_locale
        po_path = os.path.join(output_dir, f"{pseudoloc_locale}.po")
        _write_pseudoloc(strings, po_path)
        result["pseudoloc_po"] = po_path

    if args.lint_hardcoded and source_dir:
        hardcoded_findings = _lint_hardcoded(source_dir, patterns)
        lint_findings.extend(hardcoded_findings)

    result["lint_findings"] = lint_findings[:100]

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract translatable strings from source code"
    )
    parser.add_argument("--source-dir", default="", help="Source directory to scan")
    parser.add_argument("--output-dir", default=".", help="Directory for .pot output")
    parser.add_argument("--output", default="-", help="Output JSON report path (default: stdout)")
    parser.add_argument("--format", default="json", choices=["json"], help="Output format")
    parser.add_argument("--source-patterns", nargs="*",
                        default=["*.py", "*.js", "*.ts", "*.jsx", "*.tsx"])
    parser.add_argument("--pot-file", default="messages.pot", help="Name of .pot template file")
    parser.add_argument("--pseudolocalize", action="store_true", default=False)
    parser.add_argument("--pseudoloc-locale", default="qps-ploc")
    parser.add_argument("--lint-placeholders", action="store_true", default=False)
    parser.add_argument("--lint-hardcoded", action="store_true", default=False)

    args = parser.parse_args()

    try:
        result = extract(args)
    except Exception as exc:
        result = {"error": str(exc)}

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output == "-":
        print(output)
    else:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Report: {args.output}")

    sys.exit(0)


if __name__ == "__main__":
    main()
