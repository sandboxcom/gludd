#!/usr/bin/env python3
"""check_changelog_accuracy.py — AC015: changelog-accuracy.

Verifies CHANGELOG.md accurately reflects commits between prior and current tag.
"""

import os
import re
import subprocess
import sys
from pathlib import Path


def run_git(args: list[str]) -> tuple[str, str, int]:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
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


def crossref_changelog_against_commits(
    section_text: str,
    commits: list[str],
) -> dict[str, list[str]]:
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


def get_tags() -> list[str]:
    out, _, rc = run_git(["tag", "--sort=-creatordate", "-l", "v*"])
    if rc != 0:
        return []
    return [tag for tag in out.split("\n") if tag]


def comparison_refs(tags: list[str], tag: str) -> tuple[str | None, str]:
    """Return the prior release and candidate ref for changelog validation."""
    if tag in tags:
        index = tags.index(tag)
        prior = tags[index + 1] if index + 1 < len(tags) else None
        return prior, tag
    return (tags[0] if tags else None), "HEAD"


def candidate_documents_complete(
    changelog_content: str,
    release_notes_content: str,
    version: str,
) -> bool:
    """Require substantive, version-matched documents before a tag is cut."""
    return bool(
        parse_changelog_entries(changelog_content, version)
        and version in release_notes_content
    )


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    if not tag:
        print("AC015: TAG required")
        sys.exit(2)

    tags = get_tags()
    prev_tag, candidate_ref = comparison_refs(tags, tag)
    if not prev_tag:
        print("AC015: WARN — no prior tag found, skipping commit range check")
        sys.exit(0)

    range_spec = f"{prev_tag}..{candidate_ref}"
    out, error, returncode = run_git(["log", "--oneline", range_spec])
    if returncode != 0:
        print(f"AC015: FAIL — cannot inspect commit range {range_spec}: {error}")
        sys.exit(1)
    commits = [line for line in out.split("\n") if line]

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

    if candidate_ref == "HEAD":
        release_notes = root / "docs" / "releases" / f"{tag}.md"
        if not release_notes.exists():
            print(f"AC015: FAIL — release notes not found: {release_notes}")
            sys.exit(1)
        notes_text = release_notes.read_text()
        if not candidate_documents_complete(changelog_text, notes_text, version):
            print(
                "AC015: FAIL — candidate changelog or release notes are empty "
                f"or do not identify {version}"
            )
            sys.exit(1)
        print(
            f"AC015: PASS — candidate documents cover {version}; "
            f"inspected {len(commits)} commits in {range_spec}"
        )
        sys.exit(0)

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
