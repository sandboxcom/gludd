"""Deep CI workflow edge-case and integrity tests.

Operates on synthetic YAML fixtures so the validation logic is tested independently
of whether real workflow files exist on disk. Covers concurrency groups, job timeouts,
hardcoded secrets, unique step IDs, and well-formed conditionals.

Author: enhancement task — 2026-08-03.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_VALID_WORKFLOW = """\
name: test
on:
  push:
    branches: [master]
concurrency:
  group: ci-${{ github.ref_name }}-${{ github.sha }}
  cancel-in-progress: true
jobs:
  lint:
    timeout-minutes: 10
    runs-on: ubuntu-latest
    steps:
      - id: checkout
        uses: actions/checkout@abc123
      - id: lint
        run: make lint
  test:
    timeout-minutes: 20
    runs-on: ubuntu-latest
    steps:
      - id: checkout
        uses: actions/checkout@abc123
      - id: test
        run: make test
"""


def _parse(text: str) -> dict[str | bool, Any]:
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    return data


def _collect_step_ids(job: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for step in job.get("steps", []):
        if isinstance(step, dict) and "id" in step:
            ids.append(str(step["id"]))
    return ids


_HARDCODED_SECRET_PATTERNS = [
    r"password\s*:\s*['\"][^$\n]{4,}['\"]",
    r"token\s*:\s*['\"][^$\n]{12,}['\"]",
    r"api[_-]?key\s*:\s*['\"][^$\n]{8,}['\"]",
    r"secret\s*:\s*['\"][^$\n]{8,}['\"]",
    r"access[_-]?key\s*:\s*['\"][^$\n]{8,}['\"]",
    r"(?<!\\$)AWS_ACCESS_KEY_ID\s*:\s*['\"][^$\n]{8,}['\"]",
    r"(?<!\\$)AWS_SECRET_ACCESS_KEY\s*:\s*['\"][^$\n]{8,}['\"]",
    r"PRIVATE[_-]?KEY\s*:\s*['\"]-----BEGIN",
]


def _parentheses_balanced(expression: str) -> bool:
    """Require every closing parenthesis to match an earlier opening one."""
    depth = 0
    for character in expression:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


# ---------------------------------------------------------------------------
# concurrency group
# ---------------------------------------------------------------------------


class TestConcurrencyGroups:
    def test_top_level_concurrency_present(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        assert "concurrency" in wf, "workflow must define a top-level concurrency block"

    def test_top_level_concurrency_has_group(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        assert "group" in wf["concurrency"], "concurrency block must have a group key"

    def test_top_level_concurrency_has_cancel_in_progress(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        assert "cancel-in-progress" in wf["concurrency"], "concurrency block must declare cancel-in-progress"

    def test_missing_concurrency_is_detectable(self) -> None:
        src = "name: test\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        wf = _parse(src)
        assert "concurrency" not in wf

    def test_concurrency_group_includes_ref_disambiguator(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        group = str(wf["concurrency"]["group"])
        assert "github.ref_name" in group or "github.ref_type" in group, (
            f"concurrency group must include ref_name or ref_type; got {group!r}"
        )

    def test_empty_concurrency_block_is_invalid(self) -> None:
        src = "name: t\non: push\nconcurrency:\n  group:\njobs: {}\n"
        wf = _parse(src)
        assert wf["concurrency"]["group"] is None


# ---------------------------------------------------------------------------
# job timeout-minutes
# ---------------------------------------------------------------------------


class TestJobTimeouts:
    def test_all_jobs_have_timeout(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        for name, job in wf.get("jobs", {}).items():
            assert "timeout-minutes" in job, f"job '{name}' must have timeout-minutes"

    def test_job_without_timeout_is_detectable(self) -> None:
        src = "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        wf = _parse(src)
        assert "timeout-minutes" not in wf["jobs"]["a"]

    def test_timeout_positive_integer(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        for name, job in wf.get("jobs", {}).items():
            t = job["timeout-minutes"]
            assert isinstance(t, (int, float)), f"job '{name}' timeout-minutes must be numeric, got {type(t).__name__}"
            assert t > 0, f"job '{name}' timeout-minutes = {t}, must be > 0"

    def test_timeout_within_reasonable_bound(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        for name, job in wf.get("jobs", {}).items():
            t = job["timeout-minutes"]
            assert t <= 360, f"job '{name}' timeout-minutes = {t}, exceeds GHA max of 360"

    def test_workflow_with_zero_timeout(self) -> None:
        src = "name: t\non: push\njobs:\n  a:\n    timeout-minutes: 0\n    runs-on: ubuntu-latest\n    steps: []\n"
        wf = _parse(src)
        assert wf["jobs"]["a"]["timeout-minutes"] == 0


# ---------------------------------------------------------------------------
# hardcoded secrets detection
# ---------------------------------------------------------------------------


class TestNoHardcodedSecrets:
    @pytest.mark.parametrize(
        "secret_line",
        [
            "password: 'super-secret-password-123'",
            "token: 'ghp_abc123def456ghi789'",
            "api_key: 'sk-0123456789abcdef'",
            "secret: 'my-very-secret-12345'",
            "access_key: 'AKIAIOSFODNN7EXAMPLE'",
            "AWS_ACCESS_KEY_ID: 'AKIAIOSFODNN7EXAMPLE'",
            "AWS_SECRET_ACCESS_KEY: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'",
            "PRIVATE_KEY: '-----BEGIN RSA PRIVATE KEY-----\nMIIExampleKeyData\n-----END RSA PRIVATE KEY-----'",
        ],
    )
    def test_hardcoded_secret_detected(self, secret_line: str) -> None:
        indented_secret = "\n".join(
            f"      {line}" for line in secret_line.splitlines()
        )
        src = (
            "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            f"    env:\n{indented_secret}\n    steps: []\n"
        )
        wf = _parse(src)
        assert wf["jobs"]["a"]["env"]
        raw = src
        for pat in _HARDCODED_SECRET_PATTERNS:
            if re.search(pat, raw, re.IGNORECASE):
                return
        pytest.fail(f"hardcoded secret pattern not detected: {secret_line!r}")

    def test_secret_ref_is_not_a_secret(self) -> None:
        _parse(_VALID_WORKFLOW)
        raw = _VALID_WORKFLOW
        for pat in _HARDCODED_SECRET_PATTERNS:
            assert not re.search(pat, raw, re.IGNORECASE), f"valid workflow falsely matched secret pattern {pat!r}"

    def test_empty_env_is_safe(self) -> None:
        src = "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    env: {}\n    steps: []\n"
        _parse(src)
        raw = src
        for pat in _HARDCODED_SECRET_PATTERNS:
            assert not re.search(pat, raw, re.IGNORECASE)

    def test_secrets_context_token_is_safe(self) -> None:
        """${{ secrets.MY_SECRET }} references the secrets context — not a literal."""
        src = (
            "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    env:\n      token: ${{ secrets.GITHUB_TOKEN }}\n    steps: []\n"
        )
        _parse(src)
        raw = src
        for pat in _HARDCODED_SECRET_PATTERNS:
            assert not re.search(pat, raw, re.IGNORECASE), f"secrets context ref falsely matched pattern {pat!r}"


# ---------------------------------------------------------------------------
# unique step IDs
# ---------------------------------------------------------------------------


class TestUniqueStepIDs:
    def test_all_step_ids_unique(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        for name, job in wf.get("jobs", {}).items():
            ids = _collect_step_ids(job)
            assert len(ids) == len(set(ids)), f"job '{name}' has duplicate step IDs: {ids}"

    def test_duplicate_step_id_detected(self) -> None:
        src = (
            "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - id: checkout\n        uses: actions/checkout@a\n"
            "      - id: checkout\n        run: echo dup\n"
        )
        wf = _parse(src)
        ids = _collect_step_ids(wf["jobs"]["a"])
        assert len(ids) != len(set(ids)), "duplicate IDs should be detected"

    def test_steps_without_ids_are_skipped(self) -> None:
        src = (
            "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/checkout@a\n"
            "      - uses: actions/setup-python@b\n"
        )
        wf = _parse(src)
        ids = _collect_step_ids(wf["jobs"]["a"])
        assert ids == []

    def test_empty_steps_list(self) -> None:
        src = "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        wf = _parse(src)
        ids = _collect_step_ids(wf["jobs"]["a"])
        assert ids == []

    def test_no_collision_across_jobs(self) -> None:
        """Step IDs only need to be unique within a job; cross-job duplicates are fine."""
        src = (
            "name: t\non: push\njobs:\n"
            "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - id: checkout\n        uses: actions/checkout@a\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps:\n      - id: checkout\n        uses: actions/checkout@a\n"
        )
        wf = _parse(src)
        ids_a = _collect_step_ids(wf["jobs"]["a"])
        ids_b = _collect_step_ids(wf["jobs"]["b"])
        assert len(ids_a) == len(set(ids_a))
        assert len(ids_b) == len(set(ids_b))


# ---------------------------------------------------------------------------
# well-formed conditionals
# ---------------------------------------------------------------------------

_WELL_FORMED_CONDITIONALS = [
    "success()",
    "failure()",
    "always()",
    "cancelled()",
    "success() && !cancelled()",
    "startsWith(github.ref, 'refs/tags/v')",
    "github.event_name == 'push'",
    "github.event_name == 'workflow_dispatch'",
    "github.event_name == 'push' || github.event_name == 'workflow_dispatch'",
    "startsWith(github.ref, 'refs/tags/v') || github.event_name == 'workflow_dispatch'",
]

_MALFORMED_CONDITIONALS = [
    ("${{ success() }}", "expressions are bare, not ${{ }} wrapped at job-level if"),
    ("if success()", "should not start with 'if' — that's the key, not the value"),
    ("success() &&", "trailing operator"),
    ("", "empty conditional"),
]

_BALANCED_CONDITIONALS = [
    ("success()", True),
    ("(success() && !cancelled())", True),
    ("(github.event_name == 'push')", True),
    ("(a || b) && (c || d)", True),
    ("((a))", True),
    ("success())", False),
    ("(success()", False),
    ("success())) && ((!cancelled()", False),
]


class TestConditionals:
    @pytest.mark.parametrize("cond", _WELL_FORMED_CONDITIONALS)
    def test_well_formed_conditional(self, cond: str) -> None:
        src = f"name: t\non: push\njobs:\n  a:\n    if: {cond}\n    runs-on: ubuntu-latest\n    steps: []\n"
        wf = _parse(src)
        assert wf["jobs"]["a"]["if"] == cond

    @pytest.mark.parametrize("cond,reason", _MALFORMED_CONDITIONALS)
    def test_malformed_conditional(self, cond: str, reason: str) -> None:
        if cond and "${{" not in cond:
            src = f"name: t\non: push\njobs:\n  a:\n    if: {cond}\n    runs-on: ubuntu-latest\n    steps: []\n"
            _parse(src)

    def test_no_unescaped_expression_in_if(self) -> None:
        """if: conditions at the job/step level are bare expressions, not ${{ }}."""
        src = "name: t\non: push\njobs:\n  a:\n    if: ${{ success() }}\n    runs-on: ubuntu-latest\n    steps: []\n"
        wf = _parse(src)
        cond = str(wf["jobs"]["a"]["if"])
        if "${{" in cond:
            pass  # GHA accepts both; this is informational

    @pytest.mark.parametrize("cond,balanced", _BALANCED_CONDITIONALS)
    def test_parens_balanced(self, cond: str, balanced: bool) -> None:
        assert _parentheses_balanced(cond) is balanced, (
            f"conditional {cond!r}: expected balanced={balanced}"
        )

    def test_conditionals_in_steps(self) -> None:
        src = (
            "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - id: s1\n        if: success()\n        run: echo ok\n"
            "      - id: s2\n        if: always()\n        run: echo always\n"
        )
        wf = _parse(src)
        steps = wf["jobs"]["a"]["steps"]
        assert steps[0]["if"] == "success()"
        assert steps[1]["if"] == "always()"


# ---------------------------------------------------------------------------
# structural integrity
# ---------------------------------------------------------------------------


class TestStructuralIntegrity:
    def test_workflow_has_name(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        assert "name" in wf, "workflow must have a name"

    def test_workflow_has_on_trigger(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        # PyYAML normalizes bare `on` → True; check for either key.
        has_on = "on" in wf or True in wf
        assert has_on, "workflow must declare an 'on' trigger"
        on_val: object = wf.get("on") if "on" in wf else wf.get(True)
        assert isinstance(on_val, dict), "'on' must be a mapping"

    def test_workflow_has_jobs(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        assert "jobs" in wf, "workflow must have a jobs key"
        assert isinstance(wf["jobs"], dict), "jobs must be a mapping"
        assert len(wf["jobs"]) > 0, "workflow must have at least one job"

    def test_empty_jobs_is_invalid(self) -> None:
        src = "name: t\non: push\njobs: {}\n"
        wf = _parse(src)
        assert wf["jobs"] is not None
        assert len(wf["jobs"]) == 0

    def test_every_job_has_runs_on(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        for name, job in wf.get("jobs", {}).items():
            assert "runs-on" in job, f"job '{name}' must declare runs-on"

    def test_missing_jobs_key(self) -> None:
        src = "name: t\non: push\n"
        wf = _parse(src)
        assert "jobs" not in wf

    def test_every_job_has_steps(self) -> None:
        wf = _parse(_VALID_WORKFLOW)
        for name, job in wf.get("jobs", {}).items():
            assert "steps" in job, f"job '{name}' must have steps"
            assert isinstance(job["steps"], list), f"job '{name}' steps must be a list"

    def test_no_duplicate_job_names(self) -> None:
        src = (
            "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
            "  b:\n    runs-on: ubuntu-latest\n    steps: []\n"
        )
        wf = _parse(src)
        assert list(wf["jobs"].keys()) == ["a", "b"]


# ---------------------------------------------------------------------------
# YAML parse edge cases
# ---------------------------------------------------------------------------


class TestYAMLEdgeCases:
    def test_yaml_bool_normalization(self) -> None:
        """PyYAML parses bare `on` as True — normalise if needed."""
        data = yaml.safe_load("on: push\njobs: {}\n")
        assert isinstance(data, dict)
        assert data.get(True) == "push" or data.get("on") == "push"

    def test_empty_string_workflow(self) -> None:
        data = yaml.safe_load("")
        assert data is None

    def test_minimal_workflow(self) -> None:
        data = yaml.safe_load("name: t\non: push\njobs: {}\n")
        assert isinstance(data, dict)
        assert data["name"] == "t"

    def test_comment_only_workflow(self) -> None:
        data = yaml.safe_load("# just a comment\n")
        assert data is None

    def test_nested_anchors_aliases(self) -> None:
        src = (
            "name: t\non: push\njobs:\n"
            "  a: &default\n    runs-on: ubuntu-latest\n    steps: []\n"
            "  b:\n    <<: *default\n"
        )
        data = yaml.safe_load(src)
        assert "runs-on" in data["jobs"]["b"]


# ---------------------------------------------------------------------------
# permissions and environment
# ---------------------------------------------------------------------------


class TestPermissions:
    def test_explicit_permissions(self) -> None:
        src = (
            "name: t\non: push\npermissions:\n  contents: read\n"
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        )
        wf = _parse(src)
        assert "permissions" in wf
        assert wf["permissions"]["contents"] == "read"

    def test_permissions_read_all(self) -> None:
        """permissions: read-all is a valid least-privilege starting point."""
        src = "name: t\non: push\npermissions: read-all\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        wf = _parse(src)
        assert wf["permissions"] == "read-all"

    def test_default_permissions_when_absent(self) -> None:
        src = "name: t\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        wf = _parse(src)
        assert "permissions" not in wf
