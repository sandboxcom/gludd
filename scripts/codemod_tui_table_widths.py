#!/usr/bin/env python3
"""Enforce adaptive Rich table widths for TUI builders."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WIDTH_EXPR = "max(term_width, 1) if term_width != 80 else None"


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


def _patch_cli_direct_tables() -> int:
    path = ROOT / "src/general_ludd/cli.py"
    content = path.read_text(encoding="utf-8")
    content = content.replace("term_width: int = 80", "term_width: int = 60")
    changed = 0
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        if "Table(title=" in line and "title_justify=\"left\")" in line:
            line = line.replace(
                "title_justify=\"left\")",
                f"title_justify=\"left\", width={WIDTH_EXPR})",
            )
            changed += 1
        elif "Table(title=" in line and "width=max(term_width, 1))" in line:
            line = line.replace("width=max(term_width, 1))", f"width={WIDTH_EXPR})")
            changed += 1
        lines.append(line)
    new_content = "".join(lines)
    if new_content != path.read_text(encoding="utf-8"):
        path.write_text(new_content, encoding="utf-8")
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
