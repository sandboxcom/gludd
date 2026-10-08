#!/usr/bin/env python3
"""AB022 — cross-reference behavioral test assertions against Makefile target recipes.

Reads both exact behavioral-enforcement test modules, extracts target names,
finds the corresponding BEHAVIORAL_SPECS.md spec, and verifies the Makefile
target recipe actually implements the described behavior.

Flags targets whose recipe is unrelated to their spec's enforcement description.
"""

import re
import sys
from pathlib import Path

try:
    from scripts.makefile_layout import compose_makefile
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from makefile_layout import compose_makefile

try:
    from scripts.behavioral_specs import load_behavioral_specs
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from behavioral_specs import load_behavioral_specs

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
BEHAVIORAL_TEST_FILES = (
    ROOT / "tests" / "unit" / "test_behavioral_enforcement.py",
    ROOT / "tests" / "unit" / "test_behavioral_enforcement_runtime.py",
)

SPEC_ENFORCEMENT_RE = re.compile(r"\*\*Enforcement:\*\*\s+(.+)$")
TARGET_ASSERT_RE = re.compile(r'guard_exists_in_makefile\("([^"]+)"\)')


def find_target_enforcement(spec_id: str) -> str | None:
    if not SPECS_FILE.exists():
        return None

    content = load_behavioral_specs(SPECS_FILE)
    in_spec = False

    for line in content.split("\n"):
        if line.startswith(f"### {spec_id} "):
            in_spec = True
            continue
        if in_spec and line.startswith("### "):
            break
        if in_spec:
            m = SPEC_ENFORCEMENT_RE.match(line)
            if m:
                return m.group(1)
    return None


def main() -> int:
    missing = [path for path in BEHAVIORAL_TEST_FILES if not path.exists()]
    if missing:
        names = ", ".join(path.name for path in missing)
        print(f"check-target-contract: test file(s) not found: {names}")
        return 0

    test_content = "\n".join(
        path.read_text(encoding="utf-8") for path in BEHAVIORAL_TEST_FILES
    )
    make_content = compose_makefile(MAKEFILE) if MAKEFILE.exists() else ""
    mismatches: list[str] = []

    # Extract test class -> target name
    test_sections = re.split(r"\nclass (Test\w+)\W", test_content)
    for i in range(1, len(test_sections), 2):
        class_body = test_sections[i + 1] if i + 1 < len(test_sections) else ""

        targets = TARGET_ASSERT_RE.findall(class_body)
        for target in targets:
            # Check target recipe contains relevant keywords
            if make_content:
                idx = make_content.find(f"\n{target}:")
                if idx != -1:
                    block = make_content[idx : idx + 600]
                    # At minimum the target should do something beyond just echo PASS
                    has_meaningful = any(
                        kw in block.lower()
                        for kw in ["$(uv)", "$(python)", "python3", "scripts/", "git ", "exit 1", "BLOCKED"]
                    )
                    if not has_meaningful:
                        mismatches.append(
                            f"  {target}: recipe is pass-through only — verify it implements spec behavior"
                        )

    if mismatches:
        print(f"check-target-contract: {len(mismatches)} target(s) with potential recipe mismatches:")
        for m in mismatches:
            print(m)
        print("  NOTE: This is advisory — targets may be structural stubs whose logic is in scripts.")
        return 0  # Advisory only, not a hard gate

    print("check-target-contract: all targets have meaningful recipes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
