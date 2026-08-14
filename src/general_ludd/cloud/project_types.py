"""Comprehensive registry of software project types for the SoftwareGenerator.

Each :class:`ProjectType` defines everything the multi-step LLM pipeline needs
to produce a complete, validated, runnable output for a given project category.

New types can be added at runtime via :func:`register_project_type` without
modifying core generation code.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from general_ludd.schemas.benchmark import TaskRole

_ROLE_DEFAULTS: dict[str, tuple[TaskRole, ...]] = {
    "game": (TaskRole.PLANNER, TaskRole.CODER, TaskRole.REVIEWER),
    "web": (TaskRole.PLANNER, TaskRole.CODER),
    "cli": (TaskRole.CODER, TaskRole.REVIEWER),
    "library": (TaskRole.CODER,),
}

_TECH_STACK_DEFAULTS: dict[str, tuple[str, ...]] = {
    "game": ("pygame", "python"),
    "web": ("html", "css", "javascript"),
    "cli": ("click", "python"),
    "library": ("python",),
}

# ---------------------------------------------------------------------------
# ProjectType dataclass — detailed per-type pipeline specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectType:
    """Specification for one category of software project the pipeline can generate.

    Fields:
        type_id: Unique slug identifier (e.g. ``"game"``, ``"cli_tool"``).
        display_name: Human-readable label.
        default_entry_point: Default filename for the generated entry point.
        output_structure: Mapping of expected output files to descriptions.
        validation_rules: Ordered list of validation checks to run.
        prompt_template_planner: Template for the planner LLM step.
        prompt_template_coder: Template for the coder LLM step.
        acceptance_criteria: Human-readable must-pass checks.
        suggested_model_roles: Mapping of ``TaskRole`` values to recommended
            model categories (e.g. ``"reasoning"``, ``"coding"``).
        token_budget_estimate: Approximate max tokens for the coder step.
    """

    type_id: str
    display_name: str
    default_entry_point: str
    description: str = ""
    output_extension: str = ".py"
    output_structure: dict[str, str] = field(default_factory=dict)
    required_imports: list[str] = field(default_factory=list)
    validation_rules: list[str] = field(default_factory=list)
    prompt_template_planner: str = ""
    prompt_template_coder: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    suggested_model_roles: dict[str, str] = field(default_factory=dict)
    token_budget_estimate: int = 4000
    validate: Callable[[str], bool] | None = None


# ---------------------------------------------------------------------------
# ProjectSpec - high-level project description for the SoftwareGenerator
# ---------------------------------------------------------------------------


@dataclass
class ProjectSpec:
    """High-level specification for a software project to generate.

    Attribute defaults (``roles``, ``tech_stack``) are derived from
    *project_type* when not explicitly provided.
    """

    project_type: str
    name: str = ""
    description: str = ""
    prompt_template: str = ""
    roles: tuple[TaskRole, ...] = field(default_factory=tuple)
    tech_stack: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.roles and self.project_type in _ROLE_DEFAULTS:
            object.__setattr__(self, "roles", _ROLE_DEFAULTS[self.project_type])
        if not self.tech_stack and self.project_type in _TECH_STACK_DEFAULTS:
            object.__setattr__(self, "tech_stack", _TECH_STACK_DEFAULTS[self.project_type])


# ---------------------------------------------------------------------------
# Prompt template building blocks
# ---------------------------------------------------------------------------

_PLANNER_PREAMBLE = (
    "You are a senior software architect. Given the user's request, produce "
    "a detailed implementation plan. Identify the exact files to create, "
    "their contents at a high level, the dependencies between them, and the "
    "order in which they should be built. Be specific about algorithms, "
    "data structures, and edge cases.\n\n"
)

_CODER_PREAMBLE = (
    "You are an expert software engineer. Write complete, self-contained, "
    "production-quality code based on the plan provided. Every file must be "
    "syntactically valid, importable, and runnable without modification. "
    "Include ALL imports. Handle errors gracefully. Write clean, readable "
    "code with appropriate comments.\n\n"
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_BASE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "game": {
        "type_id": "game",
        "display_name": "Pygame Game",
        "description": "A self-contained Pygame game with rendering, input handling, and a game loop.",
        "output_extension": ".py",
        "required_imports": ["pygame"],
        "default_entry_point": "game.py",
        "output_structure": {
            "game.py": "Self-contained pygame game with a main game loop, input handling, and rendering.",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
            "has_pygame_import",
            "has_main_game_loop",
            "has_input_event_handling",
            "has_display_initialization",
            "has_frame_limit",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **Game** (pygame).\n"
            "Constraints: self-contained single file, window 800x600, "
            "runs for at least 30 frames then exits, handles keyboard/mouse "
            "events, uses a main game loop with frame-limited rendering.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Game** (pygame).\n"
            "Implementation plan:\n{context}\n\n"
            "Write the complete game.py. It MUST be runnable with: python game.py\n"
            "Include: import pygame, pygame.init(), set_mode((800,600)), "
            "a while-running game loop with clock.tick(), event handling "
            "(QUIT, KEYDOWN), display.flip()/update(), and a frame counter "
            "that exits after >=30 frames."
        ),
        "acceptance_criteria": [
            "python game.py runs without import errors",
            "Game window opens at 800x600",
            "Game loop runs for at least 30 frames",
            "Keyboard/mouse events are handled",
            "pygame.display.flip or update is called",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
            "reviewer": "reasoning",
        },
        "token_budget_estimate": 4000,
    },
    "website": {
        "type_id": "website",
        "display_name": "Single-Page Website",
        "description": "A responsive single-page website with HTML, CSS, and vanilla JavaScript.",
        "output_extension": ".html",
        "required_imports": [],
        "default_entry_point": "index.html",
        "output_structure": {
            "index.html": "HTML structure with embedded or linked CSS and JavaScript.",
            "style.css": "CSS styling for the page.",
            "script.js": "JavaScript for interactivity.",
        },
        "validation_rules": [
            "html_valid",
            "css_valid",
            "js_syntax_valid",
            "all_files_present",
            "css_linked",
            "js_linked",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **Single-Page Website** (HTML/CSS/JS).\n"
            "Constraints: self-contained directory, responsive design, "
            "works in modern browsers with no build step, all assets local.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Website** (HTML/CSS/JS).\n"
            "Implementation plan:\n{context}\n\n"
            "Produce index.html, style.css, and script.js. The HTML must "
            "link to both CSS and JS files. Use semantic HTML5 elements. "
            "Make it responsive with CSS media queries or flexbox/grid. "
            "JavaScript must be vanilla (no frameworks)."
        ),
        "acceptance_criteria": [
            "index.html loads in browser without errors",
            "style.css is linked and applies styles",
            "script.js is linked and executes without errors",
            "Layout is responsive (test at 320px and 1200px)",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
        },
        "token_budget_estimate": 3000,
    },
    "scraper": {
        "type_id": "scraper",
        "display_name": "Web Scraper",
        "description": "A Python web scraper using requests and BeautifulSoup4 with polite rate limiting.",
        "output_extension": ".py",
        "required_imports": ["requests", "beautifulsoup4"],
        "default_entry_point": "scraper.py",
        "output_structure": {
            "scraper.py": "Self-contained Python web scraper.",
            "requirements.txt": "Dependencies (requests, beautifulsoup4).",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
            "has_http_client_import",
            "has_html_parsing",
            "has_output_writing",
            "has_error_handling",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **Web Scraper** (Python).\n"
            "Constraints: respects robots.txt, uses polite delays, handles "
            "HTTP errors, writes output to a structured format (CSV/JSON).\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Scraper** (Python).\n"
            "Implementation plan:\n{context}\n\n"
            "Use requests + BeautifulSoup4. Add User-Agent header. Include "
            "time.sleep() between requests. Parse HTML with BeautifulSoup. "
            "Write results to CSV or JSON. Handle HTTP errors gracefully. "
            "Add a main() function with argparse for URL and output options."
        ),
        "acceptance_criteria": [
            "python scraper.py --help shows usage",
            "Can fetch a known URL and extract data",
            "Handles HTTP 404 without crashing",
            "Output is written to a file",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
        },
        "token_budget_estimate": 3000,
    },
    "database_schema": {
        "type_id": "database_schema",
        "display_name": "Database Schema",
        "description": "A normalized SQL database schema with migrations and seed data.",
        "output_extension": ".sql",
        "required_imports": [],
        "default_entry_point": "schema.sql",
        "output_structure": {
            "schema.sql": "CREATE TABLE statements with constraints and indexes.",
            "migration.sql": "ALTER statements for versioning.",
            "seed.sql": "Sample data for testing.",
        },
        "validation_rules": [
            "valid_sql_syntax",
            "has_primary_keys",
            "has_foreign_keys",
            "has_indexes",
            "has_not_null_constraints",
            "migration_idempotent",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **Database Schema** (SQL).\n"
            "Constraints: normalized to 3NF, includes indexes on foreign keys "
            "and frequently queried columns, migration file is idempotent "
            "(uses IF NOT EXISTS), seed data covers edge cases.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Database Schema** (SQL).\n"
            "Implementation plan:\n{context}\n\n"
            "Produce schema.sql with CREATE TABLE statements including "
            "PRIMARY KEY, FOREIGN KEY, NOT NULL, UNIQUE constraints, and "
            "CREATE INDEX on foreign key columns. Produce migration.sql "
            "using IF NOT EXISTS for idempotency. Produce seed.sql with "
            "INSERT statements covering normal and edge-case data."
        ),
        "acceptance_criteria": [
            "schema.sql parses without syntax errors",
            "All tables have primary keys",
            "Foreign keys are declared",
            "Indexes exist on FK columns",
            "migration.sql uses IF NOT EXISTS",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "reasoning",
        },
        "token_budget_estimate": 2000,
    },
    "cli_tool": {
        "type_id": "cli_tool",
        "display_name": "CLI Tool",
        "description": "A command-line utility with argparse, proper exit codes, and --help support.",
        "output_extension": ".py",
        "required_imports": ["argparse"],
        "default_entry_point": "cli.py",
        "output_structure": {
            "cli.py": "Self-contained CLI utility entry point.",
            "setup.py": "Package installation metadata.",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
            "has_argparse_or_click",
            "has_main_function",
            "has_help_text",
            "has_error_exit_codes",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **CLI Tool** (Python).\n"
            "Constraints: uses argparse or click, supports --help and --version, "
            "returns non-zero exit codes on error, writes to stdout/stderr appropriately.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **CLI Tool** (Python).\n"
            "Implementation plan:\n{context}\n\n"
            "Write cli.py with a main() function and if __name__ == '__main__' guard. "
            "Use argparse (stdlib) for argument parsing. Include --help (auto-generated) "
            "and --version flags. Return sys.exit(0) on success, sys.exit(1) on error. "
            "Use try/except for error handling. Print to stdout for normal output, "
            "stderr for errors."
        ),
        "acceptance_criteria": [
            "python cli.py --help prints usage",
            "python cli.py --version prints version",
            "Invalid args exit with non-zero code",
            "Valid args produce expected output",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
        },
        "token_budget_estimate": 2500,
    },
    "api_server": {
        "type_id": "api_server",
        "display_name": "FastAPI Microservice",
        "description": "A FastAPI REST microservice with CORS, health checks, and Pydantic validation.",
        "output_extension": ".py",
        "required_imports": ["fastapi", "uvicorn"],
        "default_entry_point": "main.py",
        "output_structure": {
            "main.py": "FastAPI application with routes and startup.",
            "requirements.txt": "Dependencies (fastapi, uvicorn).",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
            "has_fastapi_app",
            "has_at_least_one_route",
            "has_startup_event",
            "has_shutdown_event",
            "has_error_handlers",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **API Server** (FastAPI).\n"
            "Constraints: RESTful design, proper HTTP status codes, JSON responses, "
            "input validation with Pydantic models, async handlers where appropriate, "
            "CORS middleware, health check endpoint.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **API Server** (FastAPI).\n"
            "Implementation plan:\n{context}\n\n"
            "Write main.py with: from fastapi import FastAPI, HTTPException; "
            "from fastapi.middleware.cors import CORSMiddleware; "
            "from pydantic import BaseModel. Include at minimum a GET /health "
            "endpoint returning {'status':'ok'}. Add proper error handlers for "
            "404 and 500. Use lifespan context manager for startup/shutdown. "
            "All routes must have docstrings."
        ),
        "acceptance_criteria": [
            "uvicorn main:app starts without errors",
            "GET /health returns 200 with status ok",
            "POST endpoints validate input",
            "Invalid input returns 422",
            "404 returns proper error JSON",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
            "reviewer": "reasoning",
        },
        "token_budget_estimate": 3500,
    },
    "word_processor": {
        "type_id": "word_processor",
        "display_name": "Word Processor",
        "description": "A text processing utility for word count, frequency analysis, and text transformations.",
        "output_extension": ".py",
        "required_imports": [],
        "default_entry_point": "processor.py",
        "output_structure": {
            "processor.py": "Self-contained text processing utility.",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
            "has_main_function",
            "has_file_io",
            "has_text_transformation",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **Word Processor** (Python).\n"
            "Constraints: reads one or more input files, applies transformations, "
            "writes output. Operations may include: word count, frequency analysis, "
            "spell check, text normalization, markdown conversion, search/replace.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Word Processor** (Python).\n"
            "Implementation plan:\n{context}\n\n"
            "Write processor.py with argparse for CLI usage. Read input from files "
            "or stdin. Apply transformations using stdlib (re, collections.Counter). "
            "Handle encoding errors (UTF-8 with fallback). Write output to files "
            "or stdout. Include a main() function."
        ),
        "acceptance_criteria": [
            "python processor.py --help shows usage",
            "Can process a text file without errors",
            "Output is correct for the specified operation",
            "Handles missing files gracefully",
            "Handles non-UTF-8 input gracefully",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
        },
        "token_budget_estimate": 2500,
    },
    "kernel_module": {
        "type_id": "kernel_module",
        "display_name": "Linux Kernel Module",
        "description": "A Linux kernel module with proper init/exit functions and GPL licensing.",
        "output_extension": ".c",
        "required_imports": [],
        "default_entry_point": "module.c",
        "output_structure": {
            "module.c": "Kernel module C source file.",
            "Makefile": "Kbuild Makefile for building the module.",
        },
        "validation_rules": [
            "has_module_init",
            "has_module_exit",
            "has_license_gpl",
            "has_kernel_includes",
            "makefile_syntax_valid",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **Linux Kernel Module** (C).\n"
            "Constraints: targets Linux 5.x+ kernel API, uses proper locking "
            "(mutex/spinlock), handles errors in init with cleanup, registers "
            "a character device or procfs/sysfs entry, follows kernel coding "
            "style (8-char tabs, no typedefs for structs).\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Kernel Module** (C).\n"
            "Implementation plan:\n{context}\n\n"
            "Write module.c with: #include <linux/module.h>, <linux/kernel.h>, "
            '<linux/init.h>. Include MODULE_LICENSE("GPL"), MODULE_AUTHOR, '
            "MODULE_DESCRIPTION. Implement __init and __exit functions. "
            "Register a character device or /proc entry. Use mutex_lock/unlock "
            "for synchronization. On init failure, cleanup partial allocations. "
            "Also produce a Makefile with 'obj-m := module.o' and KDIR reference."
        ),
        "acceptance_criteria": [
            "module.c compiles against kernel headers",
            "Has module_init and module_exit functions",
            'Has MODULE_LICENSE("GPL")',
            "Makefile has obj-m target",
            "Init returns 0 on success, negative errno on failure",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
            "reviewer": "reasoning",
        },
        "token_budget_estimate": 3000,
    },
    "data_pipeline": {
        "type_id": "data_pipeline",
        "display_name": "ETL Data Pipeline",
        "description": "A modular ETL pipeline with extract, transform, and load stages using pandas.",
        "output_extension": ".py",
        "required_imports": ["pandas"],
        "default_entry_point": "pipeline.py",
        "output_structure": {
            "pipeline.py": "ETL pipeline entry point with extract, transform, load stages.",
            "requirements.txt": "Dependencies (pandas, etc.).",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
            "has_extract_function",
            "has_transform_function",
            "has_load_function",
            "has_main_orchestration",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **ETL Data Pipeline** (Python).\n"
            "Constraints: modular extract-transform-load stages, handles CSV/JSON "
            "input formats, applies data cleaning and validation, writes to a "
            "target format, logs progress, handles missing/invalid data gracefully.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Data Pipeline** (Python).\n"
            "Implementation plan:\n{context}\n\n"
            "Write pipeline.py with three distinct functions: extract(), "
            "transform(), load(). Use pandas for data manipulation. Add logging "
            "with the logging module. Handle missing files, malformed data, and "
            "empty datasets. Include a main() that chains the three stages. "
            "Support argparse for input/output file paths."
        ),
        "acceptance_criteria": [
            "python pipeline.py --help shows usage",
            "Extract reads source data without errors",
            "Transform applies specified operations",
            "Load writes to the target location",
            "Handles missing source file gracefully",
            "Handles empty input data gracefully",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
        },
        "token_budget_estimate": 3000,
    },
    "chatbot": {
        "type_id": "chatbot",
        "display_name": "Chat Interface",
        "description": "An interactive terminal-based chatbot with pattern-matching responses and session context.",
        "output_extension": ".py",
        "required_imports": [],
        "default_entry_point": "chatbot.py",
        "output_structure": {
            "chatbot.py": "Interactive chat application.",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
            "has_input_loop",
            "has_response_generation",
            "has_exit_command",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **Chatbot** (Python).\n"
            "Constraints: terminal-based interactive loop, pattern-matching "
            "or keyword-based responses, remembers conversation context within "
            "the session, supports /help and /exit commands, handles empty input.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Chatbot** (Python).\n"
            "Implementation plan:\n{context}\n\n"
            "Write chatbot.py with a while-True input loop. Store conversation "
            "history in a list of (role, text) tuples. Match against keyword "
            "patterns using regex or string methods. Support /help (lists commands) "
            "and /exit (clean shutdown). Include a greeting on startup. Handle "
            "EOFError and KeyboardInterrupt gracefully."
        ),
        "acceptance_criteria": [
            "python chatbot.py starts with greeting",
            "/help lists available commands",
            "/exit terminates cleanly",
            "Responds to recognized input patterns",
            "Handles empty input without crashing",
            "Handles Ctrl+C gracefully",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
        },
        "token_budget_estimate": 2500,
    },
    "desktop_app": {
        "type_id": "desktop_app",
        "display_name": "Desktop Application",
        "default_entry_point": "app.py",
        "output_structure": {
            "app.py": "Self-contained GUI application.",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
            "has_gui_import",
            "has_main_window",
            "has_event_loop",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **Desktop App** (Python tkinter).\n"
            "Constraints: uses tkinter (stdlib), windowed application with menu "
            "bar or toolbar, handles window close event, supports at minimum "
            "display and basic interaction. Runs on all platforms with Python.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Desktop App** (Python tkinter).\n"
            "Implementation plan:\n{context}\n\n"
            "Write app.py using tkinter. Create a Tk root window with a title. "
            "Add at minimum: a menu bar (File > Exit), a main frame with widgets, "
            "and proper geometry management (pack or grid). Run root.mainloop() "
            "at the end. Handle window close (WM_DELETE_WINDOW protocol). "
            "Use ttk themed widgets for better appearance."
        ),
        "acceptance_criteria": [
            "python app.py opens a window",
            "Window has a title",
            "Menu bar is present",
            "Window close exits cleanly",
            "No tkinter import errors",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
        },
        "token_budget_estimate": 3000,
    },
    "test_suite": {
        "type_id": "test_suite",
        "display_name": "Pytest Test Suite",
        "default_entry_point": "test_main.py",
        "output_structure": {
            "test_main.py": "pytest test file with test functions.",
            "conftest.py": "Shared pytest fixtures.",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
            "has_test_functions",
            "has_assert_statements",
            "has_pytest_import",
        ],
        "prompt_template_planner": _PLANNER_PREAMBLE
        + (
            "Project type: **Test Suite** (pytest).\n"
            "Constraints: follows AAA pattern (Arrange-Act-Assert), tests edge "
            "cases, uses fixtures for setup/teardown, isolates tests (no shared "
            "mutable state), covers happy path and error paths.\n"
            "User request: {context}"
        ),
        "prompt_template_coder": _CODER_PREAMBLE
        + (
            "Project type: **Test Suite** (pytest).\n"
            "Implementation plan:\n{context}\n\n"
            "Write test_main.py with test functions prefixed with test_. "
            "Each function must have at least one assert statement. "
            "Use pytest.raises() for error cases. Write a conftest.py with "
            "shared fixtures using @pytest.fixture. Tests must be isolated "
            "(no shared mutable state between tests). Follow AAA pattern: "
            "Arrange (setup), Act (execute), Assert (verify)."
        ),
        "acceptance_criteria": [
            "pytest test_main.py collects tests",
            "All tests have assert statements",
            "Fixtures are used for setup",
            "Error cases are tested with pytest.raises",
            "Each test function follows AAA pattern",
        ],
        "suggested_model_roles": {
            "planner": "reasoning",
            "coder": "coding",
        },
        "token_budget_estimate": 3000,
    },
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

PROJECT_TYPE_REGISTRY: dict[str, ProjectType] = {
    type_id: ProjectType(**{k: v for k, v in defn.items() if k in ProjectType.__dataclass_fields__})
    for type_id, defn in _BASE_DEFINITIONS.items()
}


def _legacy_definition(project_type: ProjectType) -> dict[str, object]:
    """Serialize a typed definition through the former dictionary contract."""
    return {
        "type_id": project_type.type_id,
        "display_name": project_type.display_name,
        "prompt_templates": {
            "system": project_type.prompt_template_planner.replace("{context}", "{description}"),
            "user": project_type.prompt_template_coder.replace("{context}", "{description}"),
        },
        "validation_rules": list(project_type.validation_rules),
        "acceptance_criteria": list(project_type.acceptance_criteria),
        "suggested_model_roles": list(project_type.suggested_model_roles),
    }


class _LegacyProjectTypesView(Mapping[str, dict[str, object]]):
    """Read-only live view over the canonical typed registry."""

    def __getitem__(self, type_id: str) -> dict[str, object]:
        return _legacy_definition(PROJECT_TYPE_REGISTRY[type_id])

    def __iter__(self) -> Iterator[str]:
        return iter(PROJECT_TYPE_REGISTRY)

    def __len__(self) -> int:
        return len(PROJECT_TYPE_REGISTRY)


PROJECT_TYPES: Mapping[str, dict[str, object]] = _LegacyProjectTypesView()


def available_type_ids() -> list[str]:
    """Return all registered project type IDs in sorted order."""
    return sorted(PROJECT_TYPE_REGISTRY)


def list_project_types() -> list[str]:
    """Compatibility alias returning registered project IDs in sorted order."""
    return available_type_ids()


def get_project_type(type_id: str) -> ProjectType:
    """Look up a project type by its slug.

    Args:
        type_id: The type identifier (e.g. ``"game"``).

    Returns:
        The matching :class:`ProjectType`.

    Raises:
        KeyError: If ``type_id`` is not registered.
    """
    if type_id not in PROJECT_TYPE_REGISTRY:
        raise KeyError(f"Unknown project type: {type_id!r}. Available: {available_type_ids()}")
    return PROJECT_TYPE_REGISTRY[type_id]


def validate_project_against_rules(code: str, type_def: ProjectType) -> bool:
    """Validate generated code by dispatching each rule in ``type_def.validation_rules``.

    Known rule names:
        ``"ast_valid"`` — ``compile()`` check with ``exec`` mode.
        ``"importable"`` — dynamic import via ``importlib`` in a temp file.
        ``"has_entry_point"`` — verify the code is non-empty (placeholder).
        ``"no_syntax_errors"`` — ``compile()`` check with ``exec`` mode.
        Any unrecognized rule passes silently (backward compat).
    """
    return all(_validate_rule(code, rule, type_def) for rule in type_def.validation_rules)


def _validate_rule(code: str, rule: str, type_def: ProjectType) -> bool:
    if rule == "ast_valid":
        try:
            compile(code, "<generated>", "exec")
        except SyntaxError:
            return False
        return True

    if rule == "importable":
        return _check_importable(code)

    if rule == "has_entry_point":
        return bool(code.strip())

    if rule == "no_syntax_errors":
        try:
            compile(code, "<generated>", "exec")
        except SyntaxError:
            return False
        return True

    return True


def _check_importable(code: str) -> bool:
    """Try to dynamically import *code* from a temp file."""
    tmp_path: str | None = None
    mod_name = f"_gen_val_{os.getpid()}_{id(code)}"
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as fh:
            fh.write(code)
            tmp_path = fh.name
        spec = importlib.util.spec_from_file_location(mod_name, tmp_path)
        if spec is None or spec.loader is None:
            return False
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(mod_name, None)
        return True
    except Exception:
        return False
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def _legacy_project_type(type_id: str, definition: Mapping[str, Any]) -> ProjectType:
    """Validate and convert the former dictionary registration form."""
    declared_type_id = definition.get("type_id")
    if declared_type_id != type_id:
        raise ValueError("legacy project type id must match its registry key")

    display_name = definition.get("display_name")
    templates = definition.get("prompt_templates")
    validation_rules = definition.get("validation_rules")
    acceptance_criteria = definition.get("acceptance_criteria")
    roles = definition.get("suggested_model_roles")
    if not isinstance(display_name, str) or not display_name:
        raise TypeError("display_name must be a non-empty string")
    if not isinstance(templates, Mapping) or not templates:
        raise TypeError("prompt_templates must be a non-empty mapping")
    if not isinstance(validation_rules, list) or not all(isinstance(rule, str) for rule in validation_rules):
        raise TypeError("validation_rules must be a list of strings")
    if not isinstance(acceptance_criteria, list) or not all(
        isinstance(criterion, str) for criterion in acceptance_criteria
    ):
        raise TypeError("acceptance_criteria must be a list of strings")
    if not isinstance(roles, list) or not roles or not all(isinstance(role, str) for role in roles):
        raise TypeError("suggested_model_roles must be a non-empty list of strings")

    planner = templates.get("planner", templates.get("system", templates.get("user", templates.get("coder"))))
    coder = templates.get("coder", templates.get("user", templates.get("system", templates.get("planner"))))
    if not isinstance(planner, str) or not isinstance(coder, str):
        raise TypeError("prompt_templates must define planner/coder or system/user strings")

    default_entry_point = definition.get("default_entry_point", f"{type_id}.py")
    if not isinstance(default_entry_point, str) or not default_entry_point:
        raise TypeError("default_entry_point must be a non-empty string")

    return ProjectType(
        type_id=type_id,
        display_name=display_name,
        default_entry_point=default_entry_point,
        prompt_template_planner=planner.replace("{description}", "{context}"),
        prompt_template_coder=coder.replace("{description}", "{context}"),
        validation_rules=list(validation_rules),
        acceptance_criteria=list(acceptance_criteria),
        suggested_model_roles={
            role: "coding" if role in {"coder", "editor"} else "reasoning"
            for role in roles
        },
    )


def register_project_type(
    project_type: ProjectType | str,
    definition: Mapping[str, Any] | None = None,
) -> None:
    """Register typed definitions or the validated legacy dictionary form."""
    if isinstance(project_type, ProjectType):
        if definition is not None:
            raise TypeError("definition is only valid with a string type id")
        resolved = project_type
    else:
        if definition is None:
            raise TypeError("legacy registration requires a definition mapping")
        resolved = _legacy_project_type(project_type, definition)
    PROJECT_TYPE_REGISTRY[resolved.type_id] = resolved


# ---------------------------------------------------------------------------
# High-level helpers for the SoftwareGenerator
# ---------------------------------------------------------------------------

VALID_PROJECT_TYPES: tuple[str, ...] = (
    "game",
    "web",
    "cli",
    "library",
)

VALIDATION_RULES: dict[str, dict[str, object]] = {
    "game": {
        "required_imports": ["pygame"],
        "required_methods": ["init", "set_mode", "get", "flip"],
        "required_patterns": ["game loop", "event handling"],
    },
    "web": {
        "required_imports": [],
        "required_elements": ["html", "head", "body"],
        "required_patterns": ["responsive", "semantic"],
    },
    "cli": {
        "required_imports": ["argparse", "click"],
        "required_patterns": ["--help", "main()"],
        "exit_codes": [0, 1, 2],
    },
    "library": {
        "required_imports": [],
        "require_public_api": True,
        "required_patterns": ["__all__", "docstring"],
    },
}


_MODEL_PROFILES: dict[str, dict[str, str]] = {
    "game": {"planner": "reasoning", "coder": "coding", "reviewer": "reasoning"},
    "web": {"planner": "reasoning", "coder": "coding"},
    "cli": {"planner": "reasoning", "coder": "coding", "reviewer": "reasoning"},
    "library": {"coder": "coding"},
}


def resolve_model_profile(project_type: str) -> dict[str, str]:
    """Return the recommended model profile dict for *project_type*.

    Returns an empty dict for unknown types.
    """
    return _MODEL_PROFILES.get(project_type, {})


def validate_project_type(project_type: str | None) -> None:
    """Raise :exc:`ValueError` if *project_type* is not a valid type slug."""
    if not project_type:
        raise ValueError(f"Unknown project type: {project_type!r}")
    if project_type not in VALID_PROJECT_TYPES:
        raise ValueError(f"Unknown project type: {project_type!r}. Known: {list(VALID_PROJECT_TYPES)}")


__all__ = [
    "PROJECT_TYPES",
    "PROJECT_TYPE_REGISTRY",
    "VALIDATION_RULES",
    "VALID_PROJECT_TYPES",
    "ProjectType",
    "available_type_ids",
    "get_project_type",
    "list_project_types",
    "register_project_type",
    "resolve_model_profile",
    "validate_project_against_rules",
    "validate_project_type",
]
