#!/usr/bin/env python3
"""Generate MCP tool definitions + topics for the gludd_* Ansible modules.

PHASE 1 of the "MCP for all Ansible resources" directive. This script walks the
``general_ludd.agent`` collection's ``plugins/modules/gludd_*.py`` modules and,
for each one, builds:

  * an MCP tool def — ``{name, description, input_schema, server_id}`` where the
    ``input_schema`` is a JSON-schema derived from the module's
    ``AnsibleModule(argument_spec=...)`` (type / required / choices / default per
    option) — written to ``docs/MCP_TOOLS_MANIFEST.json``;
  * a topic — the module's full DOCUMENTATION / EXAMPLES / RETURN YAML blocks —
    written to ``docs/MCP_TOOLS_TOPICS.yml``.

The gludd_* modules carry their documentation either as YAML blocks inside the
module docstring (top-level ``DOCUMENTATION:`` / ``EXAMPLES:`` / ``RETURN:``
keys) or as the standard Ansible string constants with those names. Their
argument_spec is a ``dict(...)`` literal passed to ``AnsibleModule``. We parse
both documentation forms and the argument spec with ``ast`` (no module import
— the modules pull in ``ansible.module_utils`` which is not a runtime
dependency of this repo).

This is the *generator*. Wiring these tools into model binding is the separate
CA-T9 tool-binding gap and is intentionally out of scope here.
"""

from __future__ import annotations

import argparse
import ast
import json
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
MANIFEST_PATH = ROOT / "docs" / "MCP_TOOLS_MANIFEST.json"
TOPICS_PATH = ROOT / "docs" / "MCP_TOOLS_TOPICS.yml"

SERVER_ID = "ansible"
COLLECTION_FQN = "general_ludd.agent"

# Ansible arg-spec types -> JSON-schema types.
_TYPE_MAP = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "path": "string",
    "raw": "string",
    "json": "object",
    "bytes": "string",
    "bits": "integer",
    "jsonarg": "string",
    "sid": "string",
}


def iter_module_paths() -> list[Path]:
    """Return the sorted list of ``gludd_*.py`` module files."""
    return sorted(p for p in MODULES_DIR.glob("gludd_*.py") if p.is_file())


def _literal(node: ast.AST) -> Any:
    """Best-effort literal evaluation of an AST node.

    Returns the literal value, or ``None`` for anything non-literal (e.g. a
    name reference). We never raise — a non-literal default just becomes None.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def _parse_option_spec(call: ast.Call) -> dict[str, Any]:
    """Parse a single ``dict(type=..., required=..., ...)`` option spec call."""
    spec: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            continue
        spec[kw.arg] = _literal(kw.value)
    return spec


def extract_argument_spec(source: str) -> dict[str, dict[str, Any]]:
    """Extract the ``argument_spec=dict(...)`` mapping from a module's source.

    Returns ``{option_name: {type, required, choices, default, no_log, ...}}``.
    Uses AST so we never import the module (which needs ansible at runtime).
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_module = (isinstance(func, ast.Name) and func.id == "AnsibleModule") or (
            isinstance(func, ast.Attribute) and func.attr == "AnsibleModule"
        )
        if not is_module:
            continue
        for kw in node.keywords:
            if kw.arg != "argument_spec":
                continue
            return _spec_from_node(kw.value)
    return {}


def _spec_from_node(node: ast.AST) -> dict[str, dict[str, Any]]:
    """Turn a ``dict(opt=dict(...), ...)`` or ``{...}`` node into a spec map."""
    result: dict[str, dict[str, Any]] = {}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        for kw in node.keywords:
            if kw.arg is None:
                continue
            if isinstance(kw.value, ast.Call):
                result[kw.arg] = _parse_option_spec(kw.value)
            else:
                lit = _literal(kw.value)
                result[kw.arg] = lit if isinstance(lit, dict) else {}
    elif isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values, strict=False):
            key = _literal(k) if k is not None else None
            if not isinstance(key, str):
                continue
            if isinstance(v, ast.Call):
                result[key] = _parse_option_spec(v)
            else:
                lit = _literal(v)
                result[key] = lit if isinstance(lit, dict) else {}
    return result


def argspec_to_json_schema(spec: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Convert an Ansible argument_spec into a JSON-schema object."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, opt in spec.items():
        opt = opt or {}
        prop: dict[str, Any] = {}
        atype = opt.get("type", "str")
        prop["type"] = _TYPE_MAP.get(atype, "string")
        if opt.get("choices"):
            prop["enum"] = list(opt["choices"])
        if "default" in opt and opt["default"] is not None:
            prop["default"] = opt["default"]
        # Surface no_log so a binder knows the field is sensitive.
        if opt.get("no_log"):
            prop["x-no-log"] = True
        properties[name] = prop
        if opt.get("required"):
            required.append(name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


_BLOCK_KEYS = ("DOCUMENTATION", "EXAMPLES", "RETURN")


def _split_blocks(docstring: str) -> dict[str, str]:
    """Split a module docstring into its top-level ``KEY:`` block bodies.

    The gludd_* modules combine DOCUMENTATION/EXAMPLES/RETURN into ONE docstring
    as adjacent top-level YAML keys. Parsing the whole docstring as a single YAML
    document is brittle: Ansible EXAMPLES routinely contain unquoted Jinja
    (``{{ ... }}``) that is not strict-YAML-safe and sinks the entire parse. So
    we slice the docstring on the ``DOCUMENTATION:`` / ``EXAMPLES:`` /
    ``RETURN:`` lines and let each block be parsed (or fail) independently —
    a malformed EXAMPLES can never sink DOCUMENTATION.
    """
    lines = docstring.splitlines()
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        for key in _BLOCK_KEYS:
            if stripped == f"{key}:" or stripped.startswith(f"{key}:"):
                # Top-level key only (no leading indentation).
                if line[: line.index(":")].strip() == key and not line.startswith((" ", "\t")):
                    starts.append((i, key))
                break
    blocks: dict[str, str] = {}
    for idx, (line_no, key) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        # Re-include the KEY: line so the block is a self-contained mapping.
        blocks[key] = "\n".join(lines[line_no:end])
    return blocks


def _assigned_doc_blocks(tree: ast.Module) -> dict[str, str]:
    """Extract standard Ansible documentation string constants from an AST."""
    blocks: dict[str, str] = {}
    for statement in tree.body:
        name: str | None = None
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name):
                name = statement.target.id
                value = statement.value
        if name not in _BLOCK_KEYS or value is None:
            continue
        literal = _literal(value)
        if isinstance(literal, str):
            blocks[name] = literal
    return blocks


def _safe_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def lenient_doc_mapping(block: str) -> dict[str, Any]:
    """Recover key DOCUMENTATION fields when strict YAML fails.

    Some gludd_* descriptions embed ``C(foo):``-style references inside plain
    multi-line scalars, which makes ``yaml.safe_load`` choke (it reads the
    colon as a mapping key). Rather than lose the whole block, we line-scan the
    DOCUMENTATION body and recover ``module``, ``short_description``,
    ``description`` (folded into one string) and the ``options`` NAMES (each
    mapped to an empty dict — the real per-option arg-spec is recovered from the
    AST elsewhere, so we only need the names/presence here).
    """
    lines = block.splitlines()
    out: dict[str, Any] = {}
    options: dict[str, Any] = {}
    in_options = False
    in_description = False
    desc_parts: list[str] = []
    for line in lines:
        stripped = line.strip()
        # 2-space-indented top-level keys under DOCUMENTATION:.
        if line.startswith("  ") and not line.startswith("   ") and ":" in stripped:
            key = stripped.split(":", 1)[0].strip()
            rest = stripped.split(":", 1)[1].strip()
            in_options = key == "options"
            in_description = key == "description"
            if key == "module" and rest:
                out["module"] = rest
            elif key == "short_description" and rest:
                out["short_description"] = rest
            elif key == "description" and rest and rest not in ("|", ">", "|-", ">-"):
                desc_parts.append(rest)
            continue
        if in_options and line.startswith("    ") and not line.startswith("     "):
            # 4-space-indented option name (e.g. "    daemon_url:").
            if stripped.endswith(":") or ":" in stripped:
                opt_name = stripped.split(":", 1)[0].strip().lstrip("- ")
                if opt_name:
                    options[opt_name] = {}
            continue
        if in_description:
            # Collect folded/bulleted description continuation lines.
            txt = stripped.lstrip("- ").strip()
            if txt:
                desc_parts.append(txt)
    if options:
        out["options"] = options
    if desc_parts:
        out["description"] = desc_parts
    return out


def parse_doc_blocks(source: str) -> dict[str, Any]:
    """Parse DOCUMENTATION/EXAMPLES/RETURN YAML without importing the module.

    Returns a dict possibly containing ``DOCUMENTATION``, ``EXAMPLES`` and
    ``RETURN`` keys from either the combined module docstring or standard
    Ansible string constants. Each block is parsed independently so one
    malformed block (commonly EXAMPLES with unquoted Jinja) does not lose the
    others. When strict YAML fails, DOCUMENTATION falls back to lenient field
    recovery; EXAMPLES / RETURN fall back to raw text.
    """
    tree = ast.parse(source)
    docstring = ast.get_docstring(tree, clean=False)
    blocks = _split_blocks(docstring) if docstring else {}
    blocks.update(_assigned_doc_blocks(tree))
    if not blocks:
        if not docstring:
            return {}
        # No recognizable block headers; fall back to whole-docstring parse.
        data = _safe_yaml(docstring)
        return data if isinstance(data, dict) else {}
    out: dict[str, Any] = {}
    for key, body in blocks.items():
        parsed = _safe_yaml(body)
        if isinstance(parsed, dict) and key in parsed:
            out[key] = parsed[key]
        elif parsed is not None:
            out[key] = parsed
        elif key == "DOCUMENTATION":
            out[key] = lenient_doc_mapping(body)
        else:
            # Preserve EXAMPLES/RETURN content even when YAML can't parse it.
            out[key] = body
    return out


def _short_description(doc: dict[str, Any]) -> str:
    short = doc.get("short_description") or ""
    return str(short).strip()


def _long_description(doc: dict[str, Any]) -> str:
    desc = doc.get("description")
    if isinstance(desc, list):
        return " ".join(str(d).strip() for d in desc if str(d).strip())
    if desc:
        return str(desc).strip()
    return ""


def build_tool_def(module_name: str, doc: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Assemble one MCP tool def dict for a module."""
    documentation = doc.get("DOCUMENTATION", {}) if "DOCUMENTATION" in doc else doc
    short = _short_description(documentation)
    long = _long_description(documentation)
    description = f"{short} — {long}" if short and long else (short or long or module_name)
    return {
        "name": f"{COLLECTION_FQN}.{module_name}",
        "description": description,
        "input_schema": schema,
        "server_id": SERVER_ID,
    }


def _validate_with_registry(tool_defs: list[dict[str, Any]]) -> None:
    """If the real MCPTool model is importable, validate each tool def against it.

    Best-effort: the manifest entries must satisfy the same schema the runtime
    registry uses (name regex, field types). We don't fail generation if the
    import is unavailable (the script must run in a bare scripts environment),
    but if it IS available a malformed name will surface here.
    """
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from general_ludd.mcp.registry import MCPTool
    except Exception:
        return
    for td in tool_defs:
        MCPTool(**td)  # raises on invalid name / fields


def generate() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generate (tool_defs, topics) for every gludd_* module."""
    tool_defs: list[dict[str, Any]] = []
    topics: dict[str, Any] = {}
    for path in iter_module_paths():
        module_name = path.stem
        source = path.read_text(encoding="utf-8")
        doc = parse_doc_blocks(source)
        spec = extract_argument_spec(source)
        schema = argspec_to_json_schema(spec)
        tool_name = f"{COLLECTION_FQN}.{module_name}"
        tool_defs.append(build_tool_def(module_name, doc, schema))
        topics[tool_name] = {
            "module": module_name,
            "DOCUMENTATION": doc.get("DOCUMENTATION", {}),
            "EXAMPLES": doc.get("EXAMPLES", []),
            "RETURN": doc.get("RETURN", {}),
        }
    return tool_defs, topics


def write_artifacts(tool_defs: list[dict[str, Any]], topics: dict[str, Any]) -> None:
    _validate_with_registry(tool_defs)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(tool_defs, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    TOPICS_PATH.write_text(
        yaml.safe_dump(topics, sort_keys=True, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Generate in-memory and report counts without writing files.",
    )
    args = parser.parse_args(argv)

    tool_defs, topics = generate()
    if args.check:
        print(f"Would write {len(tool_defs)} tool defs to {MANIFEST_PATH}")
        print(f"Would write {len(topics)} topics to {TOPICS_PATH}")
        return 0

    write_artifacts(tool_defs, topics)
    print(f"Wrote {len(tool_defs)} MCP tool defs -> {MANIFEST_PATH.relative_to(ROOT)}")
    print(f"Wrote {len(topics)} MCP topics -> {TOPICS_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
