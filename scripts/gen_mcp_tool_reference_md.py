#!/usr/bin/env python3
"""Generate a human-readable MCP tool reference (markdown) from the JSON manifest.

Reads ``docs/MCP_TOOLS_MANIFEST.json`` (produced by ``gen-mcp-tools``) and writes
``docs/MCP_TOOL_REFERENCE.md`` — a single markdown file enumerating every
``gludd_*`` Ansible module with its MCP tool name, description, and input schema
parameters. Run after ``gen-mcp-tools`` to keep the reference current.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "docs" / "MCP_TOOLS_MANIFEST.json"
OUTPUT_PATH = ROOT / "docs" / "MCP_TOOL_REFERENCE.md"

_TYPE_LABEL: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


def _param_row(name: str, prop: dict[str, Any], required: set[str]) -> str:
    ptype = _TYPE_LABEL.get(prop.get("type", "string"), prop.get("type", "string"))
    req = "**required**" if name in required else ""
    default = ""
    if "default" in prop:
        default = f"`{json.dumps(prop['default'])}`"
    return f"| `{name}` | {ptype} | {req}{' ' if req else ''}| {default} |"



def _tool_section(tool: dict[str, Any], index: int, total: int) -> str:
    name = tool["name"]
    short = name.split(".", 2)[2] if name.startswith("general_ludd.agent.") else name
    server = tool.get("server_id", "ansible")
    desc = tool.get("description", "").strip()

    schema = tool.get("input_schema", {})
    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    lines: list[str] = []
    lines.append(f"### {index}. `{short}`")
    lines.append("")
    lines.append(f"**Server:** `{server}` | **FQCN:** `{name}`")
    lines.append("")
    lines.append(f"> {desc}")
    lines.append("")

    if props:
        lines.append("| Parameter | Type | Required | Default |")
        lines.append("|-----------|------|----------|---------|")
        for pname, pdef in sorted(props.items()):
            lines.append(_param_row(pname, pdef, required))
        lines.append("")
    else:
        lines.append("_No parameters._")
        lines.append("")

    return "\n".join(lines)


def generate(output_path: Path | None = None) -> str:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise SystemExit(f"ERROR: {MANIFEST_PATH} is not a JSON array")

    tool_count = len(manifest)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    try:
        tag_cmd = ["git", "-C", str(ROOT), "describe", "--tags", "--always", "--dirty"]
        tag = subprocess.check_output(tag_cmd, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        tag = "unknown"

    sections: list[str] = []

    # Header
    sections.append("# MCP Tool Reference")
    sections.append("")
    sections.append(f"**Generated:** {timestamp} | **Version:** `{tag}` | **Tools:** {tool_count}")
    sections.append("")
    sections.append(
        "Every `gludd_*` Ansible module in the `general_ludd.agent` collection is "
        "automatically surfaced as an MCP tool with a JSON-schema input contract. "
        "This reference is regenerated via `make gen-mcp-tool-ref` (which calls "
        "`gen-mcp-tools` then this generator)."
    )
    sections.append("")
    sections.append("---")
    sections.append("")

    # TOC
    sections.append("## Tool Index")
    sections.append("")
    for i, tool in enumerate(manifest, 1):
        name = tool["name"]
        short = name.split(".", 2)[2] if name.startswith("general_ludd.agent.") else name
        short_desc = (tool.get("description", "") or "").strip().split(".")[0].strip() or short
        sections.append(f"{i}. [`{short}`](#{i}-{short.replace('_', '-')}) — {short_desc}")
    sections.append("")
    sections.append("---")
    sections.append("")

    # Per-tool sections
    sections.append("## Tool Reference")
    sections.append("")
    for i, tool in enumerate(manifest, 1):
        sections.append(_tool_section(tool, i, tool_count))

    content = "\n".join(sections) + "\n"

    out = output_path or OUTPUT_PATH
    out.write_text(content, encoding="utf-8")
    return content


def check_stale() -> bool:
    """Return True if the reference file is stale (missing or older than manifest)."""
    if not OUTPUT_PATH.exists():
        return True
    if not MANIFEST_PATH.exists():
        return False
    manifest_mtime = MANIFEST_PATH.stat().st_mtime
    ref_mtime = OUTPUT_PATH.stat().st_mtime
    return manifest_mtime > ref_mtime


def main(argv: list[str] | None = None) -> int:
    cmd = (argv or sys.argv)[1:2]
    if cmd == ["--check"]:
        if check_stale():
            print(
                "MCP tool reference is STALE — run 'make gen-mcp-tool-ref' to regenerate",
                file=sys.stderr,
            )
            return 1
        print("MCP tool reference is current.")
        return 0

    try:
        generate()
        print(f"Wrote {OUTPUT_PATH} ({len(json.loads(MANIFEST_PATH.read_text()))} tools)")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
