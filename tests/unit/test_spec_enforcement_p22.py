"""P22: Push guard is not circumventable.

No make target or agent behaviour MUST provide a backdoor around the
push guard. Every push target (including `-nv` and `force-push`) must
include `_push-rate-guard` in its prerequisite chain.
"""

import re
from pathlib import Path
from typing import ClassVar

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _build_dependency_graph(content: str) -> dict[str, set[str]]:
    """Parse prerequisite chains from Makefile into {target: set-of-prereqs}."""
    graph: dict[str, set[str]] = {}
    lines = content.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:\s*(.*)", line.strip())
        if not m:
            continue
        target = m.group(1)
        prereqs_raw = m.group(2).strip()
        prereqs = set(prereqs_raw.split()) if prereqs_raw else set()
        graph[target] = prereqs

        # Handle continuation lines (ending with \)
        j = i + 1
        while j < len(lines) and lines[j].startswith(("\t", " ")):
            stripped = lines[j].strip().rstrip("\\")
            if stripped and not stripped.startswith(("@", "#")):
                graph[target].update(stripped.split())
            j += 1
    return graph


def _transitive_prereqs(graph: dict[str, set[str]], target: str) -> set[str]:
    """Return all transitive prerequisites of a target."""
    result: set[str] = set()
    visited: set[str] = set()
    stack = [target]
    while stack:
        t = stack.pop()
        if t in visited:
            continue
        visited.add(t)
        for p in graph.get(t, set()):
            if p not in visited:
                result.add(p)
                stack.append(p)
    return result


class TestP22PushGuardNotCircumventable:
    """P22 — all push paths go through _push-rate-guard."""

    _PUSH_TARGETS: ClassVar[list[str]] = [
        "git-push-sandboxcom",
        "git-push-sandboxcom-nv",
        "push-dev-nv",
        "git-push-current-head-nv",
        "git-push-current-head-to-master-nv",
        "ci-push",
    ]

    def test_every_push_target_has_push_rate_guard(self) -> None:
        content = MAKEFILE.read_text()
        graph = _build_dependency_graph(content)
        missing = []
        for target in self._PUSH_TARGETS:
            if target not in graph:
                missing.append(f"'{target}' missing from Makefile")
                continue
            deps = _transitive_prereqs(graph, target)
            if "_push-rate-guard" not in deps and "_push-rate-guard" not in graph.get(target, set()):
                direct = graph.get(target, set())
                missing.append(f"'{target}' missing _push-rate-guard (direct prereqs: {sorted(direct)})")
        assert not missing, "P22 VIOLATION — push targets without _push-rate-guard:\n" + "\n".join(missing)

    def test_force_push_still_goes_through_guard(self) -> None:
        content = MAKEFILE.read_text()
        graph = _build_dependency_graph(content)
        if "force-push" in graph:
            deps = _transitive_prereqs(graph, "force-push")
            target_start = content.find("\nforce-push:")
            target_end = content.find("\n\n", target_start)
            recipe = content[target_start : target_end if target_end != -1 else len(content)]
            assert (
                "_push-rate-guard" in deps
                or "git-push-sandboxcom" in deps
                or ("_push-rate-guard" in recipe and "git-push-sandboxcom" in recipe)
            ), (
                "P22: force-push must delegate to a guarded push target"
            )

    def test_master_force_push_uses_push_rate_guard(self) -> None:
        content = MAKEFILE.read_text()
        graph = _build_dependency_graph(content)
        if "master-force-push" in graph:
            recipe_str = content
            target_start = recipe_str.find("\nmaster-force-push:")
            if target_start != -1:
                end = recipe_str.find("\n\n", target_start)
                if end == -1:
                    end = len(recipe_str)
                recipe = recipe_str[target_start:end]
                assert "_push-rate-guard" in recipe or "GLUDD_FORCE_PUSH" in recipe, (
                    "P22: master-force-push must reference _push-rate-guard or GLUDD_FORCE_PUSH"
                )
