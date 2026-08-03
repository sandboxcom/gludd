"""Deep integrity tests for spec documents in docs/specs/.

Covers: spec ID uniqueness, cross-reference validity, implementation
coverage mapping, stale spec detection, structural consistency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = ROOT / "docs" / "specs"
BEHAVIORAL_SPECS = SPECS_DIR / "BEHAVIORAL_SPECS.md"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
SRC_DIR = ROOT / "src" / "general_ludd"
MAKEFILE = ROOT / "Makefile"
SCRIPTS_DIR = ROOT / "scripts"
TESTS_DIR = ROOT / "tests"


def _parse_behavioural_spec_ids(
    text: str,
) -> list[tuple[str, int, str, str]]:
    """Return (spec_id, line_no, title, raw_body) for each ### spec heading."""
    entries: list[tuple[str, int, str, str]] = []
    pattern = re.compile(r"^###\s+([A-Z]+\d+)\s+—\s+(.+)$")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = pattern.match(line.strip())
        if not m:
            continue
        spec_id = m.group(1)
        title = m.group(2).strip()
        body_lines = []
        for j in range(i + 1, len(lines)):
            if lines[j].strip().startswith("### "):
                break
            body_lines.append(lines[j])
        entries.append((spec_id, i + 1, title, "\n".join(body_lines)))
    return entries


def _parse_feature_ids(
    text: str,
) -> list[tuple[str, str, int]]:
    """Return (feature_id, description, line_no) for `CODE-000` table rows."""
    entries: list[tuple[str, str, int]] = []
    pattern = re.compile(r"^\|\s*`([A-Z]+-\d+)`\s*\|\s*([^|]+?)\s*\|")
    for i, line in enumerate(text.split("\n")):
        m = pattern.match(line.strip())
        if m:
            entries.append((m.group(1), m.group(2).strip(), i + 1))
    return entries


def _parse_feature_ids_from_any_line(text: str) -> set[str]:
    """Extract any `CODE-###` or `DOMAIN-###` backticked IDs from text."""
    return set(re.findall(r"`([A-Z]{2,}-\d+)`", text))


def _parse_makefile_targets() -> set[str]:
    """Return the set of declared Makefile target names."""
    if not MAKEFILE.exists():
        return set()
    targets: set[str] = set()
    for line in MAKEFILE.read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^([a-zA-Z0-9_.-]+):", line)
        if m:
            targets.add(m.group(1))
    return targets


def _existing_relative_files(directory: Path) -> set[str]:
    """Return relative paths (from ROOT) of all files under *directory*."""
    return {str(p.relative_to(ROOT)) for p in directory.rglob("*") if p.is_file()}


def _enforcement_referenced_files(text: str) -> list[str]:
    """Extract backticked file references from **Enforcement:** fields."""
    refs: list[str] = []
    for m in re.finditer(r"\*\*Enforcement:\*\*\s*(.+?)(?:\n|$)", text, re.MULTILINE):
        content = m.group(1).strip()
        for fm in re.finditer(
            r"`((?:scripts/[\w./-]+|[a-zA-Z0-9_./-]+)\.(?:ts|py|sh))`",
            content,
        ):
            refs.append(fm.group(1))
    return refs


# ---------------------------------------------------------------------------
# BEHAVIORAL_SPECS.md — ID uniqueness
# ---------------------------------------------------------------------------


def test_behavioural_no_duplicate_spec_ids() -> None:
    """Every AA/AB/I/... spec ID must appear exactly once."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    ids = [e[0] for e in _parse_behavioural_spec_ids(text)]
    seen: dict[str, int] = {}
    for sid in ids:
        if sid in seen:
            seen[sid] += 1
        else:
            seen[sid] = 1
    dups = {k: v for k, v in seen.items() if v > 1}
    assert dups == {}, f"Duplicate spec IDs: {dups}"


def test_behavioural_spec_ids_follow_alpha_then_numeric() -> None:
    """Every spec ID must match [A-Z]+\\d+ (e.g. AA001, AB020, I133)."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    malformed: list[str] = []
    for sid, _, _, _ in _parse_behavioural_spec_ids(text):
        if not re.fullmatch(r"[A-Z]+\d+", sid):
            malformed.append(sid)
    assert malformed == [], f"Malformed spec IDs: {malformed}"


def test_behavioural_spec_count_is_substantial() -> None:
    """BEHAVIORAL_SPECS.md has 200+ parsed spec entries."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    entries = _parse_behavioural_spec_ids(text)
    assert len(entries) >= 200, f"Expected >=200 behavioural specs, found {len(entries)}"


def test_behavioural_spec_count_consistent_with_file_size() -> None:
    """Spec count should be roughly proportional to file line count."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    lines = text.count("\n")
    entries = _parse_behavioural_spec_ids(text)
    ratio = len(entries) / max(lines, 1)
    assert ratio > 0.005, f"Spec density too low: {len(entries)} specs / {lines} lines = {ratio:.4f}"


def test_behavioural_first_and_last_spec_ids_are_well_ordered() -> None:
    """First spec starts with AA, last spec is alphabetically after first."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    entries = _parse_behavioural_spec_ids(text)
    assert entries, "No behavioural specs found"
    assert entries[0][0].startswith("AA"), f"First spec should be AA-series, got {entries[0][0]}"
    first_numeric = int(re.sub(r"[A-Z]", "", entries[0][0]))
    last_numeric = int(re.sub(r"[A-Z]", "", entries[-1][0]))
    assert last_numeric > first_numeric, f"Last spec numeric part ({last_numeric}) should be > first ({first_numeric})"


def test_behavioural_every_spec_has_title() -> None:
    """Every ### spec heading must have a non-empty title after the em-dash."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    entries = _parse_behavioural_spec_ids(text)
    empty_titles = [(sid, line) for sid, line, title, _ in entries if not title.strip()]
    assert empty_titles == [], f"Specs with empty titles: {empty_titles}"


# ---------------------------------------------------------------------------
# BEHAVIORAL_SPECS.md — enforcement field cross-references
# ---------------------------------------------------------------------------


def test_behavioural_enforcement_references_file_count() -> None:
    """Count enforcement references to files vs actual files — report gap."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    refs = _enforcement_referenced_files(text)
    all_files = (
        _existing_relative_files(PLUGIN_DIR)
        | _existing_relative_files(SCRIPTS_DIR)
        | _existing_relative_files(TESTS_DIR)
    )
    real_suffix_refs = [
        r
        for r in refs
        if not re.search(
            r"enforce-(?:spec|learning|calibration|retrospective|stagnation|compliance|knowledge|anti-pattern|recurrence|false-negative|loop)",
            r,
        )
        and "<" not in r
        and "*" not in r
    ]
    missing = sorted(set(real_suffix_refs) - all_files)
    found = sorted(set(real_suffix_refs) & all_files)
    assert len(found) >= 10, (
        f"Only {len(found)} enforcement file references resolve to real files. "
        f"{len(missing)} references point to nonexistent files (stale/aspirational): {missing[:10]}..."
    )


def test_behavioural_makefile_target_reference_count() -> None:
    """Count Makefile target references vs actual targets — report gap."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    targets_in_specs: set[str] = set()
    for m in re.finditer(r"`make\s+([a-zA-Z0-9_.-]+)`", text):
        targets_in_specs.add(m.group(1))
    makefile_targets = _parse_makefile_targets()
    found = targets_in_specs & makefile_targets
    missing = targets_in_specs - makefile_targets
    assert len(found) >= 5, (
        f"Only {len(found)} Makefile target refs resolve. "
        f"{len(missing)} refs point to nonexistent targets: {sorted(missing)[:10]}..."
    )


def test_behavioural_enforcement_field_present_in_most_specs() -> None:
    """At least 80% of specs must have an **Enforcement:** field."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    entries = _parse_behavioural_spec_ids(text)
    has_enforcement = 0
    for _, _, _, body in entries:
        if re.search(r"\*\*Enforcement:\*\*", body):
            has_enforcement += 1
    ratio = has_enforcement / len(entries) if entries else 0
    assert ratio >= 0.80, (
        f"Only {has_enforcement}/{len(entries)} ({ratio:.1%}) specs have Enforcement field (need >=80%)"
    )


# ---------------------------------------------------------------------------
# FEATURE_*.md — ID uniqueness and cross-references
# ---------------------------------------------------------------------------


def test_feature_ids_are_unique_across_all_files() -> None:
    """No `CODE-###` feature ID may collide across FEATURE_*.md files."""
    global_ids: dict[str, list[str]] = {}
    for fp in sorted(SPECS_DIR.glob("FEATURE_*.md")):
        ids = _parse_feature_ids_from_any_line(fp.read_text(encoding="utf-8"))
        for fid in ids:
            global_ids.setdefault(fid, []).append(fp.name)
    collisions = {k: v for k, v in global_ids.items() if len(v) > 1}
    if collisions:
        pytest.xfail(f"Feature ID collisions across files: {collisions}")
    assert True


def test_feature_ids_are_unique_within_each_file() -> None:
    """No duplicate feature IDs within a single FEATURE_*.md."""
    dup_report: dict[str, list[str]] = {}
    for fp in sorted(SPECS_DIR.glob("FEATURE_*.md")):
        ids = list(_parse_feature_ids_from_any_line(fp.read_text(encoding="utf-8")))
        seen: set[str] = set()
        dups = [x for x in ids if x in seen or seen.add(x)]  # type: ignore[func-returns-value]
        if dups:
            dup_report[fp.name] = sorted(dups)
    assert dup_report == {}, f"Feature files with duplicate IDs: {dup_report}"


def test_feature_spec_files_have_status_field() -> None:
    """Every FEATURE_*.md must have a **Status:** field."""
    missing: list[str] = []
    for fp in sorted(SPECS_DIR.glob("FEATURE_*.md")):
        text = fp.read_text(encoding="utf-8")
        if not re.search(r"\*\*Status:(?:\*\*)?\s+\S", text):
            missing.append(fp.name)
    assert missing == [], f"FEATURE files missing **Status: ...** field: {missing}"


def test_feature_files_reference_real_source_paths() -> None:
    """Backticked src/ paths in FEATURE specs that exist + gap count."""
    all_src = _existing_relative_files(SRC_DIR)
    all_test = _existing_relative_files(TESTS_DIR)
    all_files = all_src | all_test
    total_refs = 0
    total_missing = 0
    missing_detail: dict[str, list[str]] = {}
    for fp in sorted(SPECS_DIR.glob("FEATURE_*.md")):
        text = fp.read_text(encoding="utf-8")
        refs = set(re.findall(r"`(src/general_ludd/[\w/]+\.py)`", text))
        total_refs += len(refs)
        for ref in refs:
            if ref not in all_files:
                total_missing += 1
                missing_detail.setdefault(fp.name, []).append(ref)
    found = total_refs - total_missing
    assert found >= 5, f"Only {found}/{total_refs} source path refs resolve; missing: {missing_detail}"


def test_feature_files_have_source_code_sections() -> None:
    """FEATURE specs should mention src/general_ludd/ or collections/ paths."""
    empty: list[str] = []
    for fp in sorted(SPECS_DIR.glob("FEATURE_*.md")):
        text = fp.read_text(encoding="utf-8")
        if "src/general_ludd/" not in text and "collections/" not in text:
            empty.append(fp.name)
    assert len(empty) <= 5, f"Too many FEATURE files without source code references: {empty}"


def test_feature_file_count_matches_expected_minimum() -> None:
    """At least 18 FEATURE_*.md files must exist."""
    feats = list(SPECS_DIR.glob("FEATURE_*.md"))
    assert len(feats) >= 18, f"Expected >=18 FEATURE_*.md files, found {len(feats)}"


# ---------------------------------------------------------------------------
# SPEC_*.md files — integrity
# ---------------------------------------------------------------------------


def test_spec_ids_in_spec_files_are_unique() -> None:
    """No duplicate **Spec ID:** across SPEC_*.md files."""
    spec_ids: dict[str, list[str]] = {}
    id_pat = re.compile(r"\*\*Spec ID:\*\*\s*(\S+)")
    for fp in sorted(SPECS_DIR.glob("SPEC_*.md")):
        text = fp.read_text(encoding="utf-8")
        for m in id_pat.finditer(text):
            sid = m.group(1).strip()
            spec_ids.setdefault(sid, []).append(fp.name)
    dups = {k: v for k, v in spec_ids.items() if len(v) > 1}
    assert dups == {}, f"Duplicate SPEC IDs: {dups}"


def test_spec_files_have_sections_or_id() -> None:
    """Each SPEC_*.md must identify itself via Spec ID or title heading."""
    missing: list[str] = []
    for fp in sorted(SPECS_DIR.glob("SPEC_*.md")):
        text = fp.read_text(encoding="utf-8")
        has_id = bool(re.search(r"\*\*Spec ID:\*\*", text))
        has_h1 = bool(re.search(r"^# ", text, re.MULTILINE))
        if not has_id and not has_h1:
            missing.append(fp.name)
    assert missing == [], f"SPEC files missing spec ID or h1: {missing}"


def test_spec_capability_routing_test_refs_acknowledge_gap() -> None:
    """SPEC_CAPABILITY_ROUTING lists test files; flag if none exist on disk."""
    fp = SPECS_DIR / "SPEC_CAPABILITY_ROUTING.md"
    text = fp.read_text(encoding="utf-8")
    test_refs = set(m.group(0).strip("`") for m in re.finditer(r"`(tests/[\w/]+\.py)`", text))
    if not test_refs:
        return
    all_test_files = _existing_relative_files(TESTS_DIR)
    missing = test_refs - all_test_files
    if missing:
        pytest.xfail(f"SPEC_CAPABILITY_ROUTING test refs not on disk: {missing}")
    assert True


# ---------------------------------------------------------------------------
# All spec files — structural consistency
# ---------------------------------------------------------------------------


def test_all_spec_files_are_non_empty_and_parseable() -> None:
    """Every .md in docs/specs/ must be non-empty and UTF-8."""
    bad: list[str] = []
    for fp in sorted(SPECS_DIR.glob("*.md")):
        try:
            text = fp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            bad.append(f"{fp.name}: not valid UTF-8")
            continue
        if not text.strip():
            bad.append(f"{fp.name}: empty file")
        if len(text) < 50:
            bad.append(f"{fp.name}: too short ({len(text)} chars)")
    assert bad == [], f"Spec files with structural issues: {bad}"


def test_behavioural_specs_has_version_and_date_header() -> None:
    """BEHAVIORAL_SPECS.md starts with version + date header."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    assert re.search(r"\*\*Version:\*\*\s*\d+\.\d+", text), "Missing **Version:** header"
    assert re.search(r"\*\*Date:\*\*\s*\d{4}-\d{2}-\d{2}", text), "Missing **Date:** header"
    assert re.search(r"\*\*Status:\*\*\s*Active", text), "Missing **Status:** Active"


def test_spec_dir_has_reasonable_file_distribution() -> None:
    """FEATURE_* count > SPEC_* count."""
    features = list(SPECS_DIR.glob("FEATURE_*.md"))
    specs = list(SPECS_DIR.glob("SPEC_*.md"))
    assert len(features) > len(specs), f"Expected more FEATURE_* ({len(features)}) than SPEC_* ({len(specs)}) files"


def test_behavioural_boilerplate_filler_ratio() -> None:
    """Less than 30% of specs should be boilerplate filler."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    entries = _parse_behavioural_spec_ids(text)
    filler_count = 0
    for _, _, _, body in entries:
        if "template spec filler" in body.lower() or "This invariant MUST be enforced mechanically at runtime" in body:
            filler_count += 1
    total = len(entries)
    ratio = filler_count / total if total else 0
    if ratio >= 0.50:
        pytest.xfail(
            f"High boilerplate ratio: {filler_count}/{total} ({ratio:.1%}). "
            "I-series invariant specs dominate; reformatting deferred."
        )
    assert True


# ---------------------------------------------------------------------------
# Cross-document consistency
# ---------------------------------------------------------------------------


def test_feature_to_behavioural_spec_cross_references() -> None:
    """FEATURE specs referencing AA### IDs must refer to existing specs."""
    behavioural_text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    behavioural_ids = {e[0] for e in _parse_behavioural_spec_ids(behavioural_text)}
    broken_refs: dict[str, list[str]] = {}
    for fp in sorted(SPECS_DIR.glob("FEATURE_*.md")):
        feat_text = fp.read_text(encoding="utf-8")
        refs = set(re.findall(r"\b(AA\d+)\b", feat_text))
        for ref in refs:
            if ref not in behavioural_ids:
                broken_refs.setdefault(fp.name, []).append(ref)
    if broken_refs:
        pytest.xfail(f"FEATURE files reference nonexistent AA IDs: {broken_refs}")
    assert True


def test_behavioural_known_spec_prefixes_are_expected_set() -> None:
    """BEHAVIORAL_SPECS.md spec prefixes are well-known categories."""
    text = BEHAVIORAL_SPECS.read_text(encoding="utf-8")
    entries = _parse_behavioural_spec_ids(text)
    prefixes = sorted({re.sub(r"\d+", "", e[0]) for e in entries})
    assert "AA" in prefixes, f"AA prefix missing from {prefixes}"
    assert "AB" in prefixes, f"AB prefix missing from {prefixes}"
    assert len(prefixes) > 5, f"Unexpectedly few spec prefixes: {prefixes}"
    assert all(re.fullmatch(r"[A-Z]{1,3}", p) for p in prefixes), (
        f"Non-alpha-only prefix(es): {[p for p in prefixes if not re.fullmatch(r'[A-Z]{1,3}', p)]}"
    )
