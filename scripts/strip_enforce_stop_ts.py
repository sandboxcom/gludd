#!/usr/bin/env python3
"""Strip TypeScript syntax from enforce-stop.ts so node --experimental-strip-types can parse it.

Node v26's --experimental-strip-types fails on certain TS constructs
(particularly `as const` in property values and interface blocks inside
complex nested expressions). This script removes those constructs so the
file can be loaded by the test harness at scripts/test_hook_runtime.py.

The output file is valid ES module JavaScript that node can execute directly
WITHOUT --experimental-strip-types.

Transformations:
  1. interface Name { ... } blocks — removed entirely
  2. Type annotations from function params: (x: Type) → (x)
  3. Return type annotations: ): Type { → ) {
  4. `as const`, `as any`, `as Record<...>` — removed
  5. `satisfies Plugin` — removed
  6. Variable type annotations: let x: Type = → let x =
  7. Generic type parameters: `Record<string, any>` → removed
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"


def strip_interfaces(text: str) -> str:
    """Remove interface Name { ... } blocks."""
    result = []
    i = 0
    brace_depth = 0
    in_interface = False
    while i < len(text):
        if not in_interface:
            m = re.match(r"^interface\s+(\w+)\s*\{", text[i:])
            if m:
                in_interface = True
                brace_depth = 1
                i += m.end()
                continue
        if in_interface:
            if text[i] == "{":
                brace_depth += 1
            elif text[i] == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    in_interface = False
                    i += 1
                    # eat trailing newline
                    if i < len(text) and text[i] == "\n":
                        i += 1
                    # eat any additional blank line
                    if i < len(text) and text[i] == "\n":
                        i += 1
                    continue
            i += 1
            continue
        result.append(text[i])
        i += 1
    return "".join(result)


def strip_type_annotations(text: str) -> str:
    """Remove TypeScript type annotations aggressively."""
    # Remove `satisfies Plugin`
    text = re.sub(r"\s*satisfies\s+Plugin", "", text)

    # Remove `as const` (in all positions)
    text = re.sub(r"\s+as\s+const\b", "", text)

    # Remove `as any`
    text = re.sub(r"\s+as\s+any\b", "", text)

    # Remove `as Record<...>` and similar
    text = re.sub(r"\s+as\s+Record<[^>]*>", "", text)

    # Remove return type annotations: ): Type { or ): Type =>
    # Must handle nested generics like `StopStateCache | null`
    text = re.sub(
        r"\)\s*:\s*[\w.]+(?:\s*\|\s*[\w.]+)*(?:\s*\[\])?\s*(?=[{=])",
        ") ",
        text,
    )

    # Remove function param type annotations: (name: Type, name?: Type)
    # This is the trickiest part — must handle nested generics and union types
    def _strip_param_types(line: str) -> str:
        # Handle: function name(param: Type, param2?: Type = default): RetType {
        inner = line

        # Strip param type annotations
        def _replace_param(m):
            name = m.group(1)
            optional = m.group(2) or ""
            default = m.group(3) or ""
            return f"{name}{optional}{default}"

        inner = re.sub(
            r"(\w+)\s*(:)\s*[\w.]+(?:\s*\|\s*[\w.]+)*(?:\s*\[\])?\s*(=\s*[^,)]+)?",
            _replace_param,
            inner,
        )

        return inner

    lines = text.split("\n")
    result = []
    for line in lines:
        result.append(_strip_param_types(line))
    text = "\n".join(result)

    # Remove variable type annotations: let x: Type =
    text = re.sub(r"\b(let|const|var)\s+(\w+)\s*:\s*[\w.]+(?:\s*\|\s*[\w.]+)*(?:\s*\[\])?\s*=", r"\1 \2 =", text)

    # Remove variable type without init: let x: Type;
    text = re.sub(r"\b(let|const|var)\s+(\w+)\s*:\s*[\w.]+(?:\s*\|\s*[\w.]+)*\s*;", r"\1 \2;", text)

    # Remove generic type params in `Record<string, any>` used as type annotations
    # This catches `let data: Record<string, any>`
    text = re.sub(r":\s*Record<[^>]*>", "", text)

    # Remove block comments /* ... */ (but NOT line comments // ...)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    # Remove union types in destructuring: { x }: { x: Type }
    text = re.sub(r":\s*\{\s*[\w\s:;,|\[\]]*\}", "", text)

    return text


def strip_ts_generics(text: str) -> str:
    """Remove <Type> generic parameters from function calls and declarations."""
    # `<any>` type params
    text = re.sub(r"<any>", "", text)
    # Generic function params like `writeFileSync<string>` — only in type positions
    return text


def main():
    print(f"Reading {SRC}...")
    original = SRC.read_text("utf8")

    result = original

    # Step 1: Remove interfaces (they're type-only declarations)
    print("  Stripping interface blocks...")
    result = strip_interfaces(result)

    # Step 2: Strip all type annotations
    print("  Stripping type annotations...")
    result = strip_type_annotations(result)

    # Step 3: Strip generic type params
    print("  Stripping generic params...")
    result = strip_ts_generics(result)

    # Step 4: Clean up excessive blank lines
    result = re.sub(r"\n{4,}", "\n\n\n", result)

    # Write back
    print(f"Writing to {SRC} ({len(result)} bytes)")
    SRC.write_text(result, "utf8")

    print("Done. enforce-stop.ts has been stripped of TypeScript syntax.")


if __name__ == "__main__":
    main()
