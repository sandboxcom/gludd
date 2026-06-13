from __future__ import annotations

from pathlib import Path
from typing import Any

import tree_sitter_python as tspython
from pydantic import BaseModel, field_validator, model_validator
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tspython.language())

KIND_PRIORITY: dict[str, int] = {
    "class": 0,
    "function": 1,
    "method": 1,
    "variable": 2,
    "import": 3,
}


class CodeSymbol(BaseModel):
    name: str
    kind: str
    file_path: str
    line_start: int
    line_end: int
    parent: str | None = None

    @field_validator("name", "file_path", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v

    @field_validator("line_start")
    @classmethod
    def _line_start_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("line_start must be non-negative")
        return v

    @field_validator("line_end")
    @classmethod
    def _line_end_check(cls, v: int, info: Any) -> int:
        return v

    @model_validator(mode="after")
    def _lines_consistent(self) -> CodeSymbol:
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class RepoMap(BaseModel):
    symbols: list[CodeSymbol] = []
    file_count: int = 0
    total_lines: int = 0

    def add_symbol(self, symbol: CodeSymbol) -> None:
        self.symbols.append(symbol)

    def get_symbols_for_file(self, file_path: str) -> list[CodeSymbol]:
        return [s for s in self.symbols if s.file_path == file_path]

    def get_top_symbols(self, n: int = 50) -> list[CodeSymbol]:
        builder = RepoMapBuilder()
        return builder._rank_symbols(self.symbols, n)

    def to_compact_string(self) -> str:
        if not self.symbols:
            return ""
        files: dict[str, list[CodeSymbol]] = {}
        for sym in self.symbols:
            files.setdefault(sym.file_path, []).append(sym)
        lines: list[str] = []
        for file_path, syms in sorted(files.items()):
            lines.append(f"{file_path}:")
            syms_sorted = sorted(syms, key=lambda s: s.line_start)
            classes: dict[str, list[CodeSymbol]] = {}
            top_level: list[CodeSymbol] = []
            for s in syms_sorted:
                if s.parent:
                    classes.setdefault(s.parent, []).append(s)
                else:
                    top_level.append(s)
            for s in top_level:
                if s.kind == "class":
                    lines.append(f"  class {s.name} (L{s.line_start}-{s.line_end})")
                    for m in classes.get(s.name, []):
                        lines.append(f"    method {m.name}() (L{m.line_start}-{m.line_end})")
                elif s.kind == "function":
                    lines.append(f"  function {s.name}() (L{s.line_start}-{s.line_end})")
                elif s.kind == "variable":
                    lines.append(f"  variable {s.name} (L{s.line_start}-{s.line_end})")
                elif s.kind == "import":
                    lines.append(f"  import {s.name} (L{s.line_start}-{s.line_end})")
                else:
                    lines.append(f"  {s.kind} {s.name} (L{s.line_start}-{s.line_end})")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepoMap:
        symbols_data = data.get("symbols", [])
        symbols = [CodeSymbol(**s) for s in symbols_data]
        return cls(
            symbols=symbols,
            file_count=data.get("file_count", 0),
            total_lines=data.get("total_lines", 0),
        )


class RepoMapBuilder:
    def __init__(self, language: str = "python") -> None:
        self._language_name = language
        self._parser = Parser(PY_LANGUAGE)

    def parse_file(self, file_path: str, content: str) -> list[CodeSymbol]:
        tree = self._parser.parse(bytes(content, "utf8"))
        symbols: list[CodeSymbol] = []
        self._walk_node(tree.root_node, file_path, content, symbols, parent_class=None)
        return symbols

    def build_from_directory(self, root_path: str, max_files: int = 500) -> RepoMap:
        repo_map = RepoMap()
        root = Path(root_path)
        py_files = sorted(root.rglob("*.py"))[:max_files]
        repo_map.file_count = len(py_files)
        total_lines = 0
        all_symbols: list[CodeSymbol] = []
        for py_file in py_files:
            rel = str(py_file.relative_to(root))
            try:
                text = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            total_lines += text.count("\n") + 1
            file_symbols = self.parse_file(rel, text)
            all_symbols.extend(file_symbols)
        for s in all_symbols:
            repo_map.add_symbol(s)
        repo_map.total_lines = total_lines
        return repo_map

    def _rank_symbols(self, symbols: list[CodeSymbol], n: int) -> list[CodeSymbol]:
        name_counts: dict[str, int] = {}
        for s in symbols:
            name_counts[s.name] = name_counts.get(s.name, 0) + 1
        ranked = sorted(
            symbols,
            key=lambda s: (
                KIND_PRIORITY.get(s.kind, 99),
                -name_counts.get(s.name, 0),
                -(s.line_end - s.line_start),
            ),
        )
        return ranked[:n]

    def _walk_node(
        self,
        node: Any,
        file_path: str,
        content: str,
        symbols: list[CodeSymbol],
        parent_class: str | None,
    ) -> None:
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type in ("function_definition", "class_definition"):
                    self._walk_node(child, file_path, content, symbols, parent_class)
            return

        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = name_node.text.decode("utf-8")
                sym = CodeSymbol(
                    name=name,
                    kind="class",
                    file_path=file_path,
                    line_start=node.start_point[0],
                    line_end=node.end_point[0],
                )
                symbols.append(sym)
                for child in node.children:
                    if child.type == "block":
                        self._walk_block(child, file_path, content, symbols, parent_class=name)
                return

        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = name_node.text.decode("utf-8")
                kind = "method" if parent_class else "function"
                sym = CodeSymbol(
                    name=name,
                    kind=kind,
                    file_path=file_path,
                    line_start=node.start_point[0],
                    line_end=node.end_point[0],
                    parent=parent_class,
                )
                symbols.append(sym)
            return

        if node.type == "import_statement":
            self._extract_import(node, file_path, symbols)
            return

        if node.type == "import_from_statement":
            self._extract_import(node, file_path, symbols)
            return

        for child in node.children:
            self._walk_node(child, file_path, content, symbols, parent_class)

    def _walk_block(
        self,
        node: Any,
        file_path: str,
        content: str,
        symbols: list[CodeSymbol],
        parent_class: str,
    ) -> None:
        for child in node.children:
            if child.type == "decorated_definition":
                for dec_child in child.children:
                    if dec_child.type == "function_definition":
                        self._walk_node(dec_child, file_path, content, symbols, parent_class)
            elif child.type == "function_definition":
                self._walk_node(child, file_path, content, symbols, parent_class)

    def _extract_import(self, node: Any, file_path: str, symbols: list[CodeSymbol]) -> None:
        for child in node.children:
            if child.type == "dotted_name":
                name = child.text.decode("utf-8")
                sym = CodeSymbol(
                    name=name,
                    kind="import",
                    file_path=file_path,
                    line_start=node.start_point[0],
                    line_end=node.end_point[0],
                )
                symbols.append(sym)
                return
            if child.type == "aliased_import":
                for sub in child.children:
                    if sub.type == "dotted_name":
                        name = sub.text.decode("utf-8")
                        sym = CodeSymbol(
                            name=name,
                            kind="import",
                            file_path=file_path,
                            line_start=node.start_point[0],
                            line_end=node.end_point[0],
                        )
                        symbols.append(sym)
                        return
        if node.type == "import_from_statement":
            last_identifier = None
            for child in node.children:
                if child.type == "dotted_name":
                    last_identifier = child.text.decode("utf-8")
                if child.type == "identifier":
                    last_identifier = child.text.decode("utf-8")
                if child.type == "wildcard_import":
                    last_identifier = "*"
            if last_identifier:
                sym = CodeSymbol(
                    name=last_identifier,
                    kind="import",
                    file_path=file_path,
                    line_start=node.start_point[0],
                    line_end=node.end_point[0],
                )
                symbols.append(sym)
