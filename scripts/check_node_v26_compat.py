#!/usr/bin/env python3
"""scripts/check_node_v26_compat.py — scan .ts files under .opencode/ for forbidden
Node v26 --experimental-strip-types patterns.

Forbidden patterns (each is a parse error under --experimental-strip-types):
  catch { try       — nested try inside bare catch
  catch (e) { try   — nested try inside typed catch
  catch (e:          — type-annotated catch variable
  enum                — TypeScript-only construct
  namespace           — TypeScript-only construct

Exits 0 on clean; exits 1 with file + line references on violations.
"""
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

FORBIDDEN = [
    (r"catch\s*\{[^}]*\btry\b", "catch { try (nested try inside bare catch)"),
    (r"catch\s*\([^)]*\)\s*\{[^}]*\btry\b", "catch (e) { try (nested try inside catch with param)"),
    (r"catch\s*\([^)]*:", "catch (e: Type) — type-annotated catch variable"),
    (r"\benum\s", "enum (TypeScript-only, unsupported)"),
    (r"\bnamespace\s", "namespace (TypeScript-only, unsupported)"),
]


def collect_ts_files() -> list[Path]:
    files: list[Path] = []
    for d in (PLUGIN_DIR, PLUGINS_DIR):
        if d.exists():
            files.extend(sorted(f for f in d.glob("*.ts") if f.is_file()))
    return files


def check_file(filepath: Path) -> list[str]:
    violations: list[str] = []
    lines = filepath.read_text().split("\n")
    for i, line in enumerate(lines, 1):
        for pattern, desc in FORBIDDEN:
            if re.search(pattern, line):
                rel = filepath.relative_to(ROOT)
                violations.append(f"  {rel}:{i} — {desc}: {line.strip()[:120]}")
                break  # one violation per line
    return violations


def main() -> int:
    files = collect_ts_files()
    if not files:
        print("No .ts files found under .opencode/ — nothing to check")
        return 0

    all_violations: list[str] = []
    for f in files:
        all_violations.extend(check_file(f))

    if all_violations:
        print(
            f"{len(all_violations)} Node v26 --experimental-strip-types "
            f"compatibility violation(s) in {len(files)} file(s):"
        )
        for v in all_violations:
            print(v)
        print(
            "\nFix: remove type annotation from catch, use bare catch {} with no "
            "inner try-catch, or replace enum/namespace with const objects."
        )
        return 1

    print(f"PASS: {len(files)} .ts file(s) are Node v26 --experimental-strip-types compatible")
    return 0


if __name__ == "__main__":
    sys.exit(main())
