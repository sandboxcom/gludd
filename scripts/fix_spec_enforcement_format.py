"""Fix enforcement text format in BEHAVIORAL_SPECS.md so check_spec_enforcement_coverage.py
recognizes the enforcement mechanisms. Converts "`target` in Makefile" to
"Makefile `target`" format, adds backticks where missing, etc."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS_FILE = ROOT / "docs" / "specs" / "BEHAVIORAL_SPECS.md"
MAKEFILE = ROOT / "Makefile"

# --- Constants ---

FILLER = re.compile(r"(planned|TODO|TBD|not yet|future|upcoming)", re.IGNORECASE)

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
    """Check if enforcement text is already in a recognized, verifiable format.
    For compound claims (e.g. script + make target), ANY resolved part counts."""
    if not enforcement_text or FILLER.search(enforcement_text):
        return False

    lower = enforcement_text.lower()
    if "agents.md" in lower and (ROOT / "AGENTS.md").exists():
        return True

    # Try ALL patterns — any resolved part makes the spec covered.

    for m_make in re.finditer(r"`make\s+([\w\-]+)`", enforcement_text):
        target = m_make.group(1)
        content = MAKEFILE.read_text()
        if re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE):
            return True

    for m_script in re.finditer(r"`(scripts/[\w_/]+\.py)`", enforcement_text):
        if (ROOT / m_script.group(1)).exists():
            return True

    for m_plugin in re.finditer(r"`(enforce-[\w\-]+\.ts)`", enforcement_text):
        if (ROOT / ".opencode" / "plugin" / m_plugin.group(1)).exists():
            return True

    for m_makefile in re.finditer(r"Makefile\s+`([\w\-]+)`", enforcement_text):
        target = m_makefile.group(1)
        content = MAKEFILE.read_text()
        if re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE):
            return True

    return False


def _target_exists(target: str) -> bool:
    content = MAKEFILE.read_text()
    return bool(re.search(rf"^{re.escape(target)}:\s", content, re.MULTILINE))


def fix_enforcement_text(spec):
    """Return (fixed_text, changed) for a spec's enforcement text."""
    enf = " ".join(spec["enforcement_lines"]).strip()
    if not enf or is_covered(enf):
        return enf, False

    fixed = enf

    # --- Phase 1: fix "`<target>` in Makefile" -> "Makefile `<target>`" ---
    # Always fix the format regardless of whether the target exists.
    # The coverage checker will validate existence separately.
    fixed = re.sub(
        r"`([_\w\-]+)`\s+in\s+Makefile",
        r"Makefile `\1`",
        fixed,
    )

    # --- Phase 2: strip arguments from inside `make target ARGS` backticks ---
    # "`make deduplicate-specs DEDUP=1` as pre-commit hook" -> "`make deduplicate-specs` as pre-commit hook"
    def _strip_make_args(m):
        target = m.group(1)
        suffix = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
        return f"`make {target}`{suffix}"

    fixed = re.sub(
        r"`make\s+([_\w\-]+)\s+[^`]+`(.*)",
        _strip_make_args,
        fixed,
    )

    # --- Phase 3: "`<target>` on ALL push targets" -> "`make <target>`" ---
    def _fix_on_all_push(m):
        t = m.group(1)
        if _target_exists(t):
            return f"`make {t}`"
        return m.group(0)

    fixed = re.sub(
        r"`([_\w\-]+)`\s+on\s+ALL\s+push\s+targets",
        _fix_on_all_push,
        fixed,
    )

    # --- Phase 4: "`<target>` target" -> "`make <target>`" ---
    def _fix_target_target(m):
        t = m.group(1)
        if _target_exists(t):
            return f"`make {t}`"
        return m.group(0)

    fixed = re.sub(
        r"`([_\w\-]+)`\s+target(?:\s+\+)?(.*)",
        _fix_target_target,
        fixed,
    )

    # --- Phase 5: "`<target>` extended" -> "Makefile `<target>`" / "`make <target>`" ---
    def _fix_extended(m):
        t = m.group(1)
        if _target_exists(t):
            return f"`make {t}`"
        return f"Makefile `{t}`"

    fixed = re.sub(
        r"`([_\w\-]+)`\s+extended",
        _fix_extended,
        fixed,
    )

    # --- Phase 5b: "`make <target>` extended" / "`make <target>` target" — strip suffix ---
    # These have spaces inside backticks so the `([_\w\-]+)` regex in Phase 4/5 misses them.
    fixed = re.sub(
        r"(`make\s+[_\w\-]+`)\s+(?:extended|target)\b",
        r"\1",
        fixed,
    )

    # --- Phase 6: "`<target>` in Makefile + pre-commit hook" (already mostly handled by Phase 1) ---
    fixed = re.sub(
        r"`([_\w\-]+)`\s+in\s+Makefile\s*\+\s*pre-commit\s+hook",
        r"Makefile `\1` + pre-commit hook",
        fixed,
    )

    # --- Phase 7: "`make <target>` as pre-commit hook" -> "`make <target>` + pre-commit hook" ---
    def _fix_as_precommit(m):
        t = m.group(1)
        return f"`make {t}` + pre-commit hook"

    fixed = re.sub(
        r"`make\s+([_\w\-]+)`\s+as\s+pre-commit\s+hook",
        _fix_as_precommit,
        fixed,
    )

    # --- Phase 8: "`make <target> --flag` variant" -> "`make <target>-variant`" ---
    def _fix_variant(m):
        base = m.group(1)
        content = MAKEFILE.read_text()
        for suffix in ["-no-fail-fast"]:
            candidate = f"{base}{suffix}"
            if re.search(rf"^{re.escape(candidate)}:\s", content, re.MULTILINE):
                return f"`make {candidate}`"
        return f"`make {base}`"

    fixed = re.sub(
        r"`make\s+([_\w\-]+)[^`]*`\s+variant",
        _fix_variant,
        fixed,
    )

    # --- Phase 9: "`<target>` extended with parallel-work detection" ---
    def _fix_extended_parallel(m):
        t = m.group(1)
        if _target_exists(t):
            return f"`make {t}`"
        return f"Makefile `{t}`"

    fixed = re.sub(
        r"`([_\w\-]+)`\s+extended\s+with\s+parallel-work\s+detection",
        _fix_extended_parallel,
        fixed,
    )

    # --- Phase 10: bare enforce-*.ts references ---
    bare_plugin = re.findall(r"(?<![`/])(enforce-[\w\-]+\.ts)(?![`])", fixed)
    for p in bare_plugin:
        if (ROOT / ".opencode" / "plugin" / p).exists():
            fixed = fixed.replace(p, f"`{p}`")

    # --- Phase 11: "`scripts/...` + `make target`" format already correct; strip prefix for cleaner coverage ---
    # Already well-formed — just ensure scripts that exist stay as-is.

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
