"""Bootstrap script: install default mattpocock skills into .opencode/skills/."""
from __future__ import annotations

from general_ludd.skills.catalog import SkillCatalog

DEFAULT_SKILLS = [
    "mp-diagnose",
    "mp-tdd",
    "mp-grill-me",
    "mp-grill-with-docs",
    "mp-caveman",
    "mp-handoff",
    "mp-to-prd",
    "mp-to-issues",
    "mp-zoom-out",
    "mp-improve-architecture",
    "mp-teach",
    "mp-write-a-skill",
]


def main() -> None:
    catalog = SkillCatalog()
    installed = 0
    for name in DEFAULT_SKILLS:
        path = catalog.install_skill(name, ".opencode/skills")
        if path:
            installed += 1
            print(f"  Installed {name}")
    print(f"Installed {installed}/{len(DEFAULT_SKILLS)} mattpocock skills")


if __name__ == "__main__":
    main()
