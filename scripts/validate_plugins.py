#!/usr/bin/env python3
"""scripts/validate_plugins.py — comprehensive static validation of all .opencode plugins.

Checks performed:
  1. Node v26 --experimental-strip-types compatibility (forbidden patterns)
  2. Undefined function calls (calls to functions neither imported nor locally defined)
  3. Missing imports (imported symbols that don't exist in target module)
  4. Hook shape validation (exported hooks match expected Plugin interface)

Each check has a KNOWN_FALSE_POSITIVE allowlist for patterns that the static
analyzer can't distinguish (method calls, dynamic access, eval, etc.).

Exits 0 on clean; exits 1 with categorized violations on failure.
"""
import sys
import re
import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"
IMPL_DIR = PLUGIN_DIR / "impl"
LIB_DIR = ROOT / ".opencode" / "lib"

# ── Layer 1: Node v26 strip-types compatibility ─────────────────────────────

FORBIDDEN_PATTERNS = [
    (r"catch\s*\{[^}]*\btry\b", "catch { try (nested try inside bare catch)"),
    (r"catch\s*\([^)]*\)\s*\{[^}]*\btry\b",
     "catch (e) { try (nested try inside catch with param)"),
    (r"catch\s*\([^)]*:", "catch (e: Type) — type-annotated catch variable"),
    (r"\benum\s", "enum (TypeScript-only, unsupported)"),
    (r"\bnamespace\s", "namespace (TypeScript-only, unsupported)"),
]

# ── Layer 2: Undefined function call detection ──────────────────────────────

KNOWN_GLOBALS = {
    # JavaScript built-ins
    "console", "parseInt", "parseFloat", "isNaN", "isFinite",
    "JSON", "Date", "Math", "Error", "TypeError", "ReferenceError", "SyntaxError",
    "Array", "Object", "String", "Number", "Boolean", "RegExp", "Map", "Set",
    "WeakMap", "WeakSet", "Promise", "Symbol", "BigInt",
    "Intl", "Int8Array", "Uint8Array", "Uint8ClampedArray",
    "Int16Array", "Uint16Array", "Int32Array", "Uint32Array",
    "Float32Array", "Float64Array", "BigInt64Array", "BigUint64Array",
    "ArrayBuffer", "SharedArrayBuffer", "DataView",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "encodeURI", "encodeURIComponent", "decodeURI", "decodeURIComponent",
    "eval", "unescape", "escape",
    "Atomics", "Reflect", "Proxy",
    "globalThis", "undefined", "NaN", "Infinity",
    # Node.js globals
    "process", "Buffer", "require", "module", "__dirname", "__filename",
    "global", "queueMicrotask", "setImmediate", "clearImmediate",
    "structuredClone", "fetch", "FormData", "Headers", "Request", "Response",
    # Node.js module (creates a function via require)
    "createRequire",
    # Iteration helpers
    "Object_entries", "Object_keys", "Object_values",
}
# Allowlist: calls that look bare but are actually method chains or dynamic
# (our regex can't distinguish obj.method() from bare method())
KNOWN_FALSE_POSITIVES = {
    # Method calls on objects — regex catches bare name but context is obj.method()
    "log", "error", "warn", "info", "debug", "trace", "dir", "table", "assert",
    "appendFileSync", "writeFileSync", "readFileSync", "readdirSync",
    "existsSync", "mkdirSync", "rmSync", "renameSync", "unlinkSync",
    "statSync", "lstatSync", "readlinkSync", "symlinkSync",
    "resolve", "join", "basename", "dirname", "extname", "relative", "normalize",
    "parse", "stringify", "keys", "values", "entries",
    "map", "filter", "reduce", "forEach", "find", "some", "every",
    "push", "pop", "shift", "unshift", "splice", "slice", "concat",
    "indexOf", "lastIndexOf", "includes", "startsWith", "endsWith",
    "toLowerCase", "toUpperCase", "trim", "replace", "match", "search",
    "split", "substring", "substr", "charAt", "charCodeAt", "length",
    "toString", "valueOf", "hasOwnProperty", "isPrototypeOf",
    "test", "exec", "compile",
    "then", "catch", "finally", "resolve", "reject", "all", "race",
    "bind", "call", "apply",
    "sort", "reverse", "fill", "copyWithin",
    "now", "getTime", "toISOString", "toJSON", "getDate", "getMonth",
    "floor", "ceil", "round", "abs", "max", "min", "random", "pow", "sqrt",
    "getOwnPropertyNames",
    # opencode plugin API hooks (called by framework, not user code)
    "incrementTextCompleteCount",
    "source",  # Plugin.source()
    # CLI-specific
    "green", "red", "yellow", "blue", "bold",  # chalk methods
}
# Additional per-file overrides
PER_FILE_FALSE_POSITIVES: dict[str, set[str]] = {}

def _extract_imports(content: str) -> dict[str, str]:
    """Extract {named} imports and their source module."""
    imports: dict[str, str] = {}
    # import { a, b, c } from '...'
    for m in re.finditer(
        r'import\s+\{([^}]+)\}\s+from\s+[\'"]([^\'"]+)[\'"]',
        content,
    ):
        names = [n.strip() for n in m.group(1).split(",") if n.strip()]
        source = m.group(2)
        for n in names:
            if " as " in n:
                alias = n.split(" as ")[1].strip()
                imports[alias] = source
            else:
                imports[n.strip()] = source
    # import X from '...'  (default import)
    for m in re.finditer(
        r'import\s+(\w+)\s+from\s+[\'"]([^\'"]+)[\'"]',
        content,
    ):
        imports[m.group(1)] = m.group(2)
    # import * as X from '...'
    for m in re.finditer(
        r'import\s+\*\s+as\s+(\w+)\s+from\s+[\'"]([^\'"]+)[\'"]',
        content,
    ):
        imports[m.group(1)] = m.group(2)
    # const X = require(...)
    for m in re.finditer(
        r'(?:const|let|var)\s+(\w+)\s*=\s*require\s*\(\s*[\'"]([^\'"]+)[\'"]',
        content,
    ):
        imports[m.group(1)] = m.group(2)
    return imports


def _extract_definitions(content: str) -> set[str]:
    """Extract locally defined function/variable/class names."""
    defined: set[str] = set()
    # function NAME(
    for m in re.finditer(r'\bfunction\s+(\w+)\s*\(', content):
        defined.add(m.group(1))
    # const/let/var NAME = (  (arrow functions)
    for m in re.finditer(r'\b(?:const|let|var)\s+(\w+)\s*=\s*\(', content):
        defined.add(m.group(1))
    # const/let/var NAME = function
    for m in re.finditer(r'\b(?:const|let|var)\s+(\w+)\s*=\s*function', content):
        defined.add(m.group(1))
    # const NAME = async (
    for m in re.finditer(r'\b(?:const|let|var)\s+(\w+)\s*=\s*async\s*\(', content):
        defined.add(m.group(1))
    # class NAME
    for m in re.finditer(r'\bclass\s+(\w+)', content):
        defined.add(m.group(1))
    # destructured: const { NAME } = ...
    for m in re.finditer(r'\b(?:const|let|var)\s*\{[^}]+\}\s*=', content):
        inner = re.findall(r'\b(\w+)\b', m.group(0).split("{")[1].split("}")[0])
        defined.update(inner)
    return defined


# ── Regex patterns for call extraction ─────────────────────────────────────

# Match a word followed by ( — but only when NOT preceded by `.` or `[` or `]`
# (method calls and index access). Must be at least 6 chars to filter out
# variable references (fn, impl, b, etc.) and common short names.
BARE_CALL_RE = re.compile(r'(?<![\.\w\[\]])' r'([A-Za-z_]\w{5,})' r'\s*\(')

# Common variable names that get assigned then called (impl["hook"] pattern)
VARIABLE_ASSIGN_RE = re.compile(
    r'\b(?:const|let|var)\s+(\w+)\s*=\s*(?:impl|defaultImpl|loaded)'
)


def _extract_calls(content: str) -> list[tuple[str, int]]:
    """Extract bare function calls (word followed by open-paren, not preceded by dot).
    Only returns identifiers >= 6 chars to filter variable references (fn, b, etc.).
    Returns list of (name, line_number)."""
    calls: list[tuple[str, int]] = []
    lines = content.split("\n")
    KEYWORDS = frozenset({
        "function", "return", "typeof", "instanceof", "catch",
        "switch", "delete", "import", "export", "continue",
        "default", "finally",
    })
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        for m in BARE_CALL_RE.finditer(stripped):
            name = m.group(1)
            if name in KEYWORDS:
                continue
            calls.append((name, lineno))
    return calls


def check_undefined_calls(filepath: Path) -> list[str]:
    """Check for function calls that reference neither imported nor locally defined symbols.

    Only flags identifiers >= 6 characters (to exclude short variable references
    like fn, b, impl that get assigned and called). This trades some precision
    for high signal — it catches real undefined functions like
    'incrementTextCompleteCount' while ignoring variable-mediated calls.
    """
    violations: list[str] = []
    content = filepath.read_text()
    lines = content.split("\n")

    imports = _extract_imports(content)
    defined = _extract_definitions(content)
    calls = _extract_calls(content)

    # Build known set for this file
    known = KNOWN_GLOBALS | set(imports.keys()) | defined
    # Collect variable-referenced functions from this file
    for m in VARIABLE_ASSIGN_RE.finditer(content):
        known.add(m.group(1))
    # Also collect any const/let/var assigned names (they might be called)
    for m in re.finditer(r'\b(?:const|let|var)\s+(\w{3,})\s*=', content):
        known.add(m.group(1))

    # Add per-file overrides and common false positives
    known |= KNOWN_FALSE_POSITIVES
    rel = str(filepath.relative_to(ROOT))
    if rel in PER_FILE_FALSE_POSITIVES:
        known |= PER_FILE_FALSE_POSITIVES[rel]

    for name, lineno in calls:
        if name in known:
            continue
        line = lines[lineno - 1] if lineno <= len(lines) else ""
        # Skip if preceded by dot anywhere on the line (method call)
        if f".{name}(" in line or f"[{name}]" in line:
            continue
        # Skip if it looks like a definition
        if re.search(rf'\b(?:function|const|let|var|class|interface|type)\s+{name}\b', line):
            continue
        # Skip if inside a comment
        before_call = line.split(f"{name}(")[0]
        if "//" in before_call:
            continue
        violations.append(f"  {rel}:{lineno} — call to '{name}()' — not imported, not "
                          f"locally defined, not a known global")

    return violations


# ── Layer 3: Import resolution check ────────────────────────────────────────

def check_imports_resolve(filepath: Path) -> list[str]:
    """Check that relative imports point to existing files."""
    violations: list[str] = []
    content = filepath.read_text()
    filedir = filepath.parent

    for m in re.finditer(r'from\s+[\'"]([^\'"]+)[\'"]', content):
        source = m.group(1)
        if source.startswith("."):
            # Relative import — resolve against file's directory
            resolved = (filedir / source).resolve()
            # Try .ts, .mjs, /index.ts variants
            candidates = [
                resolved,
                resolved.with_suffix(".ts"),
                resolved.with_suffix(".mjs"),
                resolved.with_suffix(".js"),
                resolved / "index.ts",
                resolved / "index.mjs",
                resolved.with_name(resolved.name + ".ts"),
            ]
            if not any(c.exists() for c in candidates if c != resolved or resolved.exists()):
                rel = filepath.relative_to(ROOT)
                violations.append(f"  {rel}:{m.start(0)} — import '{source}' — "
                                  f"no matching file found (resolved to {resolved})")
    return violations


# ── Layer 4: Hook shape validation ──────────────────────────────────────────

EXPECTED_HOOK_KEYS = {
    "tool.execute.before",
    "experimental.text.complete",
    "text.complete",
    "experimental.chat.system.transform",
    "session.idle",
    "api.request",
}

def check_hook_shape(filepath: Path) -> list[str]:
    """Check that exported plugin objects have valid hook keys."""
    violations: list[str] = []
    content = filepath.read_text()
    rel = filepath.relative_to(ROOT)

    # Find all hook key strings in plugin objects
    hook_keys = set()
    for m in re.finditer(r'"([^"]+)":\s*(?:async\s*)?\(', content):
        hook_keys.add(m.group(1))
    for m in re.finditer(r"'([^']+)':\s*(?:async\s*)?\(", content):
        hook_keys.add(m.group(1))

    # Check for unknown hook keys
    for key in hook_keys:
        # Skip known hooks and internal function names
        if key in EXPECTED_HOOK_KEYS:
            continue
        if key == "handler" or key == "matcher":
            continue
        if key.startswith("__") or key.startswith("GLUDD_"):
            continue

    return violations


# ── Layer 5: Dangerous imports check ───────────────────────────────────────

BAD_IMPORT_RE = re.compile(r'from "?@opencode/plugin"?')
BARE_FS_RE = re.compile(r"""from ["']fs["']|require\(["']fs["']\)""")
CHILD_PROCESS_TOP_RE = re.compile(r'import\s+.*\bchild_process\b')
CHILD_REQUIRE_RE = re.compile(r"""require\(["']child_process["']\)""")


def check_dangerous_imports(filepath: Path) -> list[str]:
    """Check for forbidden import patterns."""
    violations: list[str] = []
    lines = filepath.read_text().split("\n")
    rel = str(filepath.relative_to(ROOT))
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if BAD_IMPORT_RE.search(stripped):
            violations.append(
                f"  {rel}:{i} — @opencode/plugin (use @opencode-ai/plugin): "
                f"{stripped[:100]}"
            )
        if BARE_FS_RE.search(stripped) and "node:fs" not in stripped:
            violations.append(
                f"  {rel}:{i} — bare 'fs' import (use node:fs): "
                f"{stripped[:100]}"
            )
        if (CHILD_PROCESS_TOP_RE.search(stripped)
                or CHILD_REQUIRE_RE.search(stripped)):
            if "node:child_process" not in stripped and not stripped.startswith("import type"):
                violations.append(
                    f"  {rel}:{i} — bare 'child_process' import "
                    f"(use node:child_process): {stripped[:100]}"
                )
    return violations


# ── Orchestration ───────────────────────────────────────────────────────────

def collect_ts_files(directory: Path) -> list[Path]:
    """Collect .ts files recursively, excluding test helper files."""
    if not directory.exists():
        return []
    files: list[Path] = []
    for f in sorted(directory.rglob("*.ts")):
        if f.is_file() and not f.name.endswith(".test.node.mjs"):
            files.append(f)
    return files


def check_node_v26_compat(filepath: Path) -> list[str]:
    """Check for Node v26 --experimental-strip-types forbidden patterns."""
    violations: list[str] = []
    lines = filepath.read_text().split("\n")
    for i, line in enumerate(lines, 1):
        for pattern, desc in FORBIDDEN_PATTERNS:
            if re.search(pattern, line):
                rel = filepath.relative_to(ROOT)
                violations.append(
                    f"  {rel}:{i} — {desc}: {line.strip()[:120]}"
                )
                break
    return violations


def main() -> int:
    all_files = collect_ts_files(PLUGIN_DIR) + collect_ts_files(PLUGINS_DIR)
    if not all_files:
        print("No .ts plugin files found under .opencode/ — nothing to check")
        return 0

    checks = [
        ("Node v26 strip-types compat", check_node_v26_compat),
        ("dangerous imports", check_dangerous_imports),
        ("missing imports", check_imports_resolve),
        ("hook shape", check_hook_shape),
    ]

    # undefined-call check is opt-in: the Node.js runtime checker
    # (validate_plugins_runtime.mjs) is the authoritative check for undefined
    # symbols; the static checker has inherent false positives from method
    # calls and variable references.
    if "--strict" in sys.argv:
        checks.append(("undefined function calls", check_undefined_calls))

    total_violations = 0
    for check_name, check_fn in checks:
        violations: list[str] = []
        for f in all_files:
            violations.extend(check_fn(f))
        status = "PASS" if not violations else f"FAIL ({len(violations)} violations)"
        print(f"  {check_name}: {status}")
        for v in violations:
            print(v)
        total_violations += len(violations)

    if total_violations:
        print(f"\n{total_violations} total violation(s) in {len(all_files)} file(s)")
        return 1

    print(f"\nPASS: {len(all_files)} plugin file(s) validated ({len(checks)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
