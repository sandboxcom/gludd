"""
Bridge ansible modules to model-callable tools.

Exposes ansible modules as function definitions that models can call:
  - AnsibleToolAdapter: wraps a module as a callable tool
  - AnsibleToolSchema: JSON Schema from ansible module parameters
  - discover_tools: scan a collection for modules
  - call_tool: invoke a module via subprocess
  - register_collection_tools: register all modules from a collection
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# YAML parsing (pure-Python fallback when yaml not available)
# ---------------------------------------------------------------------------

try:
    import yaml as _yaml

    def _parse_yaml_safe(text: str) -> Any:
        return _yaml.safe_load(text)
except ImportError:

    def _parse_yaml_safe(text: str) -> Any:
        return _parse_yaml_inline(text)


_ANSIBLE_TYPE_MAP: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "bool": "boolean",
    "float": "number",
    "list": "array",
    "dict": "object",
    "path": "string",
    "raw": "string",
}

_BLOCK_HEADER_RE = re.compile(r"^(DOCUMENTATION|EXAMPLES|RETURN):\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Minimal YAML-like parser (for environments without PyYAML)
# ---------------------------------------------------------------------------


def _indent_level(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _extract_yaml_blocks(docstring: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    header_pattern = re.compile(r"^(DOCUMENTATION|EXAMPLES|RETURN):\s*(.*)")

    for line in docstring.split("\n"):
        stripped = line.strip()
        if stripped in ('"""', "'''"):
            if current_key is not None and current_lines:
                blocks[current_key] = "\n".join(current_lines)
            current_key = None
            current_lines = []
            continue

        m = header_pattern.match(line)
        if m:
            if current_key is not None and current_lines:
                blocks[current_key] = "\n".join(current_lines)
            current_key = m.group(1)
            current_lines = []
            trailing = m.group(2).strip()
            if trailing:
                current_lines.append(trailing)
        elif current_key is not None:
            current_lines.append(line)

    if current_key is not None and current_lines:
        blocks[current_key] = "\n".join(current_lines)

    for key in list(blocks.keys()):
        lines = blocks[key].split("\n")
        cleaned = []
        for line in lines:
            stripped = line.rstrip()
            if stripped == '"""' or stripped == "'''":
                continue
            if stripped.startswith("#"):
                continue
            cleaned.append(line)
        blocks[key] = "\n".join(cleaned).rstrip()

    return blocks


# ---------------------------------------------------------------------------
# Inline YAML parser (pure-Python, no PyYAML)
# ---------------------------------------------------------------------------


def _parse_yaml_inline(text: str) -> Any:
    lines = text.split("\n")
    root: dict[str, Any] = {}
    stack: list[tuple[dict[str, Any], int]] = [(root, -1)]

    def _current_dict() -> dict[str, Any]:
        return stack[-1][0]

    _in_list: int | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            i += 1
            continue

        indent = _indent_level(line)
        content = stripped

        while stack and indent <= stack[-1][1]:
            stack.pop()

        target = _current_dict()

        if _in_list is not None and indent > _in_list:
            if content.startswith("- "):
                val = content[2:].strip()
                val = _coerce_yaml(val)
                if isinstance(target, list):
                    target.append(val)
                i += 1
                continue
            elif indent <= _in_list:
                _in_list = None
                while stack and indent <= stack[-1][1]:
                    stack.pop()

        if content.startswith("- ") and not content.startswith("- name:"):
            val = content[2:].strip()
            val = _coerce_yaml(val)
            if isinstance(target, list):
                target.append(val)
            i += 1
            continue

        colon_idx = content.find(":")
        if colon_idx < 0:
            i += 1
            continue

        key = content[:colon_idx].strip()
        value = content[colon_idx + 1 :].strip()

        if key == "" or key.startswith("#"):
            i += 1
            continue

        if key.startswith("- "):
            key = key[2:]
            if isinstance(target, list):
                if value:
                    target.append({key: _coerce_yaml(value)})
                else:
                    new_dict: dict[str, Any] = {}
                    target.append(new_dict)
                    stack.append((new_dict, indent))
            i += 1
            continue

        if value == "":
            i += 1
            if i < len(lines) and _indent_level(lines[i]) > indent:
                if lines[i].lstrip().startswith("- "):
                    new_list: list[Any] = []
                    target[key] = new_list
                    stack.append((new_list, indent))  # type: ignore[arg-type]
                    _in_list = indent
                else:
                    new_dict: dict[str, Any] = {}
                    target[key] = new_dict
                    stack.append((new_dict, indent))
            else:
                target[key] = None
        else:
            target[key] = _coerce_yaml(value)
            i += 1

    return root


def _coerce_yaml(value: str) -> Any:
    if not value:
        return ""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() in ("null", "none", "~"):
        return None
    try:
        if "." in value or "e" in value.lower():
            return float(value)
    except (ValueError, OverflowError):
        pass
    try:
        return int(value)
    except (ValueError, OverflowError):
        pass
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


# ---------------------------------------------------------------------------
# DOCUMENTATION / EXAMPLES / RETURN extraction
# ---------------------------------------------------------------------------


def _parse_module_doc(source: str) -> dict[str, Any]:
    blocks = _extract_yaml_blocks(source)
    result: dict[str, Any] = {"doc": None, "examples": None, "return": None}

    for key, block in blocks.items():
        if not block.strip():
            continue
        try:
            if key == "DOCUMENTATION":
                result["doc"] = _parse_yaml_safe(block)
            elif key == "EXAMPLES":
                result["examples"] = _parse_yaml_safe(block)
            elif key == "RETURN":
                result["return"] = _parse_yaml_safe(block)
        except Exception:
            pass

    return result


def _extract_params(doc: dict[str, Any] | None) -> dict[str, Any]:
    if doc is None or "options" not in doc:
        return {}
    return dict(doc["options"])


# ---------------------------------------------------------------------------
# AnsibleToolSchema — JSON Schema from ansible module parameters
# ---------------------------------------------------------------------------


class AnsibleToolSchema:
    @staticmethod
    def build(name: str, description: str, params: dict[str, Any]) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        no_log_params: list[str] = []

        for pname, pspec in params.items():
            if not isinstance(pspec, dict):
                properties[pname] = {"type": "string", "description": str(pspec)}
                continue

            ans_type = pspec.get("type", "str")
            json_type = _ANSIBLE_TYPE_MAP.get(ans_type, "string")
            prop: dict[str, Any] = {"type": json_type}

            if "description" in pspec:
                prop["description"] = str(pspec["description"])

            if "default" in pspec:
                prop["default"] = pspec["default"]

            if "choices" in pspec:
                prop["enum"] = list(pspec["choices"])

            if pspec.get("required"):
                required.append(pname)

            if pspec.get("no_log"):
                no_log_params.append(pname)

            properties[pname] = prop

        schema: dict[str, Any] = {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            schema["parameters"]["required"] = required
        if no_log_params:
            schema["no_log_params"] = no_log_params

        return schema


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


def validate_params(params: dict[str, Any], schema: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    ps = schema.get("parameters", {})
    required = ps.get("required", [])
    properties = ps.get("properties", {})

    for r in required:
        if r not in params:
            errors.append(f"Missing required parameter: {r}")

    type_check: dict[str, Any] = {
        "string": str,
        "integer": int,
        "boolean": bool,
        "number": (int, float),
        "array": list,
        "object": dict,
    }

    for pname, pvalue in params.items():
        if pname in properties:
            expected = properties[pname].get("type", "string")
            check = type_check.get(expected)
            if check is not None and not isinstance(pvalue, check):
                errors.append(f"Parameter '{pname}' expected {expected}, got {type(pvalue).__name__}")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# AnsibleToolAdapter — wraps an ansible module as a callable tool
# ---------------------------------------------------------------------------


class AnsibleToolAdapter:
    def __init__(
        self,
        module_name: str,
        module_path: str,
        collection_path: str,
        timeout: int = 60,
    ) -> None:
        self.module_name = module_name
        self.module_path = module_path
        self.collection_path = collection_path
        self.timeout = timeout
        self._source: str | None = None
        self._doc_info: dict[str, Any] | None = None

    @property
    def source(self) -> str:
        if self._source is None:
            try:
                self._source = Path(self.module_path).read_text(encoding="utf-8")
            except OSError:
                self._source = ""
        return self._source

    @property
    def doc_info(self) -> dict[str, Any] | None:
        if self._doc_info is None:
            self._doc_info = _parse_module_doc(self.source)
        return self._doc_info

    @property
    def tool_name(self) -> str:
        return self.module_name

    def to_tool_schema(self) -> dict[str, Any]:
        doc_info = self.doc_info
        doc = doc_info.get("doc") if doc_info else None
        description = ""
        if doc:
            short = doc.get("short_description", "")
            desc_lines = doc.get("description", [])
            if isinstance(desc_lines, list):
                description = ". ".join(str(line) for line in desc_lines)
            else:
                description = str(desc_lines) if desc_lines else ""
            if short:
                description = short + ("" if not description else " — " + description)

        params = _extract_params(doc)
        return AnsibleToolSchema.build(self.module_name, description, params)

    def call(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            python_exe = sys.executable
            args = [python_exe, self.module_path]

            collection_parent = os.path.dirname(os.path.dirname(os.path.dirname(self.collection_path)))
            ansi_parent = os.path.dirname(collection_parent)
            env = os.environ.copy()
            current_path = env.get("ANSIBLE_COLLECTIONS_PATH", "")
            paths = [p for p in current_path.split(":") if p] if current_path else []
            if str(ansi_parent) not in paths:
                paths.insert(0, str(ansi_parent))
            env["ANSIBLE_COLLECTIONS_PATH"] = ":".join(paths)

            for key, value in params.items():
                if isinstance(value, bool):
                    args.append(f"{key}={str(value).lower()}")
                elif isinstance(value, (dict, list)):
                    args.append(f"{key}={json.dumps(value)}")
                else:
                    args.append(f"{key}={value}")

            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )

            if result.returncode == 0:
                return {
                    "success": True,
                    "changed": False,
                    "module": self.module_name,
                    "result": result.stdout.strip(),
                }
            else:
                return {
                    "success": False,
                    "changed": False,
                    "module": self.module_name,
                    "error": result.stderr.strip() or f"exit code {result.returncode}",
                }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "changed": False,
                "module": self.module_name,
                "error": f"Timeout after {self.timeout}s",
            }
        except Exception as exc:
            return {
                "success": False,
                "changed": False,
                "module": self.module_name,
                "error": str(exc),
            }


# ---------------------------------------------------------------------------
# discover_tools — scan a collection for ansible modules
# ---------------------------------------------------------------------------


def discover_tools(collection_path: str, max_doc_kb: int = 1024) -> list[AnsibleToolAdapter]:
    tools: list[AnsibleToolAdapter] = []
    modules_dir = Path(collection_path) / "plugins" / "modules"

    if not modules_dir.is_dir():
        return tools

    for entry in sorted(modules_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix != ".py":
            continue
        if entry.name.startswith("__"):
            continue
        if entry.name.startswith("."):
            continue

        module_name = entry.stem
        module_path = str(entry)

        try:
            size_kb = entry.stat().st_size / 1024
            if size_kb > max_doc_kb:
                continue
        except OSError:
            continue

        adapter = AnsibleToolAdapter(
            module_name=module_name,
            module_path=module_path,
            collection_path=collection_path,
        )
        tools.append(adapter)

    return tools


# ---------------------------------------------------------------------------
# register_collection_tools — register all modules from a collection
# ---------------------------------------------------------------------------


def register_collection_tools(collection_path: str) -> dict[str, Any]:
    adapters = discover_tools(collection_path)
    cpath = Path(collection_path).resolve()
    parts = cpath.parts
    if "ansible_collections" in parts:
        idx = parts.index("ansible_collections")
        if idx + 2 < len(parts):
            namespace = parts[idx + 1]
            collection_name = parts[idx + 2]
        else:
            namespace = cpath.parent.name
            collection_name = cpath.name
    else:
        collection_name = cpath.name
        namespace = cpath.parent.name

    fq_collection = f"{namespace}.{collection_name}"

    tool_defs: list[dict[str, Any]] = []
    for adapter in adapters:
        schema = adapter.to_tool_schema()
        tool_defs.append(
            {
                "type": "function",
                "function": schema,
            }
        )

    return {
        "collection": fq_collection,
        "tools": tool_defs,
        "adapter_count": len(adapters),
    }


# ---------------------------------------------------------------------------
# call_tool — invoke a registered tool by name
# ---------------------------------------------------------------------------


def call_tool(
    module_name: str,
    params: dict[str, Any],
    collection_path: str,
    timeout: int = 60,
) -> dict[str, Any]:
    modules_dir = Path(collection_path) / "plugins" / "modules"
    module_file = modules_dir / f"{module_name}.py"

    if not module_file.is_file():
        return {
            "success": False,
            "module": module_name,
            "error": f"Module not found: {module_file}",
        }

    adapter = AnsibleToolAdapter(
        module_name=module_name,
        module_path=str(module_file),
        collection_path=collection_path,
        timeout=timeout,
    )
    return adapter.call(params)


# ---------------------------------------------------------------------------
# JSON encoder (for modules that return non-serializable types)
# ---------------------------------------------------------------------------


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    return str(obj)
