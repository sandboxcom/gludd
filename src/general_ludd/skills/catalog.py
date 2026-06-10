"""Skills catalog: search and discover curated skills from community sources.

Provides a catalog of curated AI coding skills that can be searched,
downloaded, and installed into the general-ludd-agent config directory.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class CatalogSkillEntry(BaseModel):
    name: str
    description: str = ""
    source: str = ""
    source_url: str = ""
    tags: list[str] = Field(default_factory=list)
    category: str = ""
    body_preview: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class SkillCatalog:
    """Search and discover curated skills from community sources."""

    def __init__(self) -> None:
        pass

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
    "mp-diagnose": CatalogSkillEntry(
        name="mp-diagnose",
        description=(
            "Disciplined diagnosis loop for hard bugs: reproduce, minimise, "
            "hypothesise, instrument, fix, regression-test. Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/engineering/diagnose",
        category="engineering",
        tags=["mattpocock", "debugging", "diagnosis", "engineering"],
        body_preview=(
            "# Diagnose\n\n"
            "A 6-phase discipline for hard bugs:\n\n"
            "1. **Build a feedback loop** — Spend disproportionate effort here. "
            "Use failing tests, curl scripts, CLI invocations, headless browsers, "
            "throwaway harnesses. Iterate to make the loop faster and more deterministic.\n"
            "2. **Reproduce** — Run the loop. Confirm the failure matches the user's description.\n"
            "3. **Hypothesise** — Generate 3-5 ranked, falsifiable hypotheses. Show to user before testing.\n"
            "4. **Instrument** — One probe per hypothesis, one variable at a time. "
            "Prefer debugger/REPL over targeted logs. Tag debug logs with unique prefix.\n"
            "5. **Fix + regression test** — Write regression test before fix. "
            "If no test seam exists, that itself is the finding.\n"
            "6. **Cleanup + post-mortem** — Verify repro gone, regression test passes, "
            "all instrumentation removed. Ask 'what would have prevented this bug?'\n"
        ),
    ),
    "mp-tdd": CatalogSkillEntry(
        name="mp-tdd",
        description=(
            "Test-driven development with red-green-refactor loop. "
            "Vertical slices, not horizontal. Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/engineering/tdd",
        category="engineering",
        tags=["mattpocock", "testing", "tdd", "engineering"],
        body_preview=(
            "# TDD\n\n"
            "Test-driven development with a red-green-refactor loop.\n\n"
            "**Anti-pattern: Horizontal Slices** — Don't write all tests first then all implementation. "
            "Use vertical slices (tracer bullets): one test, one implementation, repeat.\n\n"
            "**Workflow**:\n"
            "1. Planning — confirm interface changes, behaviors to test, design for testability\n"
            "2. Tracer Bullet — one test, confirm the path works\n"
            "3. Incremental Loop — RED then GREEN for each behavior, one at a time, minimal code only\n"
            "4. Refactor — after all tests pass, extract duplication, deepen modules. Never refactor while RED\n\n"
            "Tests verify behavior through public interfaces, not implementation details. "
            "Good tests are integration-style (read like specs). Bad tests are coupled to implementation.\n"
        ),
    ),
    "mp-grill-me": CatalogSkillEntry(
        name="mp-grill-me",
        description=(
            "Relentlessly interview the user about a plan or design until "
            "every branch of the decision tree is resolved. "
            "Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/productivity/grill-me",
        category="productivity",
        tags=["mattpocock", "planning", "interview", "productivity"],
        body_preview=(
            "# Grill Me\n\n"
            "Interview me relentlessly about every aspect of this plan until we reach a shared understanding. "
            "Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. "
            "For each question, provide your recommended answer. Ask questions one at a time. "
            "If a question can be answered by exploring the codebase, explore the codebase instead.\n"
        ),
    ),
    "mp-grill-with-docs": CatalogSkillEntry(
        name="mp-grill-with-docs",
        description=(
            "Grilling session that challenges your plan against the existing "
            "domain model, sharpens terminology, updates CONTEXT.md and ADRs "
            "inline. Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/engineering/grill-with-docs",
        category="engineering",
        tags=["mattpocock", "planning", "documentation", "engineering"],
        body_preview=(
            "# Grill With Docs\n\n"
            "Grilling session that challenges your plan against the existing domain model.\n\n"
            "- Interview the user relentlessly, one question at a time\n"
            "- If a question can be answered by exploring the codebase, explore instead\n"
            "- Challenge against CONTEXT.md glossary, sharpen fuzzy language\n"
            "- Discuss concrete scenarios, cross-reference with code\n"
            "- Update CONTEXT.md inline (glossary only, no implementation details)\n"
            "- Offer ADRs sparingly (only when hard to reverse + surprising without context + real trade-off)\n"
        ),
    ),
    "mp-caveman": CatalogSkillEntry(
        name="mp-caveman",
        description=(
            "Ultra-compressed communication mode. Cuts token usage by "
            "dropping filler while keeping full technical accuracy. "
            "Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/productivity/caveman",
        category="productivity",
        tags=["mattpocock", "communication", "tokens", "productivity"],
        body_preview=(
            "# Caveman Mode\n\n"
            "Ultra-compressed communication. Active every response once triggered.\n\n"
            "Rules:\n"
            "- Drop articles, filler, pleasantries, hedging\n"
            "- Fragments OK. Short synonyms. Abbreviate common terms\n"
            "- Use arrows for causality. Technical terms stay exact\n"
            "- Pattern: [thing] [action] [reason]. [next step].\n"
            "- Code blocks unchanged\n"
            "- Auto-clarity: temporarily drop caveman for security warnings, "
            "irreversible actions, multi-step sequences\n"
        ),
    ),
    "mp-handoff": CatalogSkillEntry(
        name="mp-handoff",
        description=(
            "Compact the current conversation into a handoff document so "
            "another agent can continue the work. Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/productivity/handoff",
        category="productivity",
        tags=["mattpocock", "handoff", "session", "productivity"],
        body_preview=(
            "# Handoff\n\n"
            "Write a handoff document summarizing the conversation for a fresh agent.\n\n"
            "- Save to OS temp directory (not workspace)\n"
            "- Include suggested skills section\n"
            "- Don't duplicate content in other artifacts (PRDs, plans, ADRs, "
            "issues, commits) — reference by path/URL\n"
            "- Redact sensitive info\n"
            "- Tailor to what the next session will focus on\n"
        ),
    ),
    "mp-to-prd": CatalogSkillEntry(
        name="mp-to-prd",
        description=(
            "Turn the current conversation context into a PRD and publish to "
            "the issue tracker. No interview — synthesizes what you already "
            "know. Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/engineering/to-prd",
        category="engineering",
        tags=["mattpocock", "prd", "planning", "engineering"],
        body_preview=(
            "# To PRD\n\n"
            "Turn the current conversation context into a PRD.\n\n"
            "Do NOT interview the user — synthesize what you already know.\n\n"
            "Process:\n"
            "1. Explore repo\n"
            "2. Sketch test seams, check with user\n"
            "3. Write PRD using template, publish to issue tracker with ready-for-agent label\n\n"
            "PRD sections: Problem Statement, Solution, User Stories (As a actor, I want feature, so that benefit), "
            "Implementation Decisions, Testing Decisions, Out of Scope, Further Notes\n"
        ),
    ),
    "mp-to-issues": CatalogSkillEntry(
        name="mp-to-issues",
        description=(
            "Break a plan, spec, or PRD into independently-grabbable issues "
            "using tracer-bullet vertical slices. Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/engineering/to-issues",
        category="engineering",
        tags=["mattpocock", "issues", "planning", "engineering"],
        body_preview=(
            "# To Issues\n\n"
            "Break a plan into independently-grabbable issues using vertical slices.\n\n"
            "Process:\n"
            "1. Gather context from conversation\n"
            "2. Explore codebase optionally\n"
            "3. Draft vertical slices (tracer bullets through ALL integration layers end-to-end)\n"
            "4. Quiz user on granularity, dependencies, HITL/AFK assignments\n"
            "5. Publish issues in dependency order\n\n"
            "Vertical slice rules: Each delivers a narrow but COMPLETE path through every layer. "
            "A completed slice is demoable on its own. Prefer many thin over few thick.\n"
        ),
    ),
    "mp-zoom-out": CatalogSkillEntry(
        name="mp-zoom-out",
        description=(
            "Zoom out and give broader context or a higher-level perspective "
            "on unfamiliar code. Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/engineering/zoom-out",
        category="engineering",
        tags=["mattpocock", "context", "exploration", "engineering"],
        body_preview=(
            "# Zoom Out\n\n"
            "Go up a layer of abstraction. Give a map of all the relevant modules and callers, "
            "using the project's domain glossary vocabulary. Useful when you're unfamiliar with "
            "a section of code or need to understand how it fits into the bigger picture.\n"
        ),
    ),
    "mp-improve-architecture": CatalogSkillEntry(
        name="mp-improve-architecture",
        description=(
            "Find deepening opportunities in a codebase, informed by domain "
            "language and ADRs. Consolidate tightly-coupled modules. "
            "Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/engineering/improve-codebase-architecture",
        category="engineering",
        tags=["mattpocock", "architecture", "refactoring", "engineering"],
        body_preview=(
            "# Improve Codebase Architecture\n\n"
            "Find deepening opportunities in a codebase.\n\n"
            "Process:\n"
            "1. Explore — read glossary/ADRs, walk codebase noting friction "
            "(shallow modules, leaked coupling, no locality, untested areas). Apply deletion test.\n"
            "2. Present candidates with before/after diagrams, recommendation strength. "
            "Do NOT propose interfaces yet — ask user which to explore.\n"
            "3. Grilling loop — walk design tree, update CONTEXT.md inline, offer ADRs sparingly.\n\n"
            "Key principles: deletion test, interface is the test surface, "
            "one adapter = hypothetical seam, two adapters = real seam.\n"
        ),
    ),
    "mp-teach": CatalogSkillEntry(
        name="mp-teach",
        description=(
            "Teach the user a new skill or concept using a stateful teaching "
            "workspace with lessons, learning records, and reference docs. "
            "Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/productivity/teach",
        category="productivity",
        tags=["mattpocock", "teaching", "learning", "productivity"],
        body_preview=(
            "# Teach\n\n"
            "Teach a new skill or concept using a stateful workspace.\n\n"
            "Workspace files: MISSION.md, reference/*.html (cheat sheets), RESOURCES.md, "
            "learning-records/*.md (key insights), lessons/*.html (self-contained lessons), NOTES.md.\n\n"
            "Philosophy: Knowledge (from high-trust resources) + Skills (interactive lessons) + "
            "Wisdom (community interaction). Always challenge 'just enough' (zone of proximal development).\n"
        ),
    ),
    "mp-write-a-skill": CatalogSkillEntry(
        name="mp-write-a-skill",
        description=(
            "Create new agent skills with proper structure, progressive "
            "disclosure, and bundled resources. Adapted from mattpocock/skills."
        ),
        source="mattpocock",
        source_url="https://github.com/mattpocock/skills/tree/main/skills/productivity/write-a-skill",
        category="productivity",
        tags=["mattpocock", "skills", "authoring", "productivity"],
        body_preview=(
            "# Write a Skill\n\n"
            "Create new agent skills with proper structure.\n\n"
            "Structure: skill-name/SKILL.md (required), REFERENCE.md, EXAMPLES.md, scripts/ (optional).\n\n"
            "Description requirements: Max 1024 chars, third person, first sentence = what it does, "
            "second sentence = 'Use when [triggers]'. The description is the ONLY thing the agent sees "
            "when deciding which skill to load.\n\n"
            "Split files when SKILL.md exceeds 100 lines. Add scripts for deterministic operations.\n"
        ),
    ),
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
        description=(
            "Performance optimization: profile first, optimize algorithms,"
            " cache wisely, avoid premature optimization"
        ),
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
    "medium-frontend-design": CatalogSkillEntry(
        name="medium-frontend-design",
        description=(
            "Production-grade UI generation escaping AI's default visual "
            "signature. Bold typography, intentional color, purposeful "
            "animations. Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="frontend",
        tags=[
            "frontend", "design", "ui", "css", "animations", "medium-2026",
        ],
        body_preview=(
            "# Frontend Design\n\n"
            "Escape distributional convergence — the statistical center of "
            "AI-generated design.\n\n"
            "Principles:\n"
            "1. Distinctive typography — avoid Inter/Roboto/system-ui as primary\n"
            "2. Intentional color — no default blue-purple gradients\n"
            "3. Purposeful animation — animate to communicate, not decorate\n"
            "4. Layout variety — break the card grid habit\n"
            "5. Visual signature — output should NOT look AI-generated\n\n"
            "Process: describe design system (tokens, scale, palette) before "
            "writing any code. Choose reference sites. Build tokens first, "
            "then components.\n"
        ),
    ),
    "medium-browser-use": CatalogSkillEntry(
        name="medium-browser-use",
        description=(
            "Live web and browser automation. Navigate, click, fill forms, "
            "extract JS-rendered content, screenshot. End-to-end QA and "
            "research. Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="automation",
        tags=[
            "browser", "automation", "qa", "testing", "scraping",
            "medium-2026",
        ],
        body_preview=(
            "# Browser Use\n\n"
            "Give the agent control of a headless browser.\n\n"
            "Capabilities: navigate URLs, click elements, fill forms, extract "
            "JS-rendered content, take screenshots, handle multi-step "
            "workflows.\n\n"
            "Use for: end-to-end QA, live research, form automation, "
            "deployment validation.\n"
        ),
    ),
    "medium-code-reviewer": CatalogSkillEntry(
        name="medium-code-reviewer",
        description=(
            "Automated code quality review: simplifiable logic, single "
            "responsibility, duplicated patterns, performance, dead code, "
            "naming. Fixes before presenting. Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="quality",
        tags=[
            "code-review", "quality", "simplification", "refactoring",
            "medium-2026",
        ],
        body_preview=(
            "# Code Reviewer\n\n"
            "Structured review pass before presenting code to user.\n\n"
            "Checklist: simplifiable logic, single responsibility (30-line "
            "limit), duplicated patterns (>2x = extract), performance "
            "(N+1, blocking I/O), dead code, naming clarity.\n\n"
            "The user sees the second draft, not the first.\n"
        ),
    ),
    "medium-remotion": CatalogSkillEntry(
        name="medium-remotion",
        description=(
            "React-based programmatic video creation. Product demos, release "
            "videos, explainer animations from natural language. Uses "
            "Remotion framework. Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="media",
        tags=[
            "video", "remotion", "react", "animation", "demos",
            "medium-2026",
        ],
        body_preview=(
            "# Remotion Video Generation\n\n"
            "Create videos programmatically using React components.\n"
            "Animation = state changing over time.\n\n"
            "Process: describe video -> generate Remotion component with "
            "useCurrentFrame() -> preview in Studio -> render to MP4.\n\n"
            "Use for: product demos, release videos, explainers, animated "
            "README headers. Keep under 60 seconds.\n"
        ),
    ),
    "medium-google-workspace": CatalogSkillEntry(
        name="medium-google-workspace",
        description=(
            "Unified Google Workspace automation. 50+ APIs: Gmail, Drive, "
            "Calendar, Docs, Sheets, Slides, Chat, Admin. Built-in MCP "
            "server. Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="automation",
        tags=[
            "google", "workspace", "gmail", "drive", "calendar",
            "automation", "medium-2026",
        ],
        body_preview=(
            "# Google Workspace (GWS)\n\n"
            "Unified interface to 50+ Google Workspace APIs.\n\n"
            "Setup: npm install -g @googleworkspace/cli && gws mcp "
            "-s drive,gmail,calendar,sheets\n\n"
            "Patterns: executive assistant (email+calendar), project manager "
            "(Sheets+Chat), IT admin (users+audit), sales (CRM+proposals).\n"
        ),
    ),
    "medium-valyu": CatalogSkillEntry(
        name="medium-valyu",
        description=(
            "Real-time web search and 36+ specialised data sources. SEC "
            "filings, PubMed, ChEMBL, FRED, patents. Grounded answers with "
            "citations. Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="data",
        tags=[
            "search", "data", "research", "sec", "pubmed", "finance",
            "medium-2026",
        ],
        body_preview=(
            "# Valyu\n\n"
            "36+ specialised data sources through single API.\n\n"
            "Sources: SEC filings, PubMed, ChEMBL (2.5M compounds), "
            "ClinicalTrials.gov, FRED, BLS, patent databases, academic "
            "publishers.\n\n"
            "FreshQA benchmark: 79% vs Google 39%. Finance: 73% vs 55%.\n"
            "Always surface sources. Never fabricate data.\n"
        ),
    ),
    "medium-antigravity-skills": CatalogSkillEntry(
        name="medium-antigravity-skills",
        description=(
            "1,234+ curated agent skills library. Brainstorming, "
            "architecture, debugging, API design, security, PR creation, "
            "documentation. Universal SKILL.md format. "
            "Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="meta",
        tags=[
            "skills", "library", "community", "antigravity", "medium-2026",
        ],
        body_preview=(
            "# Antigravity Awesome Skills\n\n"
            "1,234+ curated skills in universal SKILL.md format.\n\n"
            "Key skills: @brainstorming, @architecture, @debugging-strategies, "
            "@api-design-principles, @security-auditor, @lint-and-validate, "
            "@create-pr, @doc-coauthoring.\n\n"
            "Role bundles: Web Wizard, Security Engineer, Essentials.\n"
            "22K+ GitHub stars. v7.3.0.\n"
        ),
    ),
    "medium-planetscale": CatalogSkillEntry(
        name="medium-planetscale",
        description=(
            "Serverless MySQL/Postgres with schema branching, index-aware "
            "queries, migration workflows. Schema changes as reviewable, "
            "reversible code. Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="database",
        tags=[
            "database", "mysql", "planetscale", "schema", "migrations",
            "indexing", "medium-2026",
        ],
        body_preview=(
            "# PlanetScale Database Skills\n\n"
            "Branch-based schema changes, index-first design.\n\n"
            "Workflow: create branch -> design schema with indexes -> verify "
            "query coverage -> deploy request -> review.\n\n"
            "Rules: never SELECT *, design indexes with tables, estimate at "
            "10M rows, use DECIMAL for money.\n"
        ),
    ),
    "medium-shannon": CatalogSkillEntry(
        name="medium-shannon",
        description=(
            "Autonomous AI pentesting. 96.15% exploit success rate. 50+ "
            "vulnerability types across 5 OWASP categories. No false "
            "positives. Runs in Docker. Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="security",
        tags=[
            "security", "pentesting", "owasp", "xss", "injection",
            "authentication", "medium-2026",
        ],
        body_preview=(
            "# Shannon: Autonomous AI Pentester\n\n"
            "96.15% exploit success rate (XBOW benchmark, 100/104). No false "
            "positives.\n\n"
            "5-phase pipeline: Pre-Recon -> Recon -> Vulnerability Analysis "
            "(5 parallel agents) -> Exploitation -> Reporting.\n\n"
            "Covers: SQL injection, XSS, SSRF, broken authN, broken authZ. "
            "~1-1.5h per pentest, ~$50 with Sonnet. Docker-only.\n"
        ),
    ),
    "medium-excalidraw": CatalogSkillEntry(
        name="medium-excalidraw",
        description=(
            "Architecture diagram generation from natural language. "
            "Self-validating: generates JSON, renders PNG, reviews layout, "
            "fixes issues. Visual structure = conceptual structure. "
            "Source: unicodeveloper/medium-2026."
        ),
        source="unicodeveloper",
        source_url=(
            "https://medium.com/@unicodeveloper/"
            "10-must-have-skills-for-claude-and-any-coding-agent-in-2026"
            "-b5451b013051"
        ),
        category="documentation",
        tags=[
            "diagrams", "architecture", "excalidraw", "visual",
            "documentation", "medium-2026",
        ],
        body_preview=(
            "# Excalidraw Diagram Generator\n\n"
            "Production-quality diagrams from natural language.\n\n"
            "Design: visual structure maps to conceptual structure (fan-out "
            "for 1:N, timelines for sequential, convergence for aggregation). "
            "Include real code/data, not placeholders.\n\n"
            "Self-validation: generate JSON -> render PNG -> review layout -> "
            "fix issues -> present final.\n"
        ),
    ),
}
