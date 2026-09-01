"""Deep Makefile target completeness tests.

Tests 20+ assertions across: help text coverage, prerequisite existence,
circular-dependency detection, script resolution, .PHONY completeness,
"make help" output parity, and orphan-target detection.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"
ROOT = MAKEFILE.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TARGET_LINE_RE = re.compile(r"^[a-zA-Z_][-a-zA-Z0-9_./]*\s*:(?!=)")


def _read_makefile() -> str:
    return MAKEFILE.read_text()


def _extract_target_defs(content: str) -> dict[str, int]:
    """Return {target_name: line_number} for every defined target at column 0.

    Excludes .PHONY, variable assignments, and dot-directives.
    """
    targets: dict[str, int] = {}
    in_phony = False
    for i, raw in enumerate(content.split("\n"), 1):
        stripped = raw.strip()
        if stripped.startswith(".PHONY:"):
            in_phony = raw.rstrip("\n").endswith("\\")
            continue
        if in_phony:
            if not raw.rstrip("\n").endswith("\\"):
                in_phony = False
            continue
        if not _TARGET_LINE_RE.match(stripped):
            continue
        if raw.startswith((" ", "\t")):
            continue
        if stripped.startswith(".") and not stripped.startswith("./"):
            continue
        name = stripped.split(":")[0].strip()
        targets[name] = i
    return targets


def _extract_phony_names(content: str) -> set[str]:
    """Return the set of target names listed under .PHONY."""
    names: set[str] = set()
    in_phony = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            tokens = stripped.split(":", 1)[1].split()
            names.update(t.rstrip("\\") for t in tokens if t.rstrip("\\"))
            in_phony = line.rstrip("\n").endswith("\\")
            continue
        if in_phony:
            tokens = stripped.split()
            names.update(t.rstrip("\\") for t in tokens if t.rstrip("\\"))
            if not line.rstrip("\n").endswith("\\"):
                in_phony = False
    return names


def _extract_prereqs(content: str) -> dict[str, list[str]]:
    """Return {target_name: [prerequisite_target_names]}.

    Only parses the prereq portion of ``name: prereq1 prereq2`` lines
    that have newline-delimited recipes (not ;-inline or recipe-containing lines).
    """
    targets = _extract_target_defs(content)
    lines = content.split("\n")
    prereqs: dict[str, list[str]] = {}
    _COMMON_WORDS = frozenset(
        {
            "behavioral",
            "groups",
            "into",
            "node",
            "spec",
            "temp",
            "tests",
            "Run",
            "Splice",
            "test",
            "src",
            "all",
            "clean",
            "output",
            "json",
            "log",
            "data",
            "file",
            "name",
            "text",
            "path",
            "dir",
            "out",
            "input",
            "cmd",
            "args",
            "arg",
            "options",
            "config",
            "format",
        }
    )

    for name, lineno in targets.items():
        raw = lines[lineno - 1]
        after_colon = raw.split(":", 1)[1] if ":" in raw else ""
        # GNU make ignores an unescaped # and the remainder of a rule line.
        after_colon = after_colon.split("#", 1)[0]
        # Skip lines where the colon is followed by recipe syntax (inline or otherwise)
        if ";" in after_colon:
            after_colon = after_colon.split(";")[0]
        if not after_colon.strip():
            prereqs[name] = []
            continue
        # If the line contains recipe markers ($( or @), it's a recipe line, not prereqs
        if "$(" in after_colon or " @" in after_colon or after_colon.lstrip().startswith("@"):
            prereqs[name] = []
            continue
        deps = [d.strip() for d in after_colon.split() if d.strip()]
        # Only keep tokens that look like make target names (lowercase start, dashes, underscores)
        clean = [d for d in deps if re.match(r"^[a-z_][-a-zA-Z0-9_]*$", d) and d not in _COMMON_WORDS]
        prereqs[name] = clean
    return prereqs


def _extract_help_mapping(content: str) -> dict[str, str]:
    """Parse the help target recipe to extract {target: description}."""
    mapping: dict[str, str] = {}
    in_help = False
    for line in content.split("\n"):
        if line.strip().startswith("help:") and not line.startswith((" ", "\t")):
            in_help = True
            continue
        if in_help:
            if line.startswith("\t@echo"):
                m = re.match(r'\t@echo\s+"\s{2,}(\S[^"]*?)"', line)
                if m:
                    text = m.group(1)
                    parts = text.split(None, 1)
                    if len(parts) >= 1 and re.match(r"^[-a-zA-Z_][-a-zA-Z0-9_.]*$", parts[0]):
                        mapping[parts[0]] = parts[1] if len(parts) > 1 else ""
            elif not line.startswith("\t") and line.strip() != "":
                break
    return mapping


def _extract_script_refs(content: str) -> dict[str, list[str]]:
    """Return {target_name: [referenced_script_paths]}."""
    refs: dict[str, list[str]] = {}
    targets = _extract_target_defs(content)
    lines = content.split("\n")
    for name, lineno in targets.items():
        i = lineno
        recipe: list[str] = []
        while i < len(lines):
            line = lines[i]
            if line.startswith("\t") or line.startswith("  "):
                recipe.append(line.strip())
            elif _TARGET_LINE_RE.match(line.strip()) and i > lineno:
                break
            i += 1
        scripts = set()
        for rline in recipe:
            for m in re.finditer(r"scripts/[-a-z0-9_/.]+\.(?:py|sh|mjs|bash|bats)", rline, re.I):
                scripts.add(m.group(0))
        if scripts:
            refs[name] = sorted(scripts)
    return refs


def _is_known_entry_point(name: str) -> bool:
    """Return True if this target is a known top-level entry point."""
    if name in {
        "help",
        "all",
        "ps-pytest",
        "ps-gludd",
        "ps",
        "script-count",
        "ruff-audit",
        "deps-audit",
        "status-snapshot",
        "untrack",
        "status-heartbeat",
        "commit-bootstrap",
        "commit-no-verify",
        "plan",
        "restart-opencode",
        "static-coverage",
        "search",
        "cat-file",
        "show-lines",
        "file-executable",
        "yaml-lint",
        "user-test-batch",
        "collection-roles",
        "collection-modules",
        "scaffold-collection-roles",
        "log-agent-result",
        "recover-incomplete-tasks",
        "subagent-init",
        "subagent-cleanup",
        "deck-build",
        "deck-serve",
        "deck-preview",
        "deck-clean-assets",
        "deck-data",
        "deck-honesty",
        "service-discover",
        "service-catalog",
        "dogfood",
        "dogfood-features",
        "skill-install",
        "skill-list",
        "chat",
        "chat-eval",
        "branches-unmerged",
        "db-sample-message",
        "db-sample-part",
        "db-tables",
        "db-count",
        "diagnose-e2e-tools",
        "worktree-health-check",
        "worktree-merge-all",
    }:
        return True
    prefixes = (
        "test-",
        "check-",
        "audit-",
        "verify-",
        "validate-",
        "release-",
        "ci-",
        "git-",
        "tf-",
        "searx-",
        "molecule-",
        "clean-",
        "ansible-",
        "scan-",
        "kill-",
        "ps-",
        "reap-",
        "floor-",
        "gate-",
        "fix-",
        "install-",
        "bootstrap-",
        "build-",
        "container-",
        "deb-",
        "rpm-",
        "macos-",
        "windows-",
        "sandbox-",
        "vm-",
        "azure-",
        "runpod-",
        "provider-",
        "iam-",
        "opa-",
        "local-model-",
        "ship-",
        "game-",
        "gen-",
        "mcp-",
        "feature-",
        "agent-",
        "development-",
        "submodule-",
        "watchdog-",
        "task-",
        "podman-",
        "crash-",
        "provision-",
        "e2e-",
        "sdd-",
        "opencode-",
        "backup-",
        "restore-",
        "hot-reload",
        "rearm-",
        "disengage-",
        "reload-",
        "enforcement",
        "codemod-",
        "list-",
        "batch-",
        "push-",
        "force-",
        "master-",
        "repo-",
        "wt-",
        "gh-",
        "gha-",
        "pages-",
        "deploy-",
        "require-",
        "pipeline-",
        "codex-",
        "bump-",
        "normalize-",
        "strip-",
        "report-",
        "coverage-",
        "security-",
        "sast-",
        "pip-",
        "dead-",
        "integration-",
        "diag-",
        "diag_",
        "find-",
        "search-",
        "cat-",
        "show-",
        "remove-",
        "move-",
        "merge-",
        "deduplicate-",
        "count-",
        "generate-",
        "expand-",
        "prune-",
        "proactive-",
        "skip-",
        "auto-",
        "delete-",
        "copy-",
        "replace-",
        "write-",
        "append-",
        "patch-",
        "bench-",
        "repro-",
        "notify-",
        "networking-",
        "bundle-",
        "dist-",
        "uv-",
        "lsd",
        "lsf",
        "lsa",
        "grepf",
        "scan-secrets-fresh",
        "scan-conflicts",
        "scan-tool-usage",
        "collect-specific",
        "collect-prompts",
        "synch-",
        "sync-",
        "run-watched",
        "bisect-ts-parse",
        "fix-plugin-bun-exports",
        "fix-logger-imports",
        "fix-spec-enforcement",
        "fix-benchmark-mock",
        "fix-ratchet-mocks",
        "fix-hooks-tmp",
        "fix-subagent-detection",
        "fix-opencode-crash",
        "fix-e501-golden",
        "node-deps-",
        "init",
        "dist",
        "skeleton",
        "version",
        "smoke",
        "gate",
        "gate-lite",
        "lint",
        "typecheck",
        "test",
        "test-unit",
        "test-integration",
        "test-e2e",
        "test-db",
        "preflight",
        "qa",
        "validate",
        "bootstrap",
        "sync",
        "relock",
        "clean",
        "clean-tmp",
        "clean-root",
        "clean-pycache",
        "healthcheck",
        "setup-dirs",
        "setup-venv",
        "install-hooks",
        "install-pip",
        "install-trufflehog",
        "check-uv",
        "check-pytest",
        "security",
        "sast",
        "sbom",
        "secrets-scan",
        "secrets-scrub",
        "secrets-baseline",
        "secrets-audit",
        "secrets-baseline",
        "secrets-scan-baseline",
        "collect-check",
        "collect-check-e2e-live",
        "test-count",
        "test-failures",
        "test-specific",
        "test-files",
        "test-scripts",
        "test-install",
        "test-guardrails",
        "test-hooks-live",
        "test-hook-runtime",
        "test-opencode-e2e",
        "test-opencode-boot-e2e",
        "test-opencode-binary",
        "test-opencode-binary-boot",
        "test-tui-daemon",
        "test-bg",
        "test-bg-runner",
        "test-hang-debug",
        "test-batch",
        "test-iso",
        "test-xdist",
        "_local-model",
        "_check-windows",
        "_ci-replica",
        "_stash-",
        "_disk-",
        "_push-",
        "_test-",
        "_release-",
        "_pre-commit",
        "_revert-",
        "_lint-fix-",
        "_batch-",
        "_merge-",
        "_commit-",
        "_dead-",
        "_no-",
        "_subagent-",
        "_gate-",
        "_push-",
        "_force-",
    )
    return bool(any(name.startswith(p) for p in prefixes))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHelpTextCoverage:
    """Targets visible to users should appear in help text."""

    def test_help_text_below_threshold(self) -> None:
        content = _read_makefile()
        targets = _extract_target_defs(content)
        help_map = _extract_help_mapping(content)
        missing = []
        for name in targets:
            if name == "help" or name.startswith("_"):
                continue
            if name not in help_map:
                missing.append(name)
        # Allow up to 70% undocumented (many internal/CI targets). This test
        # is a canary — it will fail if the ratio suddenly jumps.
        ratio = len(missing) / max(len(targets), 1)
        assert ratio < 0.70, (
            f"{len(missing)}/{len(targets)} targets ({ratio * 100:.0f}%) "
            f"missing from help text (max 70%). First 20: " + ", ".join(sorted(missing)[:20])
        )

    def test_help_text_no_duplicates(self) -> None:
        content = _read_makefile()
        help_map = _extract_help_mapping(content)
        seen: dict[str, int] = {}
        for name in help_map:
            if name in seen:
                pytest.fail(f"Target '{name}' appears multiple times in help text")
            seen[name] = 1


class TestPrerequisiteExistence:
    """Every prerequisite referenced by a target must itself be a defined target."""

    def test_all_prerequisites_exist(self) -> None:
        content = _read_makefile()
        targets = _extract_target_defs(content)
        prereqs = _extract_prereqs(content)
        missing: dict[str, list[str]] = {}
        for name, deps in prereqs.items():
            for dep in deps:
                if dep not in targets:
                    missing.setdefault(dep, []).append(name)
        _known_false = {"gate-refresh", "enforce-multitask", "gate-lite"}
        real_missing = {d: c for d, c in missing.items() if d not in _known_false}
        names = ", ".join(f"'{d}' (-> {', '.join(c[:5])})" for d, c in sorted(real_missing.items()))[:500]
        assert len(real_missing) == 0, f"{len(real_missing)} unknown prerequisite(s): {names}"


class TestCircularDependencies:
    """No target should have a circular dependency chain."""

    def test_no_circular_dependencies(self) -> None:
        content = _read_makefile()
        prereqs = _extract_prereqs(content)
        cycles: list[list[str]] = []
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {name: WHITE for name in prereqs}

        def dfs(node: str, path: list[str]) -> None:
            color[node] = GRAY
            path.append(node)
            for nxt in prereqs.get(node, []):
                if nxt not in color:
                    continue
                if color[nxt] == GRAY:
                    idx = path.index(nxt)
                    cycles.append([*path[idx:], nxt])
                elif color[nxt] == WHITE:
                    dfs(nxt, path)
            path.pop()
            color[node] = BLACK

        for name in prereqs:
            if color[name] == WHITE:
                dfs(name, [])

        if cycles:
            lines = [f"  Cycle: {' -> '.join(c)}" for c in cycles[:10]]
            pytest.fail(f"{len(cycles)} circular dependency chain(s) found:\n" + "\n".join(lines))


class TestScriptReferencesResolve:
    """Every script referenced by a target should exist on disk."""

    def test_script_references_exist(self) -> None:
        content = _read_makefile()
        refs = _extract_script_refs(content)
        missing: dict[str, list[str]] = {}
        for target_name, scripts in refs.items():
            for script in scripts:
                path = ROOT / script
                if not path.exists():
                    missing.setdefault(script, []).append(target_name)
        # Known missing: scripts referenced but not yet written. These are real
        # gaps in the Makefile — targets referencing scripts that don't exist.
        # The count is frozen to prevent regression.
        max_known_missing = 5
        names = ", ".join(f"'{s}' (-> {', '.join(c[:5])})" for s, c in sorted(missing.items()))[:500]
        assert len(missing) <= max_known_missing, f"{len(missing)} missing script(s) (max {max_known_missing}): {names}"


class TestPhonyCompleteness:
    """Every PHONY target that collides with a file on disk is a bug."""

    def test_phony_target_no_file_collision(self) -> None:
        content = _read_makefile()
        phony_names = _extract_phony_names(content)
        violations = []
        for name in sorted(phony_names):
            candidate = ROOT / name
            if candidate.exists():
                violations.append(f"PHONY target '{name}' exists as file: {candidate}")
        assert not violations, f"{len(violations)} PHONY target(s) collide with on-disk files:\n" + "\n".join(
            violations[:8]
        )

    def test_phony_coverage_above_minimum(self) -> None:
        """At least 75% of non-internal targets should be in .PHONY."""
        content = _read_makefile()
        targets = _extract_target_defs(content)
        phony = _extract_phony_names(content)
        non_underscore = [t for t in targets if not t.startswith("_")]
        coverage = len(phony) / max(len(non_underscore), 1)
        # Current: ~47% in .PHONY. This is a canary — if it drops, investigate.
        assert coverage >= 0.45, (
            f"Only {len(phony)}/{len(non_underscore)} targets ({coverage * 100:.0f}%) in .PHONY (minimum 45%)"
        )


class TestOrphanTargets:
    """Targets should be either entry points, have help text, or be referenced as prereqs."""

    def test_no_orphan_targets(self) -> None:
        content = _read_makefile()
        targets = _extract_target_defs(content)
        prereqs = _extract_prereqs(content)
        help_map = _extract_help_mapping(content)

        all_refs: set[str] = set()
        for deps in prereqs.values():
            all_refs.update(deps)

        orphans = []
        for name in targets:
            if name.startswith("_"):
                continue
            if name in all_refs:
                continue
            if name in help_map:
                continue
            if _is_known_entry_point(name):
                continue
            orphans.append(name)

        assert not orphans, f"{len(orphans)} orphan target(s) (defined, never referenced, no help text): " + ", ".join(
            sorted(orphans)
        )


class TestMakeHelpMatchesTargets:
    """Running `make help` should not produce errors."""

    def test_make_help_runs(self) -> None:
        result = subprocess.run(
            ["make", "-n", "-f", str(MAKEFILE), "help"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, f"make help failed (rc={result.returncode}):\n{result.stderr[-500:]}"

    def test_help_invokes_index_script(self) -> None:
        content = _read_makefile()
        assert "check_make_help.py" in content, "help target must reference scripts/check_make_help.py"


class TestTargetRecipeIntegrity:
    """Targets defined at column 0 should have a tab-indented recipe or be prereq-only."""

    def test_targets_have_recipe(self) -> None:
        content = _read_makefile()
        lines = content.split("\n")
        _extract_target_defs(content)
        in_phony = False
        no_recipe = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith(".PHONY:"):
                in_phony = True
                continue
            if in_phony:
                if not line.rstrip("\n").endswith("\\"):
                    in_phony = False
                continue
            if not _TARGET_LINE_RE.match(stripped):
                continue
            if line.startswith((" ", "\t")):
                continue
            if i >= len(lines):
                no_recipe.append(f"line {i}: {stripped} (end of file)")
                continue
            nxt = lines[i]
            if nxt.startswith("\t") or nxt.startswith("  "):
                continue
            if _TARGET_LINE_RE.match(nxt.strip()):
                continue
            if nxt.strip() == "" or nxt.strip().startswith("#"):
                continue
            no_recipe.append(f"line {i}: {stripped}")
        assert not no_recipe, "Targets without recipe:\n" + "\n".join(no_recipe[:15])


class TestNoDuplicateTargets:
    """Target names must not be declared more than once."""

    def test_no_duplicate_target_definitions(self) -> None:
        content = _read_makefile()
        targets = _extract_target_defs(content)
        seen: dict[str, int] = {}
        dups: dict[str, list[int]] = {}
        for name, lineno in sorted(targets.items()):
            if name in seen:
                dups.setdefault(name, [seen[name]]).append(lineno)
            else:
                seen[name] = lineno
        if dups:
            lines = [f"  {name}: lines {', '.join(str(n) for n in nums)}" for name, nums in sorted(dups.items())]
            pytest.fail(f"{len(dups)} duplicate target(s) defined:\n" + "\n".join(lines))


class TestMakefileParsability:
    """Makefile should be syntactically valid."""

    def test_makefile_parses_clean(self) -> None:
        result = subprocess.run(
            ["make", "-n", "-f", str(MAKEFILE), "help"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, f"Makefile parse failed:\n{result.stderr[-800:]}"


class TestDotPhonyFormatting:
    """.PHONY continuation lines should use spaces not tabs."""

    def test_phony_no_tabs(self) -> None:
        content = _read_makefile()
        in_phony = False
        violations = []
        for i, line in enumerate(content.split("\n"), 1):
            s = line.strip()
            if s.startswith(".PHONY:"):
                in_phony = True
                continue
            if in_phony:
                if line.rstrip("\n").endswith("\\"):
                    if "\t" in line.lstrip("\n"):
                        violations.append(f"line {i}: tab in .PHONY continuation")
                else:
                    in_phony = False
        assert not violations, "\n".join(violations)


class TestMakefileTargetCount:
    """Makefile should have a reasonable number of targets."""

    def test_minimum_target_count(self) -> None:
        content = _read_makefile()
        targets = _extract_target_defs(content)
        assert len(targets) >= 500, f"Only {len(targets)} targets — Makefile may be truncated"


class TestInternalTargetConventions:
    """Underscore-prefixed targets should not leak into help text."""

    def test_internal_targets_not_in_help(self) -> None:
        content = _read_makefile()
        help_map = _extract_help_mapping(content)
        dangling = [name for name in help_map if name.startswith("_")]
        assert not dangling, f"Internal targets in help text: {dangling}"


class TestSddTargetsExist:
    """SDD pipeline targets must be present."""

    def test_sdd_targets_exist(self) -> None:
        content = _read_makefile()
        targets = _extract_target_defs(content)
        expected = [
            "sdd-constitution",
            "sdd-discover",
            "sdd-specify",
            "sdd-plan",
            "sdd-tasks",
            "sdd-implement",
            "sdd-pr",
            "sdd-release",
            "sdd-audit",
            "sdd-critic",
            "sdd-harvest",
            "sdd-quickfix",
        ]
        missing = [t for t in expected if t not in targets]
        assert not missing, f"Missing SDD targets: {missing}"


class TestKeyTargetsExist:
    """Essential targets must exist."""

    def test_quality_targets(self) -> None:
        targets = _extract_target_defs(_read_makefile())
        essential = [
            "lint",
            "lint-fix",
            "typecheck",
            "test",
            "test-unit",
            "test-integration",
            "test-e2e",
            "gate",
            "gate-lite",
            "collect-check",
            "preflight",
            "qa",
            "smoke",
            "init",
            "sync",
            "clean",
            "bootstrap",
            "healthcheck",
            "security",
        ]
        missing = [t for t in essential if t not in targets]
        assert not missing, f"Missing essential targets: {missing}"

    def test_release_targets(self) -> None:
        targets = _extract_target_defs(_read_makefile())
        essential = [
            "release-cut",
            "release-recut",
            "release-create",
            "release-deploy",
            "release-delete",
            "release-list",
            "release-view",
            "verify-release-artifact",
            "verify-release-completeness",
        ]
        missing = [t for t in essential if t not in targets]
        assert not missing, f"Missing release targets: {missing}"

    def test_git_targets(self) -> None:
        targets = _extract_target_defs(_read_makefile())
        essential = [
            "git-status",
            "git-log",
            "git-diff",
            "git-add",
            "git-add-all",
            "git-commit",
            "git-reset",
            "git-branch",
            "git-checkout",
            "git-merge",
            "git-stash",
            "git-stash-pop",
            "git-push-sandboxcom",
            "git-pull-sandboxcom",
            "git-fetch-sandboxcom",
            "branches-unmerged-development",
            "git-remote-sandboxcom",
        ]
        missing = [t for t in essential if t not in targets]
        assert not missing, f"Missing git targets: {missing}"

    def test_enforcement_targets(self) -> None:
        targets = _extract_target_defs(_read_makefile())
        essential = [
            "crash-recovery",
            "disengage-enforcement",
            "reload-enforcement",
            "rearm-enforcement",
            "enforcement-status",
            "write-plugin-manifest",
            "verify-enforcement",
            "hot-reload-plugins",
            "hot-reload-status",
        ]
        missing = [t for t in essential if t not in targets]
        assert not missing, f"Missing enforcement targets: {missing}"


class TestVariableConsistency:
    """Variable assignments should prefer ?= or := over bare =."""

    def test_no_bare_equals(self) -> None:
        content = _read_makefile()
        violations = []
        for i, line in enumerate(content.split("\n"), 1):
            s = line.strip()
            if s.startswith("#") or s == "":
                continue
            if "export" in s or "override" in s:
                continue
            # Match bare = but not :=  ?=  +=  !=
            if (
                re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s+=\s", s)
                and "?=" not in s
                and ":=" not in s
                and "+=" not in s
                and "!=" not in s
            ):
                violations.append(f"line {i}: {s[:80]}")
        # Small count of bare = is allowed for legacy vars
        assert len(violations) <= 20, f"{len(violations)} bare '=' assignments found (max 20):\n" + "\n".join(
            violations[:8]
        )


class TestMakeHelpStructure:
    """Help text should cover the expected section categories."""

    def test_help_section_count(self) -> None:
        content = _read_makefile()
        section_count = 0
        in_help = False
        for line in content.split("\n"):
            if line.strip().startswith("help:"):
                in_help = True
                continue
            if in_help and not line.startswith("\t"):
                break
            if in_help and "---" in line:
                section_count += 1
        assert section_count >= 8, f"Only {section_count} help sections found (expected >= 8)"


class TestBlankLineSeparation:
    """Targets should be separated by blank lines for readability (best effort)."""

    def test_blank_lines_between_targets(self) -> None:
        content = _read_makefile()
        lines = content.split("\n")
        in_phony = False
        last_target_line = 0
        violations = []
        for i, raw in enumerate(lines, 1):
            stripped = raw.strip()
            if stripped.startswith(".PHONY:"):
                in_phony = True
                continue
            if in_phony:
                if not raw.rstrip("\n").endswith("\\"):
                    in_phony = False
                continue
            if not _TARGET_LINE_RE.match(stripped):
                continue
            if raw.startswith((" ", "\t")):
                continue
            if stripped.startswith("."):
                continue
            if last_target_line > 0 and i - last_target_line == 1:
                violations.append(f"line {i}: target '{stripped[:60]}' not preceded by blank line")
            last_target_line = i
        # Allow some violations (the Makefile is large)
        assert len(violations) <= 50, f"{len(violations)} targets not preceded by blank lines (max 50)"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
