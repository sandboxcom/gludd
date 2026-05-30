#!/usr/bin/env python3
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    dirs = [
        "src/agentic_harness/worker",
        "src/agentic_harness/event_loop",
        "src/agentic_harness/models",
        "src/agentic_harness/db",
        "src/agentic_harness/rules",
        "src/agentic_harness/schemas",
        "src/agentic_harness/secrets",
        "src/agentic_harness/git_automation",
        "src/agentic_harness/controllers",
        "src/agentic_harness/ansible",
        "src/agentic_harness/prompts",
        "src/agentic_harness/quality",
        "src/agentic_harness/runtime",
        "tests/unit",
        "tests/integration",
        "tests/e2e",
        "playbooks",
        "roles",
        "molecule/playbooks",
        "molecule/roles",
        "molecule/internal_tools",
        "templates/prompts/partials",
        "tools/ansible_lint_rules",
        "scripts",
        "docs",
        "config",
        "alembic/versions",
        "collections",
    ]
    for d in dirs:
        path = os.path.join(ROOT, d)
        os.makedirs(path, exist_ok=True)
        init_path = os.path.join(path, "__init__.py")
        if "src/" in d and not os.path.exists(init_path):
            open(init_path, "w").close()

    inits = [
        "tests/__init__.py",
        "tests/unit/__init__.py",
        "tests/integration/__init__.py",
        "tests/e2e/__init__.py",
    ]
    for rel in inits:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            open(path, "w").close()

    print("Skeleton directories and __init__.py files created.")


if __name__ == "__main__":
    main()
