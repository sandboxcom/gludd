"""Structural contract for the routed Java expert skill documentation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".opencode" / "skills" / "java-expert" / "SKILL.md"
FRAMEWORKS = SKILL.parent / "references" / "frameworks.md"
GO_SKILL = ROOT / ".opencode" / "skills" / "go-expert" / "SKILL.md"
GO_STDLIB = GO_SKILL.parent / "references" / "standard-library.md"
GO_ECOSYSTEM = GO_SKILL.parent / "references" / "ecosystem.md"


def test_java_skill_routes_framework_details_below_global_line_limit() -> None:
    """Keep the entrypoint compact while retaining framework guidance verbatim."""
    skill = SKILL.read_text(encoding="utf-8")
    framework_reference = FRAMEWORKS.read_text(encoding="utf-8")

    assert len(skill.splitlines()) < 2_500
    assert "[Java frameworks reference](references/frameworks.md)" in skill
    assert "## Frameworks" in framework_reference
    for marker in ("### Spring Boot\n", "### JPA / Hibernate\n", "**WireMock:**\n"):
        assert marker not in skill
        assert marker in framework_reference
    assert len(framework_reference.splitlines()) < 2_500


def test_go_skill_routes_large_details_below_global_line_limit() -> None:
    """Route the two largest Go references without losing their content."""
    skill = GO_SKILL.read_text(encoding="utf-8")
    standard_library = GO_STDLIB.read_text(encoding="utf-8")
    ecosystem = GO_ECOSYSTEM.read_text(encoding="utf-8")

    assert len(skill.splitlines()) < 2_500
    assert "[Go standard-library reference](references/standard-library.md)" in skill
    assert "[Go ecosystem reference](references/ecosystem.md)" in skill
    for marker, reference in (
        ("### 3.1 net/http\n", standard_library),
        ("### 9.1 Web Frameworks\n", ecosystem),
    ):
        assert marker not in skill
        assert marker in reference
    assert len(standard_library.splitlines()) < 2_500
    assert len(ecosystem.splitlines()) < 2_500
