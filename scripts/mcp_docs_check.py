#!/usr/bin/env python3
"""CI guard: every gludd_* Ansible module must carry MCP-usable documentation.

This is the presence guard that keeps the generated MCP surface complete. For
each ``plugins/modules/gludd_*.py`` module it asserts:

  * the module has a parseable DOCUMENTATION block (YAML in the module
    docstring, the standard Ansible ``DOCUMENTATION`` string constant, or a
    legacy docstring that is itself the DOCUMENTATION mapping);
  * that block has a non-empty ``short_description``;
  * that block has a non-empty ``options`` map (the argument_spec mirror).

If any module is undocumented the script exits non-zero and lists the offenders,
so CI fails until the MCP surface is filled in. With ``--generate-stub`` it will
inject a minimal DOCUMENTATION stub into offending modules (opt-in; the default
is to FAIL and never auto-edit module sources).
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
)


def iter_module_paths() -> list[Path]:
    return sorted(p for p in MODULES_DIR.glob("gludd_*.py") if p.is_file())


def _extract_documentation_block(docstring: str) -> str | None:
    """Slice just the top-level ``DOCUMENTATION:`` block body from a docstring.

    The gludd_* modules combine DOCUMENTATION/EXAMPLES/RETURN into one docstring.
    EXAMPLES routinely contains unquoted Jinja (``{{ ... }}``) that is not
    strict-YAML-safe and would sink a whole-docstring parse — so we isolate the
    DOCUMENTATION block alone before parsing it.
    """
    lines = docstring.splitlines()
    block_keys = ("DOCUMENTATION", "EXAMPLES", "RETURN")
    start = None
    for i, line in enumerate(lines):
        if line.startswith("DOCUMENTATION:") and line.split(":", 1)[0].strip() == "DOCUMENTATION":
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith(("EXAMPLES:", "RETURN:")) and not lines[j].startswith((" ", "\t")):
            key = lines[j].split(":", 1)[0].strip()
            if key in block_keys:
                end = j
                break
    return "\n".join(lines[start:end])


def _lenient_doc_mapping(block: str) -> dict[str, Any]:
    """Recover ``short_description`` + ``options`` names when strict YAML fails.

    Some module descriptions embed ``C(foo):`` references in plain multi-line
    scalars, which makes ``yaml.safe_load`` choke. This line-scanner recovers the
    presence-check fields directly so a genuinely-documented module is not a
    false offender.
    """
    out: dict[str, Any] = {}
    options: dict[str, Any] = {}
    in_options = False
    for line in block.splitlines():
        stripped = line.strip()
        if line.startswith("  ") and not line.startswith("   ") and ":" in stripped:
            key = stripped.split(":", 1)[0].strip()
            rest = stripped.split(":", 1)[1].strip()
            in_options = key == "options"
            if key == "short_description" and rest:
                out["short_description"] = rest
            continue
        if in_options and line.startswith("    ") and not line.startswith("     ") and ":" in stripped:
            opt_name = stripped.split(":", 1)[0].strip().lstrip("- ")
            if opt_name:
                options[opt_name] = {}
    if options:
        out["options"] = options
    return out


def _doc_mapping(source: str) -> dict[str, Any]:
    """Return the DOCUMENTATION mapping for a module, or {} if absent.

    Supports the in-tree convention (top-level ``DOCUMENTATION:`` key in the
    docstring YAML, parsed in isolation), the standard Ansible
    ``DOCUMENTATION`` string constant, and a bare docstring that IS the
    documentation mapping.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    for statement in tree.body:
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id == "DOCUMENTATION":
                value = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "DOCUMENTATION"
        ):
            value = statement.value
        if value is None:
            continue
        try:
            assigned = ast.literal_eval(value)
        except (ValueError, SyntaxError, TypeError):
            assigned = None
        if isinstance(assigned, str):
            try:
                data = yaml.safe_load(assigned)
            except yaml.YAMLError:
                data = None
            if isinstance(data, dict):
                return dict(data)

    docstring = ast.get_docstring(tree, clean=False)
    if not docstring:
        return {}
    block = _extract_documentation_block(docstring)
    if block is not None:
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            inner = data.get("DOCUMENTATION")
            if isinstance(inner, dict):
                return dict(inner)
            return dict(data)
        # Strict YAML failed (commonly an embedded ``C(foo):`` in a plain
        # multi-line description). Fall back to lenient field recovery so a
        # genuinely-documented module is not flagged as undocumented.
        return _lenient_doc_mapping(block)
    # No DOCUMENTATION header — try the whole docstring as a bare mapping.
    try:
        data = yaml.safe_load(docstring)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    if "DOCUMENTATION" in data and isinstance(data["DOCUMENTATION"], dict):
        return data["DOCUMENTATION"]
    return data


def check_module(path: Path) -> list[str]:
    """Return a list of problems for one module (empty == documented OK)."""
    problems: list[str] = []
    source = path.read_text(encoding="utf-8")
    doc = _doc_mapping(source)
    if not doc:
        problems.append("no parseable DOCUMENTATION block")
        return problems
    short = doc.get("short_description")
    if not (isinstance(short, str) and short.strip()):
        problems.append("missing/empty short_description")
    options = doc.get("options")
    if not (isinstance(options, dict) and options):
        problems.append("missing/empty options")
    return problems


_STUB_DOC = '''"""
DOCUMENTATION:
  module: {module}
  short_description: TODO short description for {module}
  description:
    - TODO describe what {module} does.
  options:
    daemon_url:
      description: Base URL of the daemon.
      type: str
      default: "http://localhost:8000"

EXAMPLES: []

RETURN: {{}}
"""
'''


def _inject_stub(path: Path) -> bool:
    """Prepend a stub DOCUMENTATION docstring if the module has none. Best-effort."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    if ast.get_docstring(tree):
        return False  # already has a docstring; don't clobber
    stub = _STUB_DOC.format(module=path.stem)
    lines = source.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("#!") or line.startswith("# -*-") or line.startswith("#"):
            insert_at = i + 1
            continue
        break
    new_source = "".join(lines[:insert_at]) + stub + "".join(lines[insert_at:])
    path.write_text(new_source, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generate-stub",
        action="store_true",
        help="Inject a stub DOCUMENTATION into undocumented modules (opt-in).",
    )
    args = parser.parse_args(argv)

    paths = iter_module_paths()
    if not paths:
        print(f"ERROR: no gludd_* modules found under {MODULES_DIR}", file=sys.stderr)
        return 1

    offenders: dict[str, list[str]] = {}
    for path in paths:
        problems = check_module(path)
        if problems:
            offenders[path.stem] = problems

    if offenders and args.generate_stub:
        for path in paths:
            if path.stem in offenders:
                injected = _inject_stub(path)
                if injected:
                    print(f"injected stub DOCUMENTATION into {path.name}")
        # Re-check after stub injection.
        offenders = {}
        for path in paths:
            problems = check_module(path)
            if problems:
                offenders[path.stem] = problems

    if offenders:
        print(
            f"MCP docs-check FAILED: {len(offenders)}/{len(paths)} gludd_* module(s) "
            "lack MCP-usable documentation:",
            file=sys.stderr,
        )
        for name in sorted(offenders):
            print(f"  - {name}: {'; '.join(offenders[name])}", file=sys.stderr)
        return 1

    print(f"MCP docs-check OK: all {len(paths)} gludd_* modules documented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
