#!/usr/bin/env python3
"""Compile every Jinja2 template under the templates dir (passed as argv[1])."""
import os
import sys

from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

templates_dir = sys.argv[1] if len(sys.argv) > 1 else "templates/prompts"
env = Environment(loader=FileSystemLoader(templates_dir), autoescape=True)
errors = []
for root, _dirs, files in os.walk(templates_dir):
    for f in files:
        if f.endswith(".j2"):
            rel = os.path.relpath(os.path.join(root, f), templates_dir)
            try:
                env.get_template(rel)
                print(f"OK: {rel}")
            except TemplateSyntaxError as e:
                errors.append(rel)
                print(f"FAIL: {rel} — {e}")
            except Exception as e:  # noqa: BLE001
                errors.append(rel)
                print(f"ERROR: {rel} — {e}")
sys.exit(1 if errors else 0)
