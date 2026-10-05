from __future__ import annotations

from pathlib import Path


def _recipe(target: str) -> str:
    lines = Path("Makefile").read_text().splitlines()
    start = lines.index(f"{target}:") + 1
    body: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith(("\t", " ", "#")):
            break
        body.append(line)
    return "\n".join(body)


def test_dist_clean_removes_linux_build_namespace() -> None:
    recipe = _recipe("dist-clean")
    assert "dist/linux" in recipe
