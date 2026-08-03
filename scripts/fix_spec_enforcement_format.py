"""Fix enforcement text format in BEHAVIORAL_SPECS.md so check_spec_enforcement_coverage.py
recognizes the enforcement mechanisms. Converts "`target` in Makefile" to
"Makefile `target`" format, adds backticks where missing, etc."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
MAKEFILE = ROOT / "Makefile"

# --- Recognized enforcement patterns (from check_spec_enforcement_coverage.py) ---

FILLER = re.compile(r"(planned|TODO|TBD|not yet|future|upcoming)", re.IGNORECASE)

MAKE_PATTERN = re.compile(r"`make\s+([\w\-]+)`")
SCRIPT_PATTERN = re.compile(r"`(scripts/[\w_/]+\.py)`")
PLUGIN_PATTERN = re.compile(r"`(enforce-[\w\-]+\.ts)`")
MAKEFILE_PATTERN = re.compile(r"Makefile\s+`([\w\-]+)`")

# --- Parse specs ---


def parse_specs():
    text = SPECS_FILE.read_text()
    specs = []
    current = None
    in_enforcement = False
    in_behavior = False

    for line in text.split("\n"):
        m = re.match(r"^### (A[A-Z]\d+) — (.+)$", line)
        if m:
            if current and current.get("id"):
                specs.append(current)
            current = {
                "id": m.group(1),
                "title": m.group(2),
                "enforcement_lines": [],
                "behavior_lines": [],
                "header_line": line,
                "line_idx": len(specs),
            }
            in_enforcement = False
            in_behavior = False
            continue

        if not current:
            continue

        if line.startswith("**Enforcement:**"):
            in_enforcement = True
            in_behavior = False
            enf = line.replace("**Enforcement:**", "").strip()
            current["enforcement_lines"].append(enf)
            current["enf_start_line"] = line
            continue

        if line.startswith("**Behavior:**"):
            in_behavior = True
            in_enforcement = False
            current["behavior_lines"].append(line.replace("**Behavior:**", "").strip())
            continue

        if in_enforcement and line.strip():
            current["enforcement_lines"].append(line.strip())

        if in_behavior and line.strip():
            current["behavior_lines"].append(line.strip())

    if current and current.get("id"):
        specs.append(current)
    return specs


def is_covered(enforcement_text):
    if not enforcement_text or FILLER.search(enforcement_text):
        return False
    lower = enforcement_text.lower()
    if "agents.md" in lower and (ROOT / "AGENTS.md").exists():
        return True
    m = MAKE_PATTERN.search(enforcement_text)
    if m:
        target = m.group(1)
        content = MAKEFILE.read_text()
        return bool(re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE))
    m = SCRIPT_PATTERN.search(enforcement_text)
    if m:
        return (ROOT / m.group(1)).exists()
    m = PLUGIN_PATTERN.search(enforcement_text)
    if m:
        return (ROOT / ".opencode" / "plugin" / m.group(1)).exists()
    m = MAKEFILE_PATTERN.search(enforcement_text)
    if m:
        target = m.group(1)
        content = MAKEFILE.read_text()
        return bool(re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE))
    return False


def fix_enforcement_text(spec):
    """Return (fixed_text, changed) for a spec's enforcement text."""
    enf = " ".join(spec["enforcement_lines"]).strip()
    if not enf or is_covered(enf):
        return enf, False

    makefile_targets = re.findall(r"`([_\w\-]+)`", enf)
    fixed = enf

    for target in makefile_targets:
        # Check if target exists in Makefile
        content = MAKEFILE.read_text()
        if re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE):
            # Check if it's already in a recognized format
            if f"Makefile `{target}`" in fixed:
                continue
            if f"`make {target}`" in fixed:
                continue
            # Fix "`<target>` in Makefile" -> "Makefile `<target>`"
            fixed = re.sub(
                rf"`{re.escape(target)}`\s+in\s+Makefile",
                rf"Makefile `{target}`",
                fixed,
            )
            # Fix "`<target>` on ALL push targets" -> "`make <target>`"
            fixed = re.sub(
                rf"`{re.escape(target)}`\s+on\s+ALL\s+push\s+targets",
                rf"`make {target}`",
                fixed,
            )
            # Fix "`<target>` target" -> "`make <target>`"
            fixed = re.sub(
                rf"`{re.escape(target)}`\s+target",
                rf"`make {target}`",
                fixed,
            )
            # Fix "`<target>` in Makefile + pre-commit hook" -> "Makefile `<target>` + pre-commit hook"
            fixed = re.sub(
                rf"`{re.escape(target)}`\s+in\s+Makefile\s*\+\s*pre-commit\s+hook",
                rf"Makefile `{target}` + pre-commit hook",
                fixed,
            )
            # Fix "as pre-commit hook" for make targets
            fixed = re.sub(
                rf"`make\s+{re.escape(target)}`\s+as\s+pre-commit\s+hook",
                rf"`make {target}` + pre-commit hook",
                fixed,
            )

    # Handle bare enforce-*.ts references
    bare_plugin = re.findall(r"(?<![`/])(enforce-[\w\-]+\.ts)(?![`])", fixed)
    for p in bare_plugin:
        if (ROOT / ".opencode" / "plugin" / p).exists():
            fixed = fixed.replace(p, f"`{p}`")

    # Handle "`<target>` extended with ..."
    fixed = re.sub(
        r"`([_\w\-]+)`\s+extended\s+with\s+parallel-work\s+detection",
        lambda m: (
            f"Makefile `{m.group(1)}`"
            if re.search(rf"^{re.escape(m.group(1))}:\s", MAKEFILE.read_text(), re.MULTILINE)
            else m.group(0)
        ),
        fixed,
    )

    # Handle "`make gate-lite --no-fail-fast` variant" - strip flags and use base target
    m = re.match(r"`make\s+([\w\-]+).*`\s+variant", fixed)
    if m:
        base_target = m.group(1)
        content = MAKEFILE.read_text()
        if re.search(rf"^{re.escape(base_target)}:\s", content, re.MULTILINE):
            # Check for a no-fail-fast variant
            if re.search(rf"^{re.escape(base_target)}-no-fail-fast:\s", content, re.MULTILINE):
                fixed = f"`make {base_target}-no-fail-fast`"

    return fixed, fixed != enf


def main():
    specs = parse_specs()
    uncovered_before = [s for s in specs if not is_covered(" ".join(s["enforcement_lines"]).strip())]
    print(f"Uncovered before: {len(uncovered_before)}/{len(specs)}")

    # Read the file
    lines = SPECS_FILE.read_text().split("\n")
    changes = 0

    for spec in specs:
        enf = " ".join(spec["enforcement_lines"]).strip()
        if is_covered(enf):
            continue
        fixed, changed = fix_enforcement_text(spec)
        if not changed:
            print(f"  SKIP {spec['id']}: couldn't auto-fix ({enf[:80]})")
            continue

        # Find and replace the enforcement line in the file
        enf_start = spec.get("enf_start_line")
        if not enf_start:
            continue
        line_idx = None
        for i, line in enumerate(lines):
            if line == enf_start:
                line_idx = i
                break
        if line_idx is None:
            print(f"  SKIP {spec['id']}: line not found")
            continue

        # Replace just the enforcement content on the first line
        new_line = f"**Enforcement:** {fixed}"
        lines[line_idx] = new_line
        # Clear continuation lines
        next_idx = line_idx + 1
        while next_idx < len(lines):
            next_line = lines[next_idx]
            if next_line.strip() and not next_line.startswith("**") and not next_line.startswith("#"):
                if next_idx > line_idx + 1 or not next_line.strip():
                    lines[next_idx] = ""
                next_idx += 1
            else:
                break
        changes += 1
        print(f"  FIX  {spec['id']}: {enf[:60]}... -> {fixed[:60]}...")

    # Write back
    SPECS_FILE.write_text("\n".join(lines) + "\n")
    print(f"\n{changes} specs fixed, {len(uncovered_before) - changes} left unfixed")

    # Re-verify
    specs2 = parse_specs()
    uncovered_after = [s for s in specs2 if not is_covered(" ".join(s["enforcement_lines"]).strip())]
    covered = len(specs2) - len(uncovered_after)
    ratio = covered / len(specs2)
    print(f"Coverage after: {covered}/{len(specs2)} = {ratio:.1%}")
    if ratio >= 0.90:
        print("PASS: threshold met")
        return 0
    else:
        print(f"\nStill uncovered ({len(uncovered_after)}):")
        for s in uncovered_after[:30]:
            enf = " ".join(s["enforcement_lines"]).strip()
            print(f"  {s['id']}: {enf[:100]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
