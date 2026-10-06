"""Structural contract for the routed Java expert skill documentation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".opencode" / "skills" / "java-expert" / "SKILL.md"
FRAMEWORKS = SKILL.parent / "references" / "frameworks.md"


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
