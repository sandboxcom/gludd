#!/usr/bin/env python3
"""check_changelog_accuracy.py — AC015: changelog-accuracy.

Verifies CHANGELOG.md accurately reflects commits between prior and current tag.
"""

import os
import re
import subprocess
import sys
from pathlib import Path


def run_git(args):
    result = subprocess.run(["git"] + args, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def find_version_section(changelog_text: str, version: str) -> str | None:
    match = re.search(
        rf"(##\s+\[?{re.escape(version)}[\]\s].*?)(?=##\s+\[|\Z)",
        changelog_text,
        re.DOTALL,
    )
    return match.group(0) if match else None


def find_missing_commits(commits: list[str], section: str) -> list[str]:
    missing = []
    for commit in commits:
        parts = commit.split()
        sha = parts[0]
        desc = " ".join(parts[1:])
        if sha not in section and desc[:20] not in section:
            missing.append(commit)
    return missing


def parse_changelog_entries(changelog_content: str, version: str) -> list[str]:
    section = find_version_section(changelog_content, version)
    if not section:
        return []
    lines = section.split("\n")
    entries = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            entries.append(stripped[2:])
    return entries


def crossref_changelog_against_commits(section_text: str, commits: list[str]) -> dict[str, list]:
    missing_in_changelog = []
    for commit in commits:
        parts = commit.split()
        sha = parts[0]
        desc = " ".join(parts[1:])
        if sha not in section_text and desc[:20] not in section_text:
            missing_in_changelog.append(commit)
    phantom = find_phantom_entries(section_text, commits)
    return {"missing_in_changelog": missing_in_changelog, "phantom_entries": phantom}


def find_phantom_entries(section_text: str, commits: list[str]) -> list[str]:
    commit_shas = {c.split()[0] for c in commits}
    sha_pattern = re.compile(r"\b([0-9a-f]{7,40})\b")
    referenced_shas = set(sha_pattern.findall(section_text))
    phantom_shas = referenced_shas - commit_shas
    return sorted(phantom_shas)


def get_tags():
    out, _, rc = run_git(["tag", "--sort=-creatordate", "-l", "v*"])
    if rc != 0:
        return []
    return [t for t in out.split("\n") if t]


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC015: TAG required")
        sys.exit(2)

    tags = get_tags()
    if tag not in tags:
        print(f"AC015: INCONCLUSIVE — tag '{tag}' not found in git tags")
        sys.exit(2)

    try:
        idx = tags.index(tag)
        prev_tag = tags[idx + 1] if idx + 1 < len(tags) else None
    except ValueError:
        prev_tag = None

    if not prev_tag:
        print("AC015: WARN — no prior tag found, skipping commit range check")
        sys.exit(0)

    range_spec = f"{prev_tag}..{tag}"
    out, _, _ = run_git(["log", "--oneline", range_spec])
    commits = [l for l in out.split("\n") if l]

    root = Path(__file__).resolve().parent.parent
    changelog = root / "CHANGELOG.md"
    if not changelog.exists():
        print("AC015: FAIL — CHANGELOG.md not found")
        sys.exit(1)

    changelog_text = changelog.read_text()
    version = tag.lstrip("v")

    version_section = re.search(rf"(##\s+\[?{re.escape(version)}[\]\s].*?)(?=##\s+\[|\Z)", changelog_text, re.DOTALL)

    if not version_section:
        print(f"AC015: FAIL — no {version} section in CHANGELOG.md")
        sys.exit(1)

    section = version_section.group(0)

    missing = []
    for commit in commits:
        sha = commit.split()[0]
        desc = " ".join(commit.split()[1:])
        if sha not in section and desc[:20] not in section:
            missing.append(commit)

    if missing:
        print(f"AC015: FAIL — {len(missing)} commit(s) not in changelog for {tag}")
        for m in missing[:5]:
            print(f"  {m}")
        sys.exit(1)

    print(f"AC015: PASS — changelog covers {len(commits)} commits in {range_spec}")
    sys.exit(0)


if __name__ == "__main__":
    main()
