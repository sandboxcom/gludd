#!/usr/bin/env python3
"""generate_release_notes.py — AC018: release-notes-automation.

Auto-generates release notes from conventional commits between two tags.
Categorizes by type (feat, fix, docs, refactor, test, chore).
"""

import os
import re
import subprocess
import sys


LABEL_MAP = {
    "feat": "Features",
    "fix": "Bug Fixes",
    "docs": "Documentation",
    "refactor": "Refactoring",
    "test": "Tests",
    "chore": "Chores",
    "other": "Other Changes",
}

COMMIT_CATEGORIES = list(LABEL_MAP.keys())


def run_git(args):
    result = subprocess.run(["git"] + args, capture_output=True, text=True)
    return result.stdout.strip()


def find_prev_tag(tags, current_tag):
    if current_tag not in tags:
        raise ValueError(f"Tag '{current_tag}' not found")
    idx = tags.index(current_tag)
    return tags[idx + 1] if idx + 1 < len(tags) else None


def categorize_commits(commit_lines):
    categories = {k: [] for k in COMMIT_CATEGORIES}
    for commit in commit_lines:
        match = re.match(r"^[a-f0-9]+\s+(\w+)(?:\([^)]*\))?!?:?\s*(.*)", commit)
        if match:
            ctype, desc = match.group(1), match.group(2)
            if ctype in categories:
                categories[ctype].append(desc)
            else:
                categories["other"].append(commit)
        else:
            categories["other"].append(commit)
    return categories


def format_notes(categories, version, prev_tag, contributors=""):
    lines = []
    lines.append(f"## What's Changed in {version}")
    lines.append("")

    for key, label in LABEL_MAP.items():
        if categories.get(key):
            lines.append(f"### {label}")
            for item in categories[key]:
                lines.append(f"- {item}")
            lines.append("")

    if contributors:
        lines.append("### Contributors")
        for line in contributors.split("\n"):
            lines.append(f"- {line.strip()}")
        lines.append("")

    if prev_tag:
        lines.append(f"**Full Changelog**: {prev_tag}...{version}")
    else:
        lines.append(f"**Initial Release**: {version}")
    return "\n".join(lines)


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC018: TAG required")
        sys.exit(2)

    tags = run_git(["tag", "--sort=-creatordate", "-l", "v*"]).split("\n")
    tags = [t for t in tags if t]

    if tag not in tags:
        print(f"AC018: TAG '{tag}' not found")
        sys.exit(2)

    try:
        prev_tag = find_prev_tag(tags, tag)
    except ValueError:
        prev_tag = None

    if not prev_tag:
        print("AC018: WARN — no prior tag, generating notes from first commit")
        range_spec = tag
    else:
        range_spec = f"{prev_tag}..{tag}"

    commits = run_git(["log", "--oneline", range_spec]).split("\n")
    commits = [c for c in commits if c]

    categories = categorize_commits(commits)
    contributors = run_git(["shortlog", "-sn", range_spec])

    version = tag.lstrip("v")
    print(format_notes(categories, version, prev_tag, contributors))
    sys.exit(0)


if __name__ == "__main__":
    main()
