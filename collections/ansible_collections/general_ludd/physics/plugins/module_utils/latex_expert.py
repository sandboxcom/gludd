"""LaTeX generation helpers."""
from __future__ import annotations

from pathlib import Path


class LatexConfig:
    def __init__(
        self,
        document_class: str = "article",
        font_size: str = "11pt",
        title: str = "",
        author: str = "",
        output_format: str = "tex",
    ) -> None:
        self.document_class = document_class
        self.font_size = font_size
        self.title = title
        self.author = author
        self.output_format = output_format


def generate_paper(config: LatexConfig) -> str:
    return "\n".join([
        f"\\documentclass[{config.font_size}]{{{config.document_class}}}",
        f"\\title{{{config.title}}}",
        f"\\author{{{config.author}}}",
        "\\begin{document}",
        "\\maketitle",
        "\\section{Summary}",
        "Generated physics manuscript scaffold.",
        "\\end{document}",
    ])


def render_equation(equation: str, label: str | None = None) -> str:
    label_line = f"\\label{{{label}}}" if label else ""
    return f"\\begin{{equation}}\n{equation}\n{label_line}\n\\end{{equation}}"


def render_align(lines: list[str]) -> str:
    slash = chr(92)
    body = (" " + slash + slash + chr(10)).join(lines)
    return f"\\begin{{align}}\n{body}\n\\end{{align}}"


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    slash = chr(92)
    spec = "l" * len(headers)
    row_end = " " + slash + slash
    header = " & ".join(headers) + row_end
    body = chr(10).join(" & ".join(row) + row_end for row in rows)
    return f"\\begin{{tabular}}{{{spec}}}\n{header}\n{body}\n\\end{{tabular}}"


def write_latex_output(content: str, output_dir: str, filename: str = "paper.tex") -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / filename
    path.write_text(content)
    return path
