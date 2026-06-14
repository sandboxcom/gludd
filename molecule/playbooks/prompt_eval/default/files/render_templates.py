#!/usr/bin/env python3
"""Render every Jinja2 template with a test context (templates dir = argv[1])."""
import os
import sys

from jinja2 import Environment, FileSystemLoader

templates_dir = sys.argv[1] if len(sys.argv) > 1 else "templates/prompts"
env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
context = {
    "todo_title": "Test compilation task",
    "todo_description": "Verify templates compile correctly",
    "work_type": "code",
    "queue": "core",
    "priority": "medium",
    "return_id": "RET-TEST",
    "task_return_json": '{"status": "ok"}',
    "candidate_todos_json": '["TODO-001"]',
    # return_review.md.j2 renders these via | tojson / direct interpolation
    "task_return": {"status": "ok"},
    "candidate_todos": ["TODO-001"],
    "artifacts": [{"path": "result.json"}],
    "conversation_context": "test conversation context",
}
errors = []
for root, _dirs, files in os.walk(templates_dir):
    for f in files:
        if f.endswith(".j2"):
            rel = os.path.relpath(os.path.join(root, f), templates_dir)
            try:
                result = env.get_template(rel).render(**context)
                if result:
                    print(f"RENDERED: {rel} ({len(result)} chars)")
                else:
                    errors.append(rel)
                    print(f"EMPTY: {rel}")
            except Exception as e:  # noqa: BLE001
                errors.append(rel)
                print(f"RENDER_FAIL: {rel} — {e}")
sys.exit(1 if errors else 0)
