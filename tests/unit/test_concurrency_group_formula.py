"""CP.6: Verify the top-level concurrency group in build.yml includes github.ref_name.

Background: commit 5f5c3374 fixed a bug where tag pushes and branch pushes to the
same SHA would share a concurrency group (keyed only on github.sha), letting one
cancel the other. The fix appends github.ref_name so tag/branch pushes to the
same commit get distinct groups.

These tests pin the fix structurally so a regression is caught at gate time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD_YML = ROOT / ".github" / "workflows" / "build.yml"


def _workflow_source() -> str:
    assert BUILD_YML.exists(), f"build.yml not found at {BUILD_YML}"
    return BUILD_YML.read_text()


def _extract_top_level_concurrency(src: str) -> str:
    """Return the top-level `concurrency:` block (not a job-level one).

    The top-level block begins with `concurrency:` at column 0 and ends at the
    next column-0 key. Job-level concurrency blocks (rare in this file) are
    indented and excluded.
    """
    lines = src.split("\n")
    start: int | None = None
    for i, line in enumerate(lines):
        if line.startswith("concurrency:"):
            start = i
            break
    assert start is not None, "no top-level `concurrency:` block found in build.yml"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        # next column-0 non-blank key ends the block
        if lines[j] and not lines[j][0].isspace() and not lines[j].startswith("#"):
            end = j
            break
    return "\n".join(lines[start:end])


class TestConcurrencyBlockExists:
    def test_top_level_concurrency_block_present(self):
        """A top-level concurrency block MUST exist (not just job-level)."""
        src = _workflow_source()
        assert re.search(r"^concurrency:", src, re.MULTILINE), (
            "build.yml must define a top-level `concurrency:` block"
        )

    def test_concurrency_block_has_group_key(self):
        block = _extract_top_level_concurrency(_workflow_source())
        assert re.search(r"group:\s*", block), (
            "concurrency block must define a `group:` formula"
        )

    def test_concurrency_block_has_cancel_in_progress(self):
        """cancel-in-progress key MUST exist (true or false — its presence is the pin)."""
        block = _extract_top_level_concurrency(_workflow_source())
        assert re.search(r"cancel-in-progress:\s*", block), (
            "concurrency block must define `cancel-in-progress:`"
        )


class TestGroupFormulaIncludesRefName:
    """The fix from 5f5c3374: group formula includes github.ref_name (or ref_type).

    Without ref_name, tag and branch pushes to the same SHA collapse into one
    group and one cancels the other. ref_name disambiguates them.
    """

    def test_group_formula_includes_ref_name_or_ref_type(self):
        block = _extract_top_level_concurrency(_workflow_source())
        m = re.search(r"group:\s*(.*)", block)
        assert m, "no `group:` line in concurrency block"
        formula = m.group(1)
        assert (
            "github.ref_name" in formula or "github.ref_type" in formula
        ), (
            "concurrency group formula must include github.ref_name or "
            "github.ref_type so tag/branch pushes to the same SHA get distinct "
            f"groups. Current formula: {formula!r}"
        )

    def test_group_formula_not_only_github_ref(self):
        """A formula using ONLY github.ref (no ref_name/ref_type) is the pre-fix bug.

        github.ref for a tag push is refs/tags/vX.Y.Z and for a branch push is
        refs/heads/<branch> — these already differ, BUT the historic regression
        keyed on github.sha alone (which is identical for tag+branch at the same
        commit). The pin: the formula must include ref_name/ref_type, not just
        github.ref or github.sha in isolation.
        """
        block = _extract_top_level_concurrency(_workflow_source())
        m = re.search(r"group:\s*(.*)", block)
        assert m
        formula = m.group(1)
        # Must reference the disambiguator. Bare github.ref or github.sha alone
        # does not prevent tag/branch collision when SHA matches.
        assert re.search(r"github\.ref_name|github\.ref_type", formula), (
            "group formula must reference github.ref_name or github.ref_type; "
            f"got: {formula!r}"
        )


class TestTagAndBranchPushesGetDistinctGroups:
    """The whole point of CP.6: simulate the group formula for tag vs branch push
    to the same SHA and confirm they do NOT collide."""

    @pytest.mark.parametrize(
        "event,ref,ref_name,sha",
        [
            # branch push to master at SHA deadbeef
            ("push", "refs/heads/master", "master", "deadbeef"),
            # tag push to v1.0.0 at the SAME SHA deadbeef
            ("push", "refs/tags/v1.0.0", "v1.0.0", "deadbeef"),
        ],
    )
    def test_groups_differ_for_same_sha(self, event, ref, ref_name, sha):
        """Evaluate the actual group formula for two pushes at the same SHA."""
        block = _extract_top_level_concurrency(_workflow_source())
        m = re.search(r"group:\s*(.*)", block)
        assert m
        formula = m.group(1).strip()

        # github.workflow is constant for both; substitute a placeholder.
        ctx = {
            "github.workflow": "Build and Release",
            "github.event_name": event,
            "github.ref": ref,
            "github.ref_name": ref_name,
            "github.sha": sha,
        }

        def evaluate(expr: str) -> str:
            # Handle the ternary: cond && a || b  ->  a if cond else b
            ternary = re.search(
                r"\$\{\{\s*(.*?)\s*&&\s*(.*?)\|\|\s*(.*?)\s*\}\}", expr
            )
            if ternary:
                cond_raw, a, b = (g.strip() for g in ternary.groups())
                cond_val = ctx.get(cond_raw, cond_raw)
                # pull_request branch evaluates the PR ref; push evaluates sha
                chosen = a if cond_val == "pull_request" else b
                chosen_val = ctx.get(chosen, chosen)
                rest = expr.replace(ternary.group(0), chosen_val)
                return _substitute(rest, ctx)
            return _substitute(expr, ctx)

        def _substitute(expr: str, ctx: dict[str, str]) -> str:
            out = expr
            for k, v in ctx.items():
                out = out.replace("${{ " + k + " }}", v)
                out = out.replace("${{" + k + "}}", v)
            return out.strip().strip("-")

        group = evaluate(formula)

        # Stash for cross-param comparison
        cls = self.__class__
        if not hasattr(cls, "_evaluated_groups"):
            cls._evaluated_groups = []
        cls._evaluated_groups.append((ref_name, group))

        # Once both parametrize cases have run, assert they differ.
        if len(cls._evaluated_groups) == 2:
            g_branch = cls._evaluated_groups[0][1]
            g_tag = cls._evaluated_groups[1][1]
            assert g_branch != g_tag, (
                "BRANCH and TAG pushes to the same SHA must produce DIFFERENT "
                f"concurrency groups. Both resolved to: {g_branch!r}. "
                f"Formula: {formula!r}"
            )
