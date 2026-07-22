#!/usr/bin/env python3
"""Enforce adaptive Rich table widths for TUI builders."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WIDTH_EXPR = "max(term_width, 1) if term_width != 80 else None"
HELPER_NAME = "_direct_tui_table"
NL = chr(10)
QUOTE = chr(34)
TITLE_JUSTIFY_LEFT = "title_justify=" + QUOTE + "left" + QUOTE

HELPER = """def _direct_tui_table(
    title: str,
    *,
    show_header: bool = True,
    term_width: int = 60,
) -> Table:
    from rich.table import Table

    return Table(
        title=title,
        show_header=show_header,
        expand=True,
        title_justify="left",
        width=max(term_width, 1) if term_width != 80 else None,
    )
"""


def _patch_shared_helper() -> bool:
    path = ROOT / "src/general_ludd/tui/tables.py"
    base = """    t = Table(
        title=title,
        show_header=show_header,
        expand=True,
        title_justify="left",
"""
    without_width = base + """    )
"""
    forced_width = base + """        width=max(term_width, 1),
    )
"""
    adaptive_width = base + f"""        width={WIDTH_EXPR},
    )
"""
    content = path.read_text(encoding="utf-8")
    original = content
    content = content.replace("term_width: int = 80", "term_width: int = 60")
    if forced_width in content:
        content = content.replace(forced_width, adaptive_width)
    elif without_width in content:
        content = content.replace(without_width, adaptive_width)
    elif adaptive_width not in content:
        raise SystemExit(f"shared helper pattern not found in {path}")
    if content != original:
        path.write_text(content, encoding="utf-8")
        return True
    return False


def _format_direct_table(title: str, show_header: str) -> str:
    return (
        "    t = _direct_tui_table(" + NL
        + f"        {title}," + NL
        + f"        show_header={show_header}," + NL
        + "        term_width=term_width," + NL
        + "    )" + NL
    )


def _replace_direct_table_line(line: str) -> tuple[str, bool]:
    stripped = line.strip()
    if not stripped.startswith("t = Table(title="):
        return line, False
    if TITLE_JUSTIFY_LEFT not in stripped:
        return line, False
    show_marker = ", show_header="
    expand_marker = ", expand=True"
    title_start = len("t = Table(title=")
    show_at = stripped.index(show_marker, title_start)
    title = stripped[title_start:show_at]
    header_start = show_at + len(show_marker)
    header_end = stripped.index(expand_marker, header_start)
    show_header = stripped[header_start:header_end]
    return _format_direct_table(title, show_header), True


def _ensure_direct_helper(content: str) -> str:
    if f"def {HELPER_NAME}" in content:
        return content
    marker = NL + "def _build_mcp_table("
    if marker not in content:
        raise SystemExit("direct table insertion marker not found in cli.py")
    return content.replace(marker, NL + HELPER + NL + marker, 1)


def _patch_cli_direct_tables() -> int:
    path = ROOT / "src/general_ludd/cli.py"
    content = path.read_text(encoding="utf-8")
    original = content
    content = content.replace("term_width: int = 80", "term_width: int = 60")
    content = _ensure_direct_helper(content)

    changed = 0
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        replacement, did_change = _replace_direct_table_line(line)
        if did_change:
            changed += 1
        lines.append(replacement)
    content = "".join(lines)
    content = content.replace(
        "    from rich.table import Table" + NL + NL + "    t = _direct_tui_table(",
        "    t = _direct_tui_table(",
    )
    if content != original:
        path.write_text(content, encoding="utf-8")
    return changed


def main() -> int:
    shared_changed = _patch_shared_helper()
    direct_changed = _patch_cli_direct_tables()
    print(
        "codemod-tui-table-widths: "
        f"shared={int(shared_changed)} direct={direct_changed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
