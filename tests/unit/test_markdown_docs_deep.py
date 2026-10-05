"""Deep documentation integrity tests for all .md files in docs/ and root.

Verifies: internal links resolve, no broken local image references,
all code blocks have language tags, headers are properly nested,
and related structural invariants (19 tests).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = ROOT / "docs"

EXCLUDE_PREFIXES = {
    str(ROOT / "external"),
    str(DOCS / "archive"),
}


def _md_files() -> list[Path]:
    paths: list[Path] = []
    for p in ROOT.glob("*.md"):
        paths.append(p)
    for p in DOCS.rglob("*.md"):
        if not any(str(p).startswith(prefix) for prefix in EXCLUDE_PREFIXES):
            paths.append(p)
    return sorted(paths)


MD_FILES = _md_files()


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split("\n")


# ── region helpers ──────────────────────────────────────────────────


def _fenced_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """Return (start_line, end_line) pairs for fenced code blocks."""
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if start is None:
                start = i
            else:
                ranges.append((start, i))
                start = None
    if start is not None:
        ranges.append((start, len(lines)))
    return ranges


def _in_fence(line_no: int, ranges: list[tuple[int, int]]) -> bool:
    return any(s <= line_no <= e for s, e in ranges)


# ── link / image extraction ────────────────────────────────────────

_LINK_RE = re.compile(
    r"""
    (?<!\!) \[ [^\]]* \] \( ([^)]+) \)  # not an image
""",
    re.VERBOSE,
)

_IMAGE_RE = re.compile(
    r"""
    !\[ [^\]]* \] \( ([^)]+) \)
""",
    re.VERBOSE,
)


def _all_links(lines: list[str]) -> list[tuple[int, str]]:
    """Return (line_number, target) for every inline link outside fenced blocks."""
    results: list[tuple[int, str]] = []
    ranges = _fenced_ranges(lines)
    for i, line in enumerate(lines, 1):
        if _in_fence(i, ranges):
            continue
        for m in _LINK_RE.finditer(line):
            target = m.group(1)
            if target.startswith("http://") or target.startswith("https://"):
                continue
            if target.startswith("mailto:") or target.startswith("irc:"):
                continue
            results.append((i, target))
    return results


def _all_images(lines: list[str]) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    ranges = _fenced_ranges(lines)
    for i, line in enumerate(lines, 1):
        if _in_fence(i, ranges):
            continue
        for m in _IMAGE_RE.finditer(line):
            target = m.group(1)
            if target.startswith("http://") or target.startswith("https://"):
                continue
            results.append((i, target))
    return results


def _all_code_blocks(lines: list[str]) -> list[tuple[int, str, str]]:
    """Return (open_line, fence_marker, lang_or_empty) for each fenced block."""
    blocks: list[tuple[int, str, str]] = []
    in_block = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_block:
                lang = stripped[3:].strip()
                blocks.append((i, stripped, lang))
                in_block = True
            else:
                in_block = False
    return blocks


def _all_headers(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return (line, level, title) for each ATX heading outside fenced blocks."""
    headers: list[tuple[int, int, str]] = []
    ranges = _fenced_ranges(lines)
    for i, line in enumerate(lines, 1):
        if _in_fence(i, ranges):
            continue
        m = re.match(r"^(#{1,6})\s+(.+)", line)
        if m:
            headers.append((i, len(m.group(1)), m.group(2).strip()))
    return headers


def _resolve(path: Path, target: str) -> Path:
    """Resolve a relative link target against the file's directory."""
    base = path.parent
    if "#" in target:
        target = target.split("#")[0]
    if not target:
        return path
    return (base / target).resolve()


# ── tests ───────────────────────────────────────────────────────────


def test_all_md_files_parsable():
    """Every .md file can be read as UTF-8 text without error."""
    for p in MD_FILES:
        try:
            p.read_text(encoding="utf-8")
        except Exception as e:
            pytest.fail(f"{p}: unable to read — {e}")


def test_all_internal_links_resolve():
    """Every relative link outside fenced blocks points to an existing file."""
    broken: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        for line_no, target in _all_links(lines):
            resolved = _resolve(p, target)
            if resolved.is_dir():
                idx = resolved / "index.md"
                if not idx.exists():
                    broken.append(f"{p}:{line_no}: [{target}] -> {resolved} (dir, no index.md)")
            elif not resolved.exists():
                broken.append(f"{p}:{line_no}: [{target}] -> {resolved}")
    assert not broken, f"{len(broken)} broken link(s):\n" + "\n".join(broken)


def test_no_broken_local_images():
    """Every local image reference outside fenced blocks must resolve."""
    broken: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        for line_no, target in _all_images(lines):
            resolved = _resolve(p, target)
            if not resolved.exists():
                broken.append(f"{p}:{line_no}: ![]({target}) -> {resolved}")
    assert not broken, f"{len(broken)} broken image(s):\n" + "\n".join(broken)


def test_all_code_blocks_have_language_tags():
    """Every fenced code block must have a language tag."""
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        for line_no, _, lang in _all_code_blocks(lines):
            if not lang:
                violations.append(f"{p}:{line_no}: bare ``` without language tag")
    assert not violations, f"{len(violations)} fenced block(s) without language tag:\n" + "\n".join(violations[:15])


def test_headers_never_skip_levels():
    """Heading levels must never jump by more than one
    (e.g. ## → #### is wrong).
    """
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        headers = _all_headers(lines)
        for idx in range(1, len(headers)):
            _l1, level1, title1 = headers[idx - 1]
            line2, level2, title2 = headers[idx]
            if level2 > level1 + 1:
                violations.append(f"{p}:{line2}: H{level1} '{title1}' → H{level2} '{title2}' (skipped H{level1 + 1})")
    assert not violations, f"{len(violations)} header-level skip(s):\n" + "\n".join(violations[:15])


def test_every_md_file_has_h1_title():
    """Every .md file should have at least one H1 heading."""
    skip = {str(ROOT / "SESSION.md")}
    missing: list[str] = []
    for p in MD_FILES:
        if str(p) in skip:
            continue
        lines = _read_lines(p)
        headers = _all_headers(lines)
        if not any(level == 1 for _, level, _ in headers):
            missing.append(str(p))
    assert not missing, f"{len(missing)} file(s) missing H1:\n" + "\n".join(missing)


def test_every_index_md_has_back_link():
    """Every nested index.md should link back to its parent index."""
    skip = {str(DOCS / "index.md")}
    missing: list[str] = []
    for p in DOCS.rglob("index.md"):
        if any(str(p).startswith(prefix) for prefix in EXCLUDE_PREFIXES):
            continue
        if str(p) in skip:
            continue
        text = p.read_text(encoding="utf-8")
        if "[Back to" not in text and "../index.md" not in text:
            missing.append(str(p))
    assert not missing, f"{len(missing)} index.md missing back-link:\n" + "\n".join(missing)


def test_no_empty_link_targets():
    """No inline link should have an empty target []() or [](#)."""
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        ranges = _fenced_ranges(lines)
        for i, line in enumerate(lines, 1):
            if _in_fence(i, ranges):
                continue
            for m in _LINK_RE.finditer(line):
                raw = m.group(1).strip()
                if raw == "" or raw == "#":
                    violations.append(f"{p}:{i}: {line.strip()[:80]}")
    assert not violations, f"{len(violations)} empty-link target(s):\n" + "\n".join(violations[:10])


def test_no_bare_urls_in_link_text():
    """Link text should not be a raw URL — use bare <url> or a descriptive
    label.
    """
    violations: list[str] = []
    _bare_url = re.compile(r"\[ (https?://[^\]]+) \] \( \1 \)", re.VERBOSE)
    for p in MD_FILES:
        lines = _read_lines(p)
        ranges = _fenced_ranges(lines)
        for i, line in enumerate(lines, 1):
            if _in_fence(i, ranges):
                continue
            if _bare_url.search(line):
                violations.append(f"{p}:{i}: {line.strip()[:80]}")
    assert not violations, f"{len(violations)} bare-URL-as-link-text(s):\n" + "\n".join(violations[:10])


def test_no_trailing_whitespace_in_md():
    """No lines in .md files should have trailing spaces."""
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        for i, line in enumerate(lines, 1):
            if line.rstrip("\n") != line.rstrip():
                violations.append(f"{p}:{i}: trailing whitespace")
    assert not violations, f"{len(violations)} trailing-whitespace line(s):\n" + "\n".join(violations[:15])


def test_tables_are_well_formed():
    """Every line that looks like a table row must have matching pipe counts
    within a contiguous table block. Pipes inside inline code spans and
    backslash-escaped pipes are cell CONTENT, not column separators, so they
    are excluded from the structural count.
    """
    violations: list[str] = []

    def _structural_pipes(line: str) -> int:
        no_code = re.sub(r"`+[^`]*`+", "", line)
        no_escaped = re.sub(r"\\\|", "P", no_code)
        return no_escaped.count("|")

    _table_row = re.compile(r"^\|.+\|$")
    for p in MD_FILES:
        lines = _read_lines(p)
        table_pipe_counts: list[tuple[int, int]] = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if _table_row.match(stripped):
                count = _structural_pipes(stripped)
                table_pipe_counts.append((i, count))
            else:
                if table_pipe_counts:
                    expected = table_pipe_counts[0][1]
                    for ln, cnt in table_pipe_counts:
                        if cnt != expected:
                            violations.append(
                                f"{p}:{ln}: {cnt} pipes, expected {expected} "
                                f"(table started at line "
                                f"{table_pipe_counts[0][0]})"
                            )
                table_pipe_counts = []
        if table_pipe_counts:
            expected = table_pipe_counts[0][1]
            for ln, cnt in table_pipe_counts:
                if cnt != expected:
                    violations.append(f"{p}:{ln}: {cnt} pipes, expected {expected}")
    assert not violations, f"{len(violations)} table-pipe mismatch(es):\n" + "\n".join(violations[:10])


def test_relative_links_do_not_escape_repo():
    """No relative link should escape the repo root."""
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        for line_no, target in _all_links(lines):
            resolved = _resolve(p, target)
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                violations.append(f"{p}:{line_no}: [{target}] -> {resolved}")
    assert not violations, f"{len(violations)} link(s) escape repo root:\n" + "\n".join(violations[:10])


def test_md_files_have_no_windows_line_endings():
    """No .md file should contain \\r\\n line endings."""
    violations: list[str] = []
    for p in MD_FILES:
        text = p.read_text(encoding="utf-8")
        if "\r\n" in text:
            violations.append(str(p))
    assert not violations, f"{len(violations)} file(s) with Windows line endings:\n" + "\n".join(violations[:10])


def test_md_files_end_with_newline():
    """Every .md file should end with a single trailing newline."""
    violations: list[str] = []
    for p in MD_FILES:
        text = p.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            violations.append(str(p))
    assert not violations, f"{len(violations)} file(s) missing final newline:\n" + "\n".join(violations[:10])


def test_no_html_comments_with_unmatched_delimiters():
    """HTML comments (<!-- ... -->) must have matching open/close pairs,
    checked only outside fenced code blocks.
    """
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        ranges = _fenced_ranges(lines)
        depth = 0
        for i, line in enumerate(lines, 1):
            if _in_fence(i, ranges):
                continue
            opens = line.count("<!--")
            closes = line.count("-->")
            depth += opens - closes
            if depth < 0:
                violations.append(f"{p}:{i}: stray '-->' without matching '<!--'")
                depth = 0
        if depth > 0:
            violations.append(f"{p}: {depth} unclosed '<!--' comment(s)")
    assert not violations, f"{len(violations)} HTML-comment issue(s):\n" + "\n".join(violations[:10])


def test_no_dead_readme_reference():
    """Links to README.md or README must resolve."""
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        for line_no, target in _all_links(lines):
            tl = target.casefold()
            if tl.endswith("readme.md") or tl.endswith("readme"):
                resolved = _resolve(p, target)
                if not resolved.exists():
                    violations.append(f"{p}:{line_no}: [{target}] -> {resolved}")
    assert not violations, f"{len(violations)} dead README reference(s):\n" + "\n".join(violations[:10])


def test_no_unbalanced_fenced_blocks():
    """Triple-backtick fences must be paired open/close."""
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        depth = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                depth = 1 if depth == 0 else 0
        if depth != 0:
            violations.append(f"{p}: unclosed fenced block")
    assert not violations, f"{len(violations)} unbalanced fence(s):\n" + "\n".join(violations[:10])


def test_link_targets_match_file_case():
    """Link targets with explicit .md extension must match the case of files
    on disk (case-sensitive FS guard).
    """
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        for line_no, target in _all_links(lines):
            target_only = target.split("#")[0]
            if not target_only:
                continue
            if not target_only.endswith((".md", ".MD", ".Md")):
                continue
            resolved = _resolve(p, target_only)
            if resolved.exists():
                actual = resolved.name
                stated = Path(target_only).name
                if actual != stated:
                    violations.append(f"{p}:{line_no}: [{target}] — link says '{stated}', file is '{actual}'")
    assert not violations, f"{len(violations)} case-mismatch link(s):\n" + "\n".join(violations[:10])


def test_no_tab_characters_in_md():
    """.md files should use spaces, not tabs, for indentation."""
    violations: list[str] = []
    for p in MD_FILES:
        lines = _read_lines(p)
        for i, line in enumerate(lines, 1):
            if "\t" in line:
                violations.append(f"{p}:{i}: tab character found")
    assert not violations, f"{len(violations)} tab-containing line(s):\n" + "\n".join(violations[:10])


def test_no_inline_html_instead_of_markdown():
    """Discourage raw HTML tags that have markdown equivalents
    (<br>, <hr>, <img>, <a href=...>).
    """
    violations: list[str] = []
    _html_tag = re.compile(r"<(br|hr)\s*/?>|<img\s|<a\s+href=", re.IGNORECASE)
    for p in MD_FILES:
        lines = _read_lines(p)
        ranges = _fenced_ranges(lines)
        for i, line in enumerate(lines, 1):
            if _in_fence(i, ranges):
                continue
            if _html_tag.search(line):
                violations.append(f"{p}:{i}: {line.strip()[:80]}")
    assert not violations, f"{len(violations)} inline-HTML-instead-of-markdown line(s):\n" + "\n".join(violations[:10])
