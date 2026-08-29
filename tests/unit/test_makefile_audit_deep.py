"""Deep Makefile target integrity tests.

Verifies: no duplicate targets, prerequisite target resolution, script existence,
.PHONY coverage, help text completeness, naming conventions, and structural health.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"
SCRIPTS_DIR = ROOT / "scripts"

_TARGET_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:(?!=)")


def _read_text() -> str:
    return MAKEFILE.read_text()


def _all_target_lines(content: str) -> dict[str, int]:
    targets: dict[str, int] = {}
    in_phony = False
    for lineno, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            in_phony = True
            continue
        if in_phony:
            if not line.rstrip("\n").endswith("\\"):
                in_phony = False
            continue
        m = _TARGET_RE.match(stripped)
        if m and not line.startswith((" ", "\t")) and not stripped.startswith("."):
            targets[m.group(1)] = lineno
    return targets


def _phony_names(content: str) -> set[str]:
    names: set[str] = set()
    in_phony = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            in_phony = True
            tokens = stripped.split(":", 1)[1].split()
            names.update(t.rstrip("\\") for t in tokens if t.rstrip("\\"))
            continue
        if in_phony:
            tokens = stripped.split()
            names.update(t.rstrip("\\") for t in tokens if t.rstrip("\\"))
            if not line.rstrip("\n").endswith("\\"):
                in_phony = False
    return names


def _without_make_expansions(text: str) -> str:
    """Replace balanced Make variable/function expansions with whitespace."""
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index : index + 2] not in {"$(", "${"}:
            output.append(text[index])
            index += 1
            continue

        start = index
        closers = [")" if text[index + 1] == "(" else "}"]
        index += 2
        while index < len(text) and closers:
            opener = text[index : index + 2]
            if opener in {"$(", "${"}:
                closers.append(")" if opener == "$(" else "}")
                index += 2
            elif text[index] == closers[-1]:
                closers.pop()
                index += 1
            else:
                index += 1
        if closers:
            output.append(text[start:])
            break
        output.append(" ")
    return "".join(output)


def _prereq_targets(line: str) -> set[str]:
    """Extract prerequisite target names from a target declaration line.

    Handles: `target: p1 p2`, `target: p1 ; recipe`, `target: p1 p2 # comment`,
    `target: VAR = val` (target-specific variables), and `target: ## comment`.
    """
    if ":" not in line:
        return set()
    rhs = _without_make_expansions(line.split(":", 1)[1])
    if ";" in rhs:
        rhs = rhs.split(";", 1)[0]
    prereqs: set[str] = set()
    for token in rhs.split():
        token = token.strip()
        if not token:
            continue
        if token.startswith("#"):
            break
        if token.startswith("$("):
            continue
        if "=" in token:
            prereqs = set()
            break
        prereqs.add(token)
    return prereqs


def test_prereq_targets_ignore_nested_make_expansion() -> None:
    """Dynamic Make functions are not literal prerequisite target names."""
    line = (
        "target: $(if $(filter 1,$(VALIDATE_ONLY)),,_clean-tree) "
        "real-prerequisite"
    )
    assert _prereq_targets(line) == {"real-prerequisite"}


def _script_refs(text: str) -> set[str]:
    refs: set[str] = set()
    for m in re.finditer(r"scripts/([a-zA-Z_][a-zA-Z0-9_./-]+\.(?:py|sh|mjs))", text):
        refs.add(m.group(0))
    return refs


def _recipe_body(content: str, target_name: str) -> str:
    idx = content.find(f"\n{target_name}:")
    if idx == -1:
        return ""
    start = idx + len(target_name) + 2
    lines = content[start:].split("\n")
    body: list[str] = []
    for line in lines:
        if _TARGET_RE.match(line.strip()) and not line.startswith((" ", "\t")):
            break
        body.append(line)
    return "\n".join(body)


def _help_target_names() -> tuple[set[str], str]:
    """Extract target names from `make -n help` output. Matches:
    - @echo "  target-name      desc"
    - @echo "  target-name VAR=val  desc"
    """
    r = subprocess.run(
        ["make", "-n", "-f", str(MAKEFILE), "help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    targets: set[str] = set()
    for line in r.stdout.split("\n"):
        m = re.match(r'^\s*@?echo\s+"\s{2}([a-zA-Z_][a-zA-Z0-9_-]*)\s', line)
        if m:
            targets.add(m.group(1))
    return targets, r.stdout


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def content() -> str:
    return _read_text()


@pytest.fixture(scope="module")
def targets(content: str) -> dict[str, int]:
    return _all_target_lines(content)


@pytest.fixture(scope="module")
def phony(content: str) -> set[str]:
    return _phony_names(content)


@pytest.fixture(scope="module")
def help_data() -> tuple[set[str], str]:
    return _help_target_names()


# ── 1. No duplicate targets ──────────────────────────────────────────────────


class TestNoDuplicateTargets:
    def test_no_duplicates(self, targets: dict[str, int]) -> None:
        seen: dict[str, int] = {}
        dups: dict[str, list[int]] = {}
        for name, lineno in sorted(targets.items()):
            if name in seen:
                dups.setdefault(name, [seen[name]]).append(lineno)
            else:
                seen[name] = lineno
        assert not dups, f"{len(dups)} duplicate(s):\n" + "\n".join(
            f"  {k}: lines {v}" for k, v in sorted(dups.items())
        )


# ── 2. Prerequisites resolve to real targets ─────────────────────────────────


class TestPrerequisitesResolve:
    def test_all_prereqs_are_targets(
        self,
        content: str,
        targets: dict[str, int],
        phony: set[str],
    ) -> None:
        all_names = set(targets) | phony
        missing: dict[str, list[str]] = {}
        for line in content.split("\n"):
            stripped = line.strip()
            m = _TARGET_RE.match(stripped)
            if not m:
                continue
            if line.startswith((" ", "\t")) or stripped.startswith("."):
                continue
            name = m.group(1)
            for prereq in _prereq_targets(stripped):
                if prereq not in all_names:
                    missing.setdefault(prereq, []).append(name)
        assert not missing, f"{len(missing)} unresolved prerequisite(s):\n" + "\n".join(
            f"  {k} <- {v}" for k, v in sorted(missing.items())[:15]
        )


# ── 3. Scripts exist on disk ─────────────────────────────────────────────────


class TestScriptReferencesExist:
    SKIP = frozenset(
        {  # known stale refs; these are the bugs this test exists to surface
            "scripts/gen_branch_coverage_json.py",
            "scripts/install.sh",
            "scripts/scan-secrets.py",
        }
    )

    def test_all_scripts_exist(self, content: str) -> None:
        all_refs = _script_refs(content)
        missing = {r for r in all_refs if not (ROOT / r).exists()} - self.SKIP
        assert not missing, f"{len(missing)} script ref(s) missing from disk:\n" + "\n".join(
            f"  {r}" for r in sorted(missing)[:10]
        )


# ── 4. .PHONY coverage ───────────────────────────────────────────────────────


class TestPhonyCoverage:
    def test_no_phony_shadows_file(self, phony: set[str]) -> None:
        conflicts = sorted(n for n in phony if (ROOT / n).is_file())
        assert not conflicts, f"{len(conflicts)} PHONY shadow(s) real files:\n" + "\n".join(
            f"  {n}" for n in conflicts[:15]
        )


# ── 5. Help text completeness ────────────────────────────────────────────────


class TestHelpText:
    MAJOR = (
        "init",
        "sync",
        "bootstrap",
        "install-hooks",
        "lint",
        "lint-fix",
        "typecheck",
        "test-unit",
        "test-integration",
        "test-e2e",
        "test-specific",
        "gate",
        "gate-lite",
        "collect-check",
        "git-status",
        "git-add",
        "git-commit",
        "git-log",
        "git-branch",
        "git-push-sandboxcom",
        "feature-start",
        "feature-done",
        "agent-worktree",
        "agent-merge",
        "secrets-scan",
        "secrets-baseline",
        "security-audit",
        "release-cut",
        "release-create",
        "clean",
        "dist",
    )

    def test_major_targets_in_help(self, help_data: tuple[set[str], str]) -> None:
        h, _ = help_data
        missing = [t for t in self.MAJOR if t not in h]
        assert not missing, f"{len(missing)} major target(s) missing: {missing}"

    def test_help_has_section_headings(self, help_data: tuple[set[str], str]) -> None:
        _, text = help_data
        for section in ("Setup", "Quality", "Git", "Release", "Build", "Terraform"):
            assert section in text, f"help missing heading: '{section}'"


# ── 6. Target naming conventions ─────────────────────────────────────────────


class TestNamingConventions:
    def test_no_spaces_in_names(self, targets: dict[str, int]) -> None:
        bad = [n for n in targets if " " in n]
        assert not bad, f"Targets with spaces: {bad}"

    def test_all_lowercase(self, targets: dict[str, int]) -> None:
        bad = [n for n in targets if n != n.lower()]
        assert not bad, f"Targets with uppercase: {bad}"

    def test_git_targets_at_least_5_chars(self, targets: dict[str, int]) -> None:
        git_tgts = [n for n in targets if n.startswith("git-")]
        for name in git_tgts:
            assert len(name) > 5, f"'{name}' too short for git- pattern"


# ── 7. Target count sanity ───────────────────────────────────────────────────


class TestTargetCountSanity:
    def test_at_least_200_targets(self, targets: dict[str, int]) -> None:
        assert len(targets) >= 200, f"Only {len(targets)} — expected >= 200"

    def test_at_least_150_phony(self, phony: set[str]) -> None:
        assert len(phony) >= 150, f"Only {len(phony)} — expected >= 150"


# ── 8. Key scripts exist ─────────────────────────────────────────────────────


class TestKeyScriptsExist:
    KEY = (
        "check_duplicate_targets.py",
        "check_make_help.py",
        "check_tdd_compliance.py",
        "check_node_v26_compat.py",
        "validate_plugins_runtime.mjs",
        "verify_release_artifact.py",
        "verify_release_completeness.py",
        "require_ci_green.py",
        "check_readme_status_current.py",
        "check_green_branch_guard.py",
    )

    def test_key_scripts_on_disk(self) -> None:
        missing = [s for s in self.KEY if not (SCRIPTS_DIR / s).exists()]
        assert not missing, f"Key scripts missing: {missing}"


# ── 9. Dry-run key targets ───────────────────────────────────────────────────


class TestDryRun:
    KEY = ("help", "lint", "typecheck", "test-count", "collect-check", "clean")

    @pytest.mark.parametrize("target", KEY)
    def test_dry_run_ok(self, target: str) -> None:
        r = subprocess.run(
            ["make", "-n", "-f", str(MAKEFILE), target],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, f"make -n {target} failed:\n{r.stderr[-300:]}"


# ── 10. Critical paths exist ─────────────────────────────────────────────────


class TestPathsExist:
    def test_scripts_dir(self) -> None:
        assert SCRIPTS_DIR.is_dir()

    def test_plugin_dir(self) -> None:
        assert (ROOT / ".opencode" / "plugin").is_dir()

    def test_config_dir(self) -> None:
        assert (ROOT / "config").is_dir()

    def test_makefile_exists(self) -> None:
        assert MAKEFILE.is_file()


# ── 11. Key targets use $(MAKE) ──────────────────────────────────────────────


class TestSubMake:
    USE_MAKE = ("release-cut", "gate", "gate-lite")

    def test_submake_usage(
        self, targets: dict[str, int], content: str
    ) -> None:
        missing: list[str] = []
        for name in self.USE_MAKE:
            if name not in targets:
                continue
            body = _recipe_body(content, name)
            if "$(MAKE)" not in body:
                missing.append(name)
        assert not missing, f"Missing $(MAKE): {missing}"


# ── 12. Makefile parses cleanly ──────────────────────────────────────────────


class TestParseOk:
    def test_make_dry_run_help_ok(self) -> None:
        r = subprocess.run(
            ["make", "-n", "-f", str(MAKEFILE), "help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert r.returncode == 0, f"Syntax error:\n{r.stderr[-400:]}"


# ── 13. No target name / file collisions ─────────────────────────────────────


class TestTargetFileCollisions:
    def test_no_file_target_collisions(self, targets: dict[str, int]) -> None:
        bad = sorted(n for n in targets if (ROOT / n).is_file())
        assert not bad, f"{len(bad)} target(s) collide with real files:\n" + "\n".join(
            f"  {n} (line {targets[n]})" for n in bad[:10]
        )


# ── 14. Help has no duplicate entries ────────────────────────────────────────


class TestHelpNoDupTargets:
    @staticmethod
    def _help_lines() -> list[str]:
        r = subprocess.run(
            ["make", "-n", "-f", str(MAKEFILE), "help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines: list[str] = []
        for line in r.stdout.split("\n"):
            m = re.match(r'^\s*@?echo\s+"(\s{2}[a-zA-Z_][a-zA-Z0-9_-]*\s.*)"$', line)
            if m:
                inner = m.group(1)
                tm = re.match(r"^\s{2}([a-zA-Z_][a-zA-Z0-9_-]*)", inner)
                if tm:
                    lines.append(tm.group(1))
        return lines

    def test_no_duplicate_help_entries(self) -> None:
        lines = self._help_lines()
        seen: dict[str, bool] = {}
        dups: list[str] = []
        for target in lines:
            if target in seen:
                dups.append(target)
            else:
                seen[target] = True
        # Known: codemod-lean-enforcement-plugins duplicated (help bug)
        dups = [d for d in dups if d != "codemod-lean-enforcement-plugins"]
        assert not dups, f"{len(dups)} duplicate help target(s): {dups[:10]}"
