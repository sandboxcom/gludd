"""
Python Expert Configuration for gludd

Not a model profile — a companion config defining which tools, linters,
patterns, and practices gludd agents MUST use when working with Python code.

Load this when gludd needs to: write Python code, review Python code,
configure a Python project, audit Python dependencies, or debug Python issues.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

# =============================================================================
# Ruff — Linter Configuration
# =============================================================================
class RuffRules(Enum):
    """Ruff rule sets gludd should enable for Python projects.

    Full rule list: https://docs.astral.sh/ruff/rules/
    """

    # Error detection — always on
    E = "pycodestyle errors"
    F = "pyflakes (unused imports, undefined names, etc.)"

    # Type checking
    TC = "flake8-type-checking — imports only used for type hints"

    # Security
    S = "flake8-bandit — security issues (asserts, pickle, subprocess, eval)"

    # Bug detection
    B = "flake8-bugbear — common bugs (mutable defaults, except pass, etc.)"
    B904 = "raise-without-from-inside-except — require raise ... from"
    C4 = "flake8-comprehensions — unnecessary comprehensions"
    T20 = "flake8-print — print statements in production code"

    # Code quality
    SIM = "flake8-simplify — opportunities to simplify code"
    PIE = "flake8-pie — miscellaneous lint rules"
    RET = "flake8-return — unnecessary returns/elifs"
    PL = "pylint — general code quality rules"

    # Naming
    N = "pep8-naming — naming conventions"

    # Imports
    I = "isort — import sorting"
    TID = "flake8-tidy-imports — banned imports"

    # Docstrings
    D = "pydocstyle — docstring conventions"

    # Performance
    PERF = "perflint — performance anti-patterns"
    FURB = "refurb — code modernization suggestions"

    # Async
    ASYNC = "flake8-async — async/await issues"
    RUF = "ruff-specific rules"

    # pandas
    PD = "pandas-vet — pandas anti-patterns"


@dataclass(frozen=True)
class RuffConfig:
    """Canonical ruff configuration for gludd-managed Python projects."""

    line_length: int = 100
    target_version: str = "py312"
    src_paths: tuple[str, ...] = ("src", "tests", "scripts")

    # Extend-select: rules beyond the defaults (E, F)
    # See RuffRules enum above for descriptions
    select: tuple[str, ...] = (
        "E", "F",      # Errors + pyflakes (enabled by default, listed for clarity)
        "B",           # bugbear
        "B904",        # raise-without-from
        "C4",          # comprehensions
        "I",           # isort
        "N",           # naming
        "PIE",         # miscellaneous
        "SIM",         # simplify
        "S",           # bandit (security)
        "T20",         # print
        "TC",          # type-checking imports
        "RET",         # unnecessary return
        "PERF",        # performance
        "ASYNC",       # async
        "RUF",         # ruff-specific
    )

    # Ignored rules with rationale:
    ignore: tuple[str, ...] = (
        "S101",  # assert — used in tests and invariant checks
        "S104",  # possible-binding-to-all-interfaces — dev servers
        "S301",  # pickle — acceptable for internal caching
        "D100",  # missing docstring in public module — not enforced
        "D104",  # missing docstring in public package — not enforced
        "D107",  # missing docstring in __init__ — verbose
        "D203",  # one-blank-line-before-class — conflicts with D211
        "D212",  # multi-line-summary-first-line — conflicts with D213
        "PLR0913",  # too-many-arguments — allowed when justified
        "TRY003",    # multiple-raise-in-try — allowed
    )

    # Paths to exclude from linting:
    exclude: tuple[str, ...] = (
        ".git",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "alembic/versions",  # Auto-generated migrations
        "*.egg-info",
    )

    # Per-file rule ignores:
    per_file_ignores: dict[str, list[str]] = field(default_factory=lambda: {
        "tests/**/*.py": ["S101", "S301", "ARG001"],   # Allow assert, pickle, unused args
        "scripts/**/*.py": ["T201"],                     # Allow print in scripts
        "src/**/__init__.py": ["F401", "F811"],          # Re-exports and __all__
    })

    @property
    def toml_section(self) -> str:
        """Generate [tool.ruff] section for pyproject.toml."""
        lines = ["[tool.ruff]"]
        lines.append(f'line-length = {self.line_length}')
        lines.append(f'target-version = "{self.target_version}"')
        lines.append(f'src = {list(self.src_paths)}')
        lines.append("")
        lines.append("[tool.ruff.lint]")
        lines.append(f'select = {list(self.select)}')
        lines.append(f'ignore = {list(self.ignore)}')
        lines.append(f'exclude = {list(self.exclude)}')
        lines.append("")
        lines.append("[tool.ruff.lint.per-file-ignores]")
        for path, rules in self.per_file_ignores.items():
            lines.append(f'"{path}" = {rules}')
        lines.append("")
        lines.append("[tool.ruff.format]")
        lines.append(f'quote-style = "double"')
        lines.append(f'indent-style = "space"')
        lines.append(f'skip-magic-trailing-comma = false')
        lines.append(f'line-ending = "auto"')
        return "\n".join(lines)


# =============================================================================
# MyPy — Type Checking Configuration
# =============================================================================
@dataclass(frozen=True)
class MypyConfig:
    """Mypy strictness profile for gludd-managed Python projects.

    Target: strict mode. Pragma: no `# type: ignore` in committed code.
    See AGENTS.md Guardrail Integrity Policy.
    """

    python_version: str = "3.12"
    strict: bool = True

    # When strict=True, these are implicitly enabled:
    # --warn-unused-configs
    # --disallow-any-generics
    # --disallow-subclassing-any
    # --disallow-untyped-calls
    # --disallow-untyped-defs
    # --disallow-incomplete-defs
    # --check-untyped-defs
    # --disallow-untyped-decorators
    # --warn-redundant-casts
    # --warn-unused-ignores
    # --warn-return-any
    # --no-implicit-reexport
    # --strict-equality
    # --extra-checks

    # Additional strictness beyond --strict:
    extra: tuple[str, ...] = (
        "--enable-error-code=ignore-without-code",
        "--enable-error-code=redundant-expr",
        "--enable-error-code=truthy-bool",
        '--enable-error-code=unused-awaitable',
    )

    # Packages to type-check:
    # If using namespace packages under src/, list explicitly:
    packages: tuple[str, ...] = ()

    # Per-module overrides (rare; prefer fixing the code):
    module_overrides: ClassVar[dict[str, list[str]]] = {}
    # Example:
    # "tests.*": ["--disable-error-code=attr-defined"],

    @property
    def toml_section(self) -> str:
        """Generate [tool.mypy] section for pyproject.toml."""
        lines = ["[tool.mypy]"]
        lines.append(f'python_version = "{self.python_version}"')
        lines.append("strict = true")
        if self.extra:
            lines.append(f'enable_error_code = {list(self.extra)}')
        if self.packages:
            lines.append(f'packages = {list(self.packages)}')
        return "\n".join(lines)


# =============================================================================
# Pytest — Testing Configuration
# =============================================================================
@dataclass(frozen=True)
class PytestConfig:
    """Pytest best practices for gludd-managed projects."""

    testpaths: tuple[str, ...] = ("tests",)
    python_files: str = "test_*.py"
    python_classes: str = "Test*"
    python_functions: str = "test_*"

    # Default options for CI:
    ci_options: str = (
        "-ra"          # Show all test results (except passes)
        " -q"          # Quiet mode
        " --strict-markers"  # Fail on unknown markers
        " --strict-config"   # Fail on unknown config
        " --tb=short"        # Short traceback
        " --color=yes"
    )

    # Default options for local dev:
    dev_options: str = (
        "-ra"
        " --strict-markers"
        " --strict-config"
        " --tb=long"
        " -x"           # Stop on first failure
        " --ff"         # Run failures first
    )

    # Custom markers — enforced by --strict-markers:
    markers: tuple[str, ...] = (
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
        "integration: marks tests as integration tests",
        "unit: marks tests as unit tests",
        "e2e: marks tests as end-to-end tests",
        "smoke: marks tests as smoke tests (quick sanity check)",
    )

    # Pytest plugins gludd should consider (opt-in per project):
    recommended_plugins: tuple[str, ...] = (
        "pytest-cov",          # Coverage integration
        "pytest-xdist",        # Parallel test execution
        "pytest-timeout",      # Per-test timeouts
        "pytest-mock",         # mocker fixture (prefer over unittest.mock.patch)
        "pytest-asyncio",      # Async test support
        "pytest-sugar",        # Better output formatting (local dev)
        "pytest-randomly",     # Randomize test order (CI)
    )

    @property
    def toml_section(self) -> str:
        """Generate [tool.pytest.ini_options] for pyproject.toml."""
        lines = ["[tool.pytest.ini_options]"]
        lines.append(f'testpaths = {list(self.testpaths)}')
        lines.append(f'python_files = "{self.python_files}"')
        lines.append(f'python_classes = "{self.python_classes}"')
        lines.append(f'python_functions = "{self.python_functions}"')
        # CI options as a single addopts line:
        lines.append(f'addopts = """{self.ci_options}"""')
        lines.append(f'markers = [')
        for marker in self.markers:
            lines.append(f'    "{marker}",')
        lines.append(f']')
        return "\n".join(lines)


# =============================================================================
# Coverage Configuration
# =============================================================================
@dataclass(frozen=True)
class CoverageConfig:
    """Coverage.py best practices."""

    branch: bool = True      # Branch coverage, not just line
    source: tuple[str, ...] = ("src",)
    fail_under: int = 85     # Minimum coverage percentage
    precision: int = 2

    # Files/directories to omit from coverage:
    omit: tuple[str, ...] = (
        "tests/*",
        "*/migrations/*",
        "*/__init__.py",     # Often just re-exports
        "setup.py",
    )

    # Lines to exclude from coverage calculation:
    exclude_lines: tuple[str, ...] = (
        "pragma: no cover",
        "if TYPE_CHECKING:",
        "raise NotImplementedError",
        "if __name__ == .__main__.:",
        "class .*\\bProtocol\\):",
        "@(abc\\.)?abstractmethod",
    )

    @property
    def toml_section(self) -> str:
        """Generate [tool.coverage.report] for pyproject.toml."""
        lines = ["[tool.coverage.run]"]
        lines.append(f'branch = {"true" if self.branch else "false"}')
        if self.source:
            lines.append(f'source = {list(self.source)}')
        if self.omit:
            lines.append(f'omit = {list(self.omit)}')
        lines.append("")
        lines.append("[tool.coverage.report]")
        lines.append(f"fail_under = {self.fail_under}")
        lines.append(f"precision = {self.precision}")
        lines.append("skip_covered = true")
        lines.append(f'exclude_lines = [')
        for excl in self.exclude_lines:
            lines.append(f'    "{excl}",')
        lines.append(f']')
        return "\n".join(lines)


# =============================================================================
# Virtual Environment Management
# =============================================================================
class VirtualEnvTool(Enum):
    UV = "uv"          # Fast, Rust-based, pip-compatible (RECOMMENDED)
    VENV = "venv"      # stdlib, always available, no lock file support
    PIPENV = "pipenv"  # Pipfile/Pipfile.lock, slow resolution
    POETRY = "poetry"  # pyproject.toml + poetry.lock, full workflow
    CONDA = "conda"    # Cross-language, binary packages, large ecosystem

    @classmethod
    def recommended(cls) -> "VirtualEnvTool":
        """gludd's default recommendation for new projects."""
        return cls.UV


@dataclass(frozen=True)
class VirtualEnvConfig:
    """Virtual environment management conventions."""

    tool: VirtualEnvTool = VirtualEnvTool.UV
    python_version: str = "3.12"
    location: str = ".venv"        # Virtual environment directory name

    # uv-specific:
    uv_sync_flags: str = "--frozen"  # CI: exact reproduction
    uv_install_flags: str = ""       # Dev: allow resolution

    # Dependency files to maintain (uv/pip):
    dependency_files: tuple[str, ...] = (
        "pyproject.toml",         # Direct dependencies
        "requirements-dev.txt",   # Dev dependencies (pinned, if not using pyproject extras)
        # "requirements.txt",     # Production (pin with --hash for security)
    )

    # Lock file conventions:
    lock_files: tuple[str, ...] = (
        "uv.lock",                # uv lock file
        # "poetry.lock",          # Poetry lock file
        # "Pipfile.lock",         # Pipenv lock file
    )


# =============================================================================
# Project Structure Template
# =============================================================================
@dataclass(frozen=True)
class ProjectStructure:
    """Canonical project structure for gludd-managed Python projects."""

    # Layout preference: src-layout (avoids accidental imports)
    src_layout: bool = True

    # Required directories:
    required_dirs: ClassVar[tuple[str, ...]] = (
        "src/",
        "tests/",
        "tests/unit/",
        "tests/integration/",
        "tests/fixtures/",
        "scripts/",
    )

    # Required files:
    required_files: ClassVar[tuple[str, ...]] = (
        "pyproject.toml",
        "README.md",
        "LICENSE",
        ".gitignore",
    )

    # Recommended optional files:
    recommended_files: ClassVar[tuple[str, ...]] = (
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        ".pre-commit-config.yaml",
    )

    @property
    def src_path(self) -> str:
        return "src/" if self.src_layout else ""

    def get_package_init(self, project_name: str) -> str:
        """Generate canonical __init__.py content."""
        return (
            f'"""{project_name} — <description>."""\n'
            f"\n"
            f'__version__ = "0.1.0"\n'
            f'__all__: list[str] = []\n'
        )

    def get_test_init(self) -> str:
        """Generate canonical conftest.py content."""
        return (
            "import pytest\n"
            "\n"
            "# Global fixtures go here — they cascade to all subdirectories.\n"
            "# Per-package fixtures go in tests/<package>/conftest.py.\n"
        )


# =============================================================================
# Pre-Commit Hooks Configuration
# =============================================================================
@dataclass(frozen=True)
class PreCommitConfig:
    """Pre-commit hooks for gludd-managed Python projects."""

    # Hook configuration — ordered by speed (fastest first):
    hooks: ClassVar[tuple[dict, ...]] = (
        {
            "repo": "https://github.com/pre-commit/pre-commit-hooks",
            "rev": "v4.6.0",
            "hooks": [
                {"id": "trailing-whitespace"},
                {"id": "end-of-file-fixer"},
                {"id": "check-yaml"},
                {"id": "check-toml"},
                {"id": "check-added-large-files", "args": ["--maxkb=500"]},
                {"id": "check-merge-conflict"},
                {"id": "detect-private-key"},
                {"id": "debug-statements"},
            ],
        },
        {
            "repo": "https://github.com/astral-sh/ruff-pre-commit",
            "rev": "v0.5.0",
            "hooks": [
                {"id": "ruff", "args": ["--fix"]},
                {"id": "ruff-format"},
            ],
        },
        {
            "repo": "https://github.com/pre-commit/mirrors-mypy",
            "rev": "v1.10.0",
            "hooks": [
                {"id": "mypy", "args": ["--strict"]},
            ],
        },
        {
            "repo": "https://github.com/Yelp/detect-secrets",
            "rev": "v1.5.0",
            "hooks": [
                {"id": "detect-secrets"},
            ],
        },
    )


# =============================================================================
# Dependency Management Patterns
# =============================================================================
@dataclass(frozen=True)
class DepManagement:
    """Dependency management rules for gludd-managed projects."""

    # Rules for production dependencies:
    production_deps: ClassVar[tuple[str, ...]] = (
        "Pin with >= lower bound only in pyproject.toml",
        "Lock exact versions in lock file (uv.lock / poetry.lock)",
        "For pip: use requirements.txt with pinned versions + hashes",
        "Never use >= without an upper bound for dependencies with known breaking changes",
        "Prefer stdlib over transitive deps — every dep is a supply-chain risk",
    )

    # Rules for dev dependencies:
    dev_deps: ClassVar[tuple[str, ...]] = (
        "Keep in [project.optional-dependencies] dev group",
        "Pin loosely (>=) — dev deps are not shipped",
        "Include: pytest, ruff, mypy, coverage, pre-commit",
        "Exclude from wheel metadata (not needed at runtime)",
    )

    # Security rules:
    security_rules: ClassVar[tuple[str, ...]] = (
        "Run pip-audit or safety on every CI run",
        "Pin hashes in requirements.txt for production installs",
        "Use --require-hashes in pip install for production",
        "Never commit .env files or secrets to the repo",
        "Run bandit as part of CI lint step",
        "Check for dependency confusion: private packages use --index-url",
    )


# =============================================================================
# Code Quality Thresholds
# =============================================================================
@dataclass(frozen=True)
class QualityThresholds:
    """Minimum quality bars for gludd-managed Python projects."""

    # Lint: zero errors
    ruff_errors: int = 0

    # Typecheck: zero errors (strict mode)
    mypy_errors: int = 0

    # Coverage: per-file minimum
    coverage_line_min: int = 80   # Per-file minimum
    coverage_branch_min: int = 70  # Per-file branch minimum
    coverage_overall_min: int = 85  # Project overall

    # Test collection: zero errors
    collection_errors: int = 0

    # Complexity (radon/mccabe):
    max_cyclomatic_complexity: int = 10  # Per function
    max_cognitive_complexity: int = 15   # Per function

    # Docstring coverage (interrogate):
    docstring_coverage_min: int = 80  # Percentage of public API documented


# =============================================================================
# Python Version Support Policy
# =============================================================================
@dataclass(frozen=True)
class VersionPolicy:
    """Python version support rules."""

    # Target: latest stable CPython
    target: str = "3.12"

    # Drop support when a version reaches end-of-life:
    # https://devguide.python.org/versions/
    support_window: str = (
        "Support the same Python versions as the CPython EOL schedule — "
        "typically the latest 3 minor releases. When a version reaches EOL, "
        "drop it and bump requires-python."
    )

    # currently_supported at time of writing (2026-07):
    currently_supported: ClassVar[tuple[str, ...]] = (
        "3.12",  # Stable, supported until 2028-10
        "3.11",  # Security fixes until 2027-10
        "3.10",  # Security fixes until 2026-10
    )

    # Experimental:
    experimental: ClassVar[tuple[str, ...]] = (
        "3.13",  # Free-threading experimental, JIT experimental
    )


# =============================================================================
# Tool-Availability Table
# =============================================================================
@dataclass(frozen=True)
class ToolAvailability:
    """Which tools are available in gludd's execution environment."""

    # Installed via uv/pip:
    installed: ClassVar[tuple[str, ...]] = (
        "ruff",          # Linter + formatter
        "mypy",          # Type checker
        "pytest",        # Test runner
        "coverage",      # Coverage measurement
        "bandit",        # Security linting
        "pip-audit",     # Dependency vulnerability scanning
        "detect-secrets", # Secrets scanning (pre-commit hook)
    )

    # Available via make targets (wrapped):
    available_via_make: ClassVar[tuple[str, ...]] = (
        "black",         # via ruff format
        "isort",         # via ruff (I rules)
        "flake8",        # via ruff (F rules)
        "pylint",        # via ruff (PL rules)
        "pyright",       # not installed; use mypy
    )

    # NOT available / DO NOT USE:
    unavailable: ClassVar[tuple[str, ...]] = (
        "pip",           # Use uv pip or make sync
        "python",        # Use make test, make test-unit, etc.
        "pylint",        # Use ruff (PL rules)
        "flake8",        # Use ruff
        "black",         # Use ruff format
        "isort",         # Use ruff
    )


# =============================================================================
# Aggregate Configuration
# =============================================================================
@dataclass(frozen=True)
class PythonExpertConfig:
    """Master configuration aggregating all Python tooling defaults.

    This is the single source of truth for how gludd should configure,
    lint, type-check, test, and audit Python code.
    """

    ruff: RuffConfig = field(default_factory=RuffConfig)
    mypy: MypyConfig = field(default_factory=MypyConfig)
    pytest: PytestConfig = field(default_factory=PytestConfig)
    coverage: CoverageConfig = field(default_factory=CoverageConfig)
    venv: VirtualEnvConfig = field(default_factory=VirtualEnvConfig)
    structure: ProjectStructure = field(default_factory=ProjectStructure)
    quality: QualityThresholds = field(default_factory=QualityThresholds)

    def generate_pyproject_toml(self, project_name: str) -> str:
        """Generate a complete pyproject.toml for a new Python project."""
        sections = [
            "[build-system]",
            'requires = ["hatchling"]',
            'build-backend = "hatchling.build"',
            "",
            "[project]",
            f'name = "{project_name}"',
            f'version = "0.1.0"',
            f'requires-python = ">={self.venv.python_version}"',
            f"dependencies = []",
            "",
            "[project.optional-dependencies]",
            'dev = ["pytest>=8.0", "ruff>=0.5", "mypy>=1.10", "coverage>=7.0"]',
            "",
            self.ruff.toml_section,
            "",
            self.mypy.toml_section,
            "",
            self.pytest.toml_section,
            "",
            self.coverage.toml_section,
        ]
        return "\n".join(sections)


# Singleton instance for easy import:
DEFAULT = PythonExpertConfig()

# =============================================================================
# Pattern Reference: When gludd encounters Python code, apply these rules
# =============================================================================
PATTERNS: tuple[str, ...] = (
    # Editing Python
    "1. TDD: test file exists before touching src/ (enforced by enforce-tdd.ts)",
    "2. No # type: ignore, # noqa, or any suppression comment (enforced)",
    "3. No Any in type annotations — use object, Protocol, or concrete types",
    "4. Use context managers for all resource management",
    "5. Prefer dataclass/attrs/pydantic over hand-written __init__",

    # Linting
    "6. ruff check + ruff format before every commit",
    "7. mypy --strict before every commit (zero errors)",
    "8. No lint-suppression comments in committed code",

    # Testing
    "9. Write tests FIRST (red-green-refactor)",
    "10. Every src/ file has a corresponding tests/ file",
    "11. pytest with --strict-markers, no xfail without a tracking issue",
    "12. Fixtures scoped to 'function' unless explicit reason for wider scope",

    # Security
    "13. pip-audit on every CI run",
    "14. bandit on every CI run",
    "15. Never use eval(), exec(), pickle with untrusted data",
    "16. Run detect-secrets pre-commit hook",

    # Imports
    "17. Absolute imports (from package.module import name)",
    "18. Group: stdlib, third-party, first-party (enforced by ruff I)",
    "19. No circular imports — restructure or use lazy imports",
    "20. __init__.py re-exports define the public API via __all__",

    # Async
    "21. Use asyncio for I/O-bound work, multiprocessing for CPU-bound",
    "22. Never call blocking functions in async code (use run_in_executor)",
    "23. Use asyncio.TaskGroup (3.11+) for structured concurrency",
)
