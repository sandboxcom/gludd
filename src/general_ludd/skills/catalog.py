"""Skills catalog: search and discover curated skills from community sources.

Provides a catalog of curated AI coding skills that can be searched,
downloaded, and installed into the general-ludd-agent config directory.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CatalogSkillEntry(BaseModel):
    name: str
    description: str = ""
    source: str = ""
    source_url: str = ""
    tags: list[str] = Field(default_factory=list)
    category: str = ""
    body_preview: str = ""


class SkillCatalog:
    """Search and discover curated skills from community sources."""

    def __init__(self, cache_dir: str | None = None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else Path(
            "~/.cache/general-ludd/skills"
        ).expanduser()
        self._cache: list[CatalogSkillEntry] = []

    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> list[CatalogSkillEntry]:
        results: list[CatalogSkillEntry] = []
        query_lower = query.lower()

        for entry in _CURATED_SKILLS.values():
            if query_lower and query_lower not in entry.name.lower() and query_lower not in entry.description.lower():
                continue
            if tags and not any(t in entry.tags for t in tags):
                continue
            if category and entry.category != category:
                continue
            results.append(entry)
            if len(results) >= limit:
                break

        return results

    def get_skill(self, name: str) -> CatalogSkillEntry | None:
        return _CURATED_SKILLS.get(name)

    def download_skill(self, name: str, target_dir: str) -> Path | None:
        entry = _CURATED_SKILLS.get(name)
        if entry is None:
            return None

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)

        skill_file = target / f"{name}.md"
        skill_file.write_text(_build_skill_md(entry))
        logger.info("Downloaded skill %s to %s", name, skill_file)
        return skill_file

    def list_categories(self) -> list[str]:
        cats: set[str] = set()
        for entry in _CURATED_SKILLS.values():
            cats.add(entry.category)
        return sorted(cats)

    def list_tags(self) -> list[str]:
        tags: set[str] = set()
        for entry in _CURATED_SKILLS.values():
            tags.update(entry.tags)
        return sorted(tags)

    def install_skill(self, name: str, config_dir: str) -> Path | None:
        skills_dir = Path(config_dir) / "skills"
        return self.download_skill(name, str(skills_dir))


def _build_skill_md(entry: CatalogSkillEntry) -> str:
    frontmatter = {
        "name": entry.name,
        "description": entry.description,
        "tags": entry.tags,
    }
    if entry.category:
        frontmatter["category"] = entry.category

    lines = ["---"]
    lines.append(json.dumps(frontmatter, indent=2))
    lines.append("---")
    lines.append("")
    if entry.body_preview:
        lines.append(entry.body_preview)
    else:
        lines.append(f"# {entry.name}")
        lines.append("")
        lines.append(entry.description)
    return "\n".join(lines) + "\n"


_CURATED_SKILLS: dict[str, CatalogSkillEntry] = {
    "tdd-discipline": CatalogSkillEntry(
        name="tdd-discipline",
        description="Enforce test-driven development: write failing tests before implementation code",
        source="curated",
        category="methodology",
        tags=["testing", "tdd", "quality"],
        body_preview=(
            "# TDD Discipline\n\n"
            "Always follow the Red-Green-Refactor cycle:\n"
            "1. Write a failing test that describes the desired behavior\n"
            "2. Run tests to confirm the test fails\n"
            "3. Write the minimal implementation to make the test pass\n"
            "4. Run tests to confirm all tests pass\n"
            "5. Refactor if needed, keeping tests green\n"
            "6. Never skip steps. Never write implementation before tests."
        ),
    ),
    "security-first": CatalogSkillEntry(
        name="security-first",
        description="Security-first approach: never expose secrets, validate all inputs, use parameterized queries",
        source="curated",
        category="security",
        tags=["security", "secrets", "validation"],
        body_preview=(
            "# Security First\n\n"
            "Security rules for all code:\n"
            "- Never commit secrets, API keys, or tokens to source control\n"
            "- Always use parameterized queries for database operations\n"
            "- Validate and sanitize all user inputs\n"
            "- Use environment variables or secret managers for credentials\n"
            "- Never log sensitive data (passwords, tokens, PII)\n"
            "- Apply principle of least privilege to all access patterns"
        ),
    ),
    "git-conventional-commits": CatalogSkillEntry(
        name="git-conventional-commits",
        description="Enforce conventional commit messages with structured format",
        source="curated",
        category="git",
        tags=["git", "commits", "conventions"],
        body_preview=(
            "# Conventional Commits\n\n"
            "All commit messages must follow the Conventional Commits format:\n"
            "```\n"
            "type(scope): description\n"
            "```\n"
            "Types: feat, fix, docs, style, refactor, test, chore, perf\n"
            "Scopes: optional but recommended for larger repos"
        ),
    ),
    "code-review-checklist": CatalogSkillEntry(
        name="code-review-checklist",
        description="Systematic code review checklist covering correctness, security, performance, and style",
        source="curated",
        category="quality",
        tags=["review", "quality", "checklist"],
        body_preview=(
            "# Code Review Checklist\n\n"
            "Before approving any code review, verify:\n"
            "- Correctness: Does the code do what it claims?\n"
            "- Tests: Are there tests? Do they cover edge cases?\n"
            "- Security: No hardcoded secrets, input validation, SQL injection prevention\n"
            "- Performance: No obvious N+1 queries, unnecessary allocations\n"
            "- Style: Follows project conventions, no dead code\n"
            "- Documentation: Public APIs documented, complex logic explained"
        ),
    ),
    "error-handling-patterns": CatalogSkillEntry(
        name="error-handling-patterns",
        description="Robust error handling: use specific exceptions, always clean up resources, provide context",
        source="curated",
        category="patterns",
        tags=["errors", "exceptions", "patterns"],
        body_preview=(
            "# Error Handling Patterns\n\n"
            "Error handling rules:\n"
            "- Catch specific exceptions, never bare except\n"
            "- Always clean up resources (files, connections, locks)\n"
            "- Provide context in error messages (what operation failed, on what)\n"
            "- Use custom exception classes for domain errors\n"
            "- Log errors with enough context to debug\n"
            "- Never silently swallow exceptions"
        ),
    ),
    "api-design-rest": CatalogSkillEntry(
        name="api-design-rest",
        description="REST API design best practices: proper HTTP methods, status codes, pagination, versioning",
        source="curated",
        category="patterns",
        tags=["api", "rest", "http"],
        body_preview=(
            "# REST API Design\n\n"
            "API design rules:\n"
            "- Use plural nouns for resource endpoints (/users, not /user)\n"
            "- Use correct HTTP methods (GET=read, POST=create, PUT=replace, PATCH=update, DELETE=remove)\n"
            "- Return appropriate status codes (201 for creation, 204 for deletion, 422 for validation)\n"
            "- Paginate list endpoints with cursor-based pagination\n"
            "- Version APIs via URL path (/v1/) or Accept header\n"
            "- Always validate request body against a schema"
        ),
    ),
    "database-migrations": CatalogSkillEntry(
        name="database-migrations",
        description="Safe database migration patterns: always reversible, no data loss, test up and down",
        source="curated",
        category="database",
        tags=["database", "migrations", "schema"],
        body_preview=(
            "# Database Migrations\n\n"
            "Migration rules:\n"
            "- Every migration must be reversible (implement downgrade)\n"
            "- Never modify an existing migration after it's been applied\n"
            "- Test both upgrade and downgrade paths\n"
            "- Add data migrations separately from schema migrations\n"
            "- Use transactions where possible\n"
            "- Never delete data without a backup migration first"
        ),
    ),
    "async-patterns": CatalogSkillEntry(
        name="async-patterns",
        description="Async/await best practices: avoid blocking calls, use connection pools, handle cancellation",
        source="curated",
        category="patterns",
        tags=["async", "concurrency", "performance"],
        body_preview=(
            "# Async Patterns\n\n"
            "Async rules:\n"
            "- Never call blocking I/O in async functions (use run_in_executor)\n"
            "- Use connection pools for database and HTTP clients\n"
            "- Handle asyncio.CancelledError gracefully\n"
            "- Use asyncio.timeout for operations that may hang\n"
            "- Prefer asyncio.gather for concurrent operations\n"
            "- Always await coroutines, never fire-and-forget"
        ),
    ),
    "logging-observability": CatalogSkillEntry(
        name="logging-observability",
        description="Structured logging and observability: use correlation IDs, log levels correctly, trace requests",
        source="curated",
        category="operations",
        tags=["logging", "observability", "monitoring"],
        body_preview=(
            "# Logging and Observability\n\n"
            "Logging rules:\n"
            "- Use structured logging (JSON) in production\n"
            "- Include correlation IDs in all log entries\n"
            "- Use correct levels: DEBUG=dev detail, INFO=business events, WARN=recoverable issues, ERROR=failures\n"
            "- Never log secrets, tokens, or PII\n"
            "- Log at the boundary (entry/exit of services)\n"
            "- Use consistent field names across services"
        ),
    ),
    "performance-optimization": CatalogSkillEntry(
        name="performance-optimization",
        description="Performance optimization: profile first, optimize algorithms, cache wisely, avoid premature optimization",  # noqa: E501
        source="curated",
        category="performance",
        tags=["performance", "optimization", "profiling"],
        body_preview=(
            "# Performance Optimization\n\n"
            "Performance rules:\n"
            "- Profile before optimizing — never guess where the bottleneck is\n"
            "- Optimize algorithms and data structures first\n"
            "- Cache wisely — cache only what's expensive and frequently accessed\n"
            "- Batch database operations instead of N+1 queries\n"
            "- Use lazy loading for expensive resources\n"
            "- Set performance budgets and measure against them"
        ),
    ),
}
