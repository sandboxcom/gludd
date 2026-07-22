"""Compatibility helpers for LaTeX CLI workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LatexConfig:
    document_class: str
    font_size: str
    title: str
    author: str


def generate_paper(config: LatexConfig) -> str:
    return "\n".join([
        f"\\documentclass[{config.font_size}]{{{config.document_class}}}",
        f"\\title{{{config.title}}}",
        f"\\author{{{config.author}}}",
        "\\begin{document}",
        "\\maketitle",
        "\\section{Overview}",
        "Generated analysis document.",
        "\\end{document}",
        "",
    ])


def render_equation(equation: str, label: str | None = None) -> str:
    label_line = f"\\label{{{label}}}" if label else ""
    return f"\\begin{{equation}}\n{equation}\n{label_line}\n\\end{{equation}}\n"


def render_align(lines: list[str]) -> str:
    body = " \\\\\n".join(lines)
    return f"\\begin{{align}}\n{body}\n\\end{{align}}\n"


def render_table(rows: list[list[Any]]) -> str:
    body = "\n".join(" & ".join(str(cell) for cell in row) + r" \\" for row in rows)
    return f"\\begin{{tabular}}{{lll}}\n{body}\n\\end{{tabular}}\n"


def write_latex_output(content: str, output_dir: str | Path, filename: str = "paper.tex") -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / filename
    out.write_text(content, encoding="utf-8")
    return out
