#!/usr/bin/env python3
"""Fix mechanical markdown drift: trailing whitespace, missing final newlines,
unbalanced fences, bare fences without language tags, and stale audit-index
links. A --report mode enumerates remaining hand-fixable violations.

Usage:
    python scripts/fix_docs_drift.py             # apply mechanical fixes
    python scripts/fix_docs_drift.py --report    # enumerate hand-fixable issues
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
EXCLUDE_PREFIXES = (str(ROOT / "external"), str(DOCS / "archive"))


def _md_files() -> list[Path]:
    paths = [p for p in ROOT.glob("*.md")]
    for p in DOCS.rglob("*.md"):
        if not p.as_posix().startswith(EXCLUDE_PREFIXES):
            paths.append(p)
    return sorted(paths)


def _fence_lang(line: str, content_lines: list[str]) -> str:
    """Heuristic language for a fenced block from its content."""
    text = "\n".join(content_lines[:40])
    first = content_lines[0].strip() if content_lines else ""
    if first.startswith("#!/"):
        return "bash"
    if first == "---" and re.search(r"^[\w-]+:\s*\S", text, re.MULTILINE):
        return "yaml"
    if re.search(r"^(def |class |from \w+ import |import \w|@pytest)", text, re.MULTILINE):
        return "python"
    if re.search(r"(export (interface|function|const|class)|interface \w+ \{|const \w+ = |import \{|\b=>\b)", text):
        return "ts"
    if first.startswith("{") or first.startswith("["):
        return "json"
    if re.search(r"^(#+ )", text, re.MULTILINE):
        return "markdown"
    if re.search(r"(\$\w+|\$\{|;;|\bdo\b|\bthen\b|fi\b)", text):
        return "bash"
    return "text"


def _fix_fences(text: str) -> str:
    lines = text.split("\n")
    i = 0
    in_block = False
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            if not in_block:
                lang = stripped[3:].strip()
                if not lang:
                    j = i + 1
                    content: list[str] = []
                    while j < len(lines) and not lines[j].strip().startswith("```"):
                        content.append(lines[j])
                        j += 1
                    lines[i] = lines[i].rstrip() + _fence_lang(lines[i], content)
                    i = j
                    in_block = True
                    continue
                in_block = True
            else:
                in_block = False
        i += 1
    text = "\n".join(lines)
    depth = 0
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            depth = 1 if depth == 0 else 0
    if depth != 0:
        text = text.rstrip("\n") + "\n```\n"
    return text


def _strip_trailing_whitespace(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.split("\n"))


def _fix_final_newline(text: str) -> str:
    text = text.rstrip("\n")
    return text + "\n"


def _escape_html_comments(text: str) -> str:
    """Entity-escape HTML-comment delimiters on non-fenced lines so prose that
    discusses ``<!--``/``-->`` markers is not parsed as a live HTML comment."""
    lines = text.split("\n")
    in_block = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_block = not in_block
            out.append(line)
            continue
        if not in_block and ("<!--" in line or "-->" in line):
            line = line.replace("<!--", "&lt;!--").replace("-->", "--&gt;")
        out.append(line)
    return "\n".join(out)


def _fix_audit_index_links(text: str) -> str:
    """Rewrite docs/audit/index.md table links to exact on-disk names
    (resolving into parent dirs where the file lives elsewhere under docs/),
    and drop rows whose target no longer exists anywhere under docs/."""
    docs_files: dict[str, str] = {}
    for p in DOCS.rglob("*.md"):
        if p.as_posix().startswith(EXCLUDE_PREFIXES):
            continue
        docs_files[p.name] = p.name

    def normalize(name: str) -> str:
        return name.replace("-", "_").casefold()

    index_dir = DOCS / "audit"
    by_norm: dict[str, str] = {normalize(n): n for n in docs_files}
    new_lines: list[str] = []
    for line in text.split("\n"):
        match = re.match(r"^(\|[^|]*?\[[^\]]*\]\()([^)#]+)(#[^)]+)?(\)\s*\|.*)$", line)
        if not (match and match.group(2).endswith(".md")):
            new_lines.append(line)
            continue
        prefix, target, _anchor, suffix = match.groups()
        target_name = Path(target).name
        resolved = (index_dir / target).resolve()
        if resolved.exists() and resolved.name == target_name:
            new_lines.append(line)
            continue
        exact = by_norm.get(normalize(target_name))
        if exact is not None:
            found = next(
                (p for p in DOCS.rglob(exact) if not p.as_posix().startswith(EXCLUDE_PREFIXES)),
                None,
            )
            if found is not None:
                rel_target = os.path.relpath(found, index_dir)
                new_lines.append(f"{prefix}{rel_target}{suffix}")
                continue
        # Target no longer exists anywhere under docs/ — drop the stale row.
    return "\n".join(new_lines)


def _report(text: str, path: Path) -> list[str]:
    """Enumerate hand-fixable violations; returns lines of findings."""
    findings: list[str] = []
    lines = text.split("\n")
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            if start is None:
                start = i
            else:
                ranges.append((start, i))
                start = None
    if start is not None:
        ranges.append((start, len(lines)))

    def in_fence(no: int) -> bool:
        return any(s <= no <= e for s, e in ranges)

    for i, line in enumerate(lines, 1):
        if "\t" in line:
            findings.append(f"TAB {path}:{i}")
    table_counts: list[tuple[int, int]] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"^\|.+\|$", stripped):
            table_counts.append((i, stripped.count("|")))
        else:
            if table_counts:
                expected = table_counts[0][1]
                for ln, cnt in table_counts:
                    if cnt != expected:
                        findings.append(f"TABLE {path}:{ln} pipes={cnt} expected={expected} start={table_counts[0][0]}")
            table_counts = []
    if table_counts:
        expected = table_counts[0][1]
        for ln, cnt in table_counts:
            if cnt != expected:
                findings.append(f"TABLE {path}:{ln} pipes={cnt} expected={expected} start={table_counts[0][0]}")
    depth = 0
    for i, line in enumerate(lines, 1):
        if in_fence(i):
            continue
        opens = line.count("<!--")
        closes = line.count("-->")
        depth += opens - closes
        if depth < 0:
            findings.append(f"HTML_COMMENT {path}:{i} stray '-->'")
            depth = 0
    if depth > 0:
        findings.append(f"HTML_COMMENT {path}: {depth} unclosed")
    headers: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines, 1):
        if in_fence(i):
            continue
        m = re.match(r"^(#{1,6})\s+(.+)", line)
        if m:
            headers.append((i, len(m.group(1)), m.group(2).strip()))
    for idx in range(1, len(headers)):
        l1, level1, title1 = headers[idx - 1]
        line2, level2, title2 = headers[idx]
        if level2 > level1 + 1:
            findings.append(f"HEADER {path}:{line2} H{level1}->H{level2} '{title2}'")
    if headers and not any(level == 1 for _, level, _ in headers) and path.name != "SESSION.md":
        findings.append(f"H1_MISSING {path}")
    for i, line in enumerate(lines, 1):
        if in_fence(i):
            continue
        if re.search(r"\[ (https?://[^\]]+) \] \( \1 \)", line):
            findings.append(f"BARE_URL {path}:{i}")
        if re.search(r"<(br|hr)\s*/?>|<img\s|<a\s+href=", line, re.IGNORECASE):
            findings.append(f"INLINE_HTML {path}:{i}")
    link_re = re.compile(r"(?<!\!) \[ [^\]]* \] \( ([^)]+) \)", re.VERBOSE)
    for i, line in enumerate(lines, 1):
        if in_fence(i):
            continue
        for m in link_re.finditer(line):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "irc:")):
                continue
            tgt = target.split("#")[0]
            if not tgt:
                continue
            resolved = (path.parent / tgt).resolve()
            if resolved.is_dir():
                if not (resolved / "index.md").exists():
                    findings.append(f"LINK {path}:{i} dir-no-index [{target}]")
            elif not resolved.exists():
                findings.append(f"LINK {path}:{i} broken [{target}]")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)

    files = _md_files()
    changed: list[str] = []
    findings: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        original = text
        if not args.report:
            text = _strip_trailing_whitespace(text)
            text = _fix_fences(text)
            text = _fix_final_newline(text)
            text = _escape_html_comments(text)
            if path == DOCS / "audit" / "index.md":
                text = _fix_audit_index_links(text)
            if text != original:
                changed.append(str(path.relative_to(ROOT)))
                path.write_text(text, encoding="utf-8")
        if args.report:
            findings.extend(_report(text, path))

    if args.report:
        for finding in findings:
            print(finding)
        return 1 if findings else 0

    for rel in changed:
        print(f"fixed {rel}")
    print(f"fixed {len(changed)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
