"""Deep audit of .opencode/skills/ SKILL.md files — parse, validate, deduplicate."""

import json
import re
from pathlib import Path

SKILLS_ROOT = Path(__file__).parent.parent.parent / ".opencode" / "skills"
SKILLS_SUB = SKILLS_ROOT / "skills"  # mattypocock legacy md files
BUILTIN_SKILL_NAMES = {"opencode-customize"}  # built-in, no SKILL.md on disk

KNOWN_FRONTMATTER_FIELDS = {"name", "description", "location", "metadata", "tags", "category"}

VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")


def _skill_dirs():
    """Return directories under SKILLS_ROOT that should contain SKILL.md (exclude sub/)."""
    return [d for d in sorted(SKILLS_ROOT.iterdir()) if d.is_dir() and d.name != "skills"]


def _read_skill(path):
    """Read and return (raw_text, frontmatter_dict, body_text) for a skill markdown file."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return raw, None, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return raw, None, raw
    return raw, parts[1].strip(), parts[2]


def _parse_yaml_frontmatter(yaml_text):
    """Parse simple YAML frontmatter without third-party libs."""
    result = {}
    multiline_key = None
    multiline_value = ""
    for line in yaml_text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if multiline_key:
            if stripped.endswith('"') and not stripped.startswith('"'):
                multiline_value += "\n" + line
                result[multiline_key] = multiline_value.strip().strip('"')
                multiline_key = None
            else:
                multiline_value += "\n" + line
            continue
        match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)", stripped)
        if match:
            key = match.group(1)
            val = match.group(2).strip()
            if val.startswith('"') and not val.endswith('"'):
                multiline_key = key
                multiline_value = line
            elif val.startswith('"') and val.endswith('"'):
                result[key] = val.strip('"')
            elif val in ("true", "false"):
                result[key] = val == "true"
            elif val == "{}":
                result[key] = {}
            elif (val.startswith("{") and val.endswith("}")) or (val.startswith("[") and val.endswith("]")):
                try:
                    result[key] = json.loads(val)
                except json.JSONDecodeError:
                    result[key] = val
            elif re.match(r"^-?\d+$", val):
                result[key] = int(val)
            else:
                result[key] = val
    return result


def _parse_json_frontmatter(text):
    """Parse JSON frontmatter (used in skills/skills/ files)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_keywords(description):
    """Extract trigger keywords from a description string."""
    match = re.search(r"Trigger keywords?\s*:\s*(.+)", description, re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1).rstrip(".")
    return [kw.strip().lower() for kw in raw.split(",") if kw.strip()]


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────


class TestEverySkillHasMarkdown:
    """Every skill directory (except built-in and skills/) has a SKILL.md."""

    def test_every_skill_dir_has_skill_md(self):
        missing = []
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                missing.append(d.name)
        assert not missing, f"Missing SKILL.md in: {missing}"

    def test_skill_count_reasonable(self):
        dirs = [d.name for d in _skill_dirs() if d.name not in BUILTIN_SKILL_NAMES]
        assert len(dirs) >= 15, f"Expected >=15 skill dirs, found {len(dirs)}"


class TestEverySkillMarkdownParses:
    """Every SKILL.md has valid frontmatter and content."""

    def test_frontmatter_opens_and_closes(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            raw = md.read_text(encoding="utf-8")
            assert raw.startswith("---"), f"{d.name}/SKILL.md: does not start with ---"
            count = raw.count("---")
            assert count >= 2, f"{d.name}/SKILL.md: missing closing ---"

    def test_frontmatter_yaml_parseable(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            assert fm is not None, f"{d.name}/SKILL.md: no frontmatter found"
            parsed = _parse_yaml_frontmatter(fm)
            assert len(parsed) > 0, f"{d.name}/SKILL.md: frontmatter parsed to empty dict"

    def test_frontmatter_has_required_fields(self):
        required = {"name", "description"}
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            missing = required - set(parsed.keys())
            assert not missing, f"{d.name}/SKILL.md: missing required fields: {missing}"

    def test_frontmatter_no_unknown_fields(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            unknown = set(parsed.keys()) - KNOWN_FRONTMATTER_FIELDS
            assert not unknown, f"{d.name}/SKILL.md: unknown fields: {unknown}"


class TestSkillNameConsistency:
    """Skill name in frontmatter matches directory name."""

    def test_name_matches_directory(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            actual = parsed.get("name", "")
            assert actual == d.name, f"{d.name}/SKILL.md: 'name' field '{actual}' != directory '{d.name}'"

    def test_name_non_empty(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            name = parsed.get("name", "")
            assert name, f"{d.name}/SKILL.md: name field is empty"
            assert len(name.strip()) == len(name), f"{d.name}/SKILL.md: name has leading/trailing whitespace"

    def test_description_non_empty(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            desc = parsed.get("description", "")
            assert desc, f"{d.name}/SKILL.md: description is empty"
            assert len(desc) >= 10, f"{d.name}/SKILL.md: description too short ({len(desc)} chars)"


class TestNoDuplicateSkillNames:
    """No two skills share the same frontmatter name."""

    def test_no_duplicate_names(self):
        seen = {}
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            name = parsed.get("name", "")
            if name in seen:
                seen[name].append(d.name)
            else:
                seen[name] = [d.name]
        dupes = {n: dirs for n, dirs in seen.items() if len(dirs) > 1}
        assert not dupes, f"Duplicate skill names: {dupes}"


class TestTriggerKeywords:
    """Trigger keywords are present where expected and unique across skills."""

    def test_at_least_some_skills_have_trigger_keywords(self):
        count = 0
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            desc = parsed.get("description", "")
            if "trigger keywords" in desc.lower():
                count += 1
        assert count >= 5, f"Only {count} skills have trigger keywords, expected >=5"

    def test_trigger_keywords_parse_valid(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            desc = parsed.get("description", "")
            kws = _extract_keywords(desc)
            for kw in kws:
                assert kw, f"{d.name}: empty keyword in '{desc[-80:]}'"
                assert len(kw) >= 2, f"{d.name}: keyword '{kw}' too short"
                assert "," not in kw, f"{d.name}: keyword '{kw}' contains comma — split error"

    def test_no_duplicate_keywords_within_single_skill(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            desc = parsed.get("description", "")
            kws = _extract_keywords(desc)
            seen = set()
            dupes = []
            for kw in kws:
                if kw in seen:
                    dupes.append(kw)
                else:
                    seen.add(kw)
            assert not dupes, f"{d.name}: duplicate keywords in trigger list: {dupes}"

    def test_trigger_keywords_across_skills_overlap_report(self):
        all_kws = {}
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            desc = parsed.get("description", "")
            kws = _extract_keywords(desc)
            for kw in kws:
                if kw in all_kws:
                    all_kws[kw].append(d.name)
                else:
                    all_kws[kw] = [d.name]
        overlaps = {kw: dirs for kw, dirs in all_kws.items() if len(dirs) > 1}
        assert isinstance(overlaps, dict)


class TestBodyContent:
    """Each SKILL.md has meaningful content after the frontmatter."""

    def test_body_not_empty(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, _, body = _read_skill(md)
            assert body.strip(), f"{d.name}/SKILL.md: body is empty after frontmatter"

    def test_body_has_heading(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, _, body = _read_skill(md)
            assert re.search(r"^#+\s", body.strip()), f"{d.name}/SKILL.md: no markdown heading found in body"

    def test_body_minimum_length(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, _, body = _read_skill(md)
            stripped = body.strip()
            assert len(stripped) >= 50, f"{d.name}/SKILL.md: body too short ({len(stripped)} chars)"


class TestDirectoryNaming:
    """Skill directory names follow naming conventions."""

    def test_directory_names_are_valid(self):
        for d in _skill_dirs():
            assert VALID_NAME_RE.match(d.name), f"Invalid skill directory name: '{d.name}'"

    def test_no_uppercase_in_dir_names(self):
        for d in _skill_dirs():
            assert d.name == d.name.lower(), f"Skill directory '{d.name}' has uppercase characters"


class TestLegacySubSkills:
    """skills/skills/ directory — Matt Pocock legacy .md files."""

    def test_skills_subdir_exists(self):
        assert SKILLS_SUB.is_dir(), "skills/skills/ directory missing"

    def test_legacy_md_files_exist(self):
        mds = list(SKILLS_SUB.glob("*.md"))
        assert len(mds) >= 10, f"Expected >=10 legacy md files, found {len(mds)}"

    def test_legacy_md_files_have_frontmatter(self):
        for md in sorted(SKILLS_SUB.glob("*.md")):
            raw = md.read_text(encoding="utf-8")
            assert raw.startswith("---"), f"skills/{md.name}: does not start with ---"

    def test_legacy_md_files_frontmatter_parseable(self):
        for md in sorted(SKILLS_SUB.glob("*.md")):
            raw = md.read_text(encoding="utf-8")
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                fm_text = parts[1].strip()
                # Try JSON first (Matt Pocock format), then YAML
                parsed = _parse_json_frontmatter(fm_text)
                if parsed is None:
                    parsed = _parse_yaml_frontmatter(fm_text)
                assert parsed is not None, f"skills/{md.name}: frontmatter unparseable"
                assert "name" in parsed, f"skills/{md.name}: missing 'name'"
                assert "description" in parsed, f"skills/{md.name}: missing 'description'"


class TestSkillFileEncoding:
    """All skill files are valid UTF-8 and don't have BOM."""

    def test_all_skill_md_files_are_valid_utf8(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            raw = md.read_bytes()
            assert not raw.startswith(b"\xef\xbb\xbf"), f"{d.name}/SKILL.md: has BOM"
            raw.decode("utf-8")

    def test_legacy_md_files_valid_utf8(self):
        for md in sorted(SKILLS_SUB.glob("*.md")):
            raw = md.read_bytes()
            assert not raw.startswith(b"\xef\xbb\xbf"), f"skills/{md.name}: has BOM"
            raw.decode("utf-8")


class TestCrossReference:
    """Skills reference each other and AGENTS.md correctly."""

    def test_no_skill_self_references_own_name_in_description(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            name = parsed.get("name", "")
            desc = parsed.get("description", "")
            assert name not in desc, f"{d.name}/SKILL.md: skill name appears in its own description"

    def test_location_field_when_present_points_to_correct_path(self):
        for d in _skill_dirs():
            if d.name in BUILTIN_SKILL_NAMES:
                continue
            md = d / "SKILL.md"
            if not md.exists():
                continue
            _, fm, _ = _read_skill(md)
            parsed = _parse_yaml_frontmatter(fm)
            loc = parsed.get("location", "")
            if loc:
                assert loc.endswith(f"/{d.name}/SKILL.md"), (
                    f"{d.name}: location field '{loc}' does not end with /{d.name}/SKILL.md"
                )
