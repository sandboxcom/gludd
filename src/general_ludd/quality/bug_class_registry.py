"""Bug-class registry — turn every found error into a system-wide sweep + guard.

Every bug discovered in a remediation session is a *symptom* of a *class* of
bug. Fixing the one line you tripped over is a point fix; the next instance of
the same class is still live. This registry makes that impossible to forget:

* A :class:`BugClass` pairs a **detector** (a compiled regex over a line, or a
  predicate over ``(path, text)``) with a **guard_test_id** — the pytest node
  id of the regression test that prevents the class from coming back.
* :func:`sweep` walks the whole ``src/general_ludd`` tree and returns *every*
  occurrence of *every* class, so a fix is verified system-wide rather than at
  the single site that surfaced the bug.
* :func:`verify_guards` reports the classes whose ``guard_test_id`` is not in
  the set of currently-collected test ids — i.e. a bug class with **no active
  regression test**, a prevention gap that must be closed.

Self-flagging guard
-------------------
This file necessarily contains the literal detector patterns (``shell=True``,
etc.) as *data* inside the seed definitions below. A naive sweep would flag the
registry's own seeds as occurrences, drowning real findings. Every seed line
that embeds a pattern carries the sentinel :data:`SEED_MARKER` in a trailing
comment, and :func:`sweep` skips any line bearing that marker. The
``test_registry_does_not_self_flag_its_own_definitions`` test pins this.

The scanning implementation lives in a small sibling module so seed data and
tree traversal have separate maintenance boundaries.
"""
from __future__ import annotations

import re

from general_ludd.quality.bug_class_registry_core import (
    SEED_MARKER,
    SWEEP_ROOT,
    BugClass,
    Occurrence,
    sweep,
    verify_guards,
)
from general_ludd.quality.bug_class_registry_core import (
    detect_ssrf_unvalidated_url as _detect_ssrf_unvalidated_url,
)
from general_ludd.quality.bug_class_registry_core import (
    detect_unvalidated_subprocess_argv as _detect_unvalidated_subprocess_argv,
)

# ---------------------------------------------------------------------------
# Seed definitions. Every line embedding a literal detector pattern carries the
# SEED_MARKER sentinel comment so the sweep skips it (no self-flagging). The
# guard_test_id points at the regression test that already guards the class
# where one exists; an empty string marks a known prevention gap.
# ---------------------------------------------------------------------------

# Pre-compiled to keep the seed table line-length under the linter limit.
_COST_CAP_FAIL_OPEN = re.compile(
    r"(budget|cost|cap).*fail.*open|return\s+True\s*#.*(spend|budget)",  # bug-class-seed:exclude
)

DEFAULT_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="shell_true",
        description=(
            "subprocess invoked with shell=True / create_subprocess_shell — a "
            "shell-injection sink. Use a list argv with shell disabled."
        ),
        # The trailing sentinel keeps this seed line out of its own sweep.
        detector=re.compile(r"shell\s*=\s*True|create_subprocess_shell"),  # bug-class-seed:exclude
        guard_test_id="tests/unit/test_guardrails.py::test_no_shell_true_in_src",
        remediation=(
            "Replace with a list-form argv (shell disabled). Sweep ALL hits — "
            "every shell=True is the same class."
        ),
    ),
    BugClass(
        id="unvalidated_subprocess_argv",
        description=(
            "User-controlled value interpolated (f-string or +) into a "
            "subprocess argv without validation — argv injection."
        ),
        detector=_detect_unvalidated_subprocess_argv,
        guard_test_id="tests/unit/test_guardrails.py::test_no_unvalidated_subprocess_argv",
        remediation=(
            "Pass values as discrete argv elements after a '--' end-of-options "
            "guard; validate/allowlist any user-derived token."
        ),
    ),
    BugClass(
        id="fail_open_auth",
        description=(
            "Auth check that defaults to allow on missing/invalid credentials "
            "(fail-open) instead of denying."
        ),
        detector=re.compile(r"return\s+True\s*#.*(auth|allow)|auth.*fail.*open"),  # bug-class-seed:exclude
        guard_test_id="tests/unit/test_guardrails.py::test_auth_fails_closed",
        remediation=(
            "Default-DENY: any missing/invalid credential path must return a "
            "denial, never allow. Add a fail-closed regression test."
        ),
    ),
    BugClass(
        id="fail_open_cost_cap",
        description=(
            "Cost/budget cap that defaults to permitting spend when the cap is "
            "unknown or the check errors (fail-open)."
        ),
        detector=_COST_CAP_FAIL_OPEN,
        guard_test_id="tests/unit/test_guardrails.py::test_cost_cap_fails_closed",
        remediation=(
            "When the cap is unknown or the check raises, DENY the spend. Clamp "
            "provider token counts >= 0 and treat errors as over-budget."
        ),
    ),
    BugClass(
        id="swallowed_commit_exception",
        description=(
            "Bare 'except ...: pass' around a git commit / DB commit — a "
            "silently swallowed failure that hides data loss."
        ),
        detector=re.compile(r"except[^:]*:\s*pass\b.*commit|commit.*except[^:]*:\s*pass"),  # bug-class-seed:exclude
        guard_test_id="tests/unit/test_guardrails.py::test_no_swallowed_commit_exception",
        remediation=(
            "Never swallow a commit failure. Log + re-raise (or roll back) so "
            "the caller sees the failure; observability invariant applies."
        ),
    ),
    BugClass(
        id="unbounded_metric_labels",
        description=(
            "Prometheus/metric label populated from unbounded user input — a "
            "cardinality explosion / memory-exhaustion vector."
        ),
        detector=re.compile(r"\.labels\([^)]*(request|user|path|url|id)[^)]*\)"),  # bug-class-seed:exclude
        guard_test_id="tests/unit/test_guardrails.py::test_metric_labels_are_bounded",
        remediation=(
            "Bound label values to a fixed allowlist / bucketed set. Never feed "
            "raw request paths, ids, or user input into a metric label."
        ),
    ),
    BugClass(
        id="ssrf_unvalidated_url",
        description=(
            "A base_url / clone url is handed to a client or subprocess without "
            "an is_safe_* guard nearby — SSRF / arbitrary-fetch."
        ),
        detector=_detect_ssrf_unvalidated_url,
        guard_test_id="tests/unit/test_guardrails.py::test_urls_validated_before_fetch",
        remediation=(
            "Run an is_safe_url/host allowlist check before any fetch/clone; "
            "reject private/link-local/metadata addresses."
        ),
    ),
    BugClass(
        id="overlay_shadow",
        description=(
            "A config/overlay layer silently shadows a base value with no "
            "precedence record — an unexplained-override bug."
        ),
        detector=re.compile(r"overlay.*update\(|\.update\(.*overlay"),  # bug-class-seed:exclude
        guard_test_id="",
        remediation=(
            "Make overlay precedence explicit and recorded: log which layer won "
            "for each key so an override is never silent. (Guard gap — add one.)"
        ),
    ),
    BugClass(
        id="non_idempotent_reconcile",
        description=(
            "A reconcile/converge path that mutates state without an "
            "idempotency guard — re-running it double-applies."
        ),
        detector=re.compile(r"def\s+reconcile|def\s+converge"),  # bug-class-seed:exclude
        guard_test_id="",
        remediation=(
            "Make reconcile idempotent: guard each mutation on a desired-vs-"
            "actual diff so a second run is a no-op. (Guard gap — add one.)"
        ),
    ),
    BugClass(
        id="adversarial_eval_on_input",
        description=(
            "eval/exec invoked on user-controlled input (request/input/data/"
            "body/params) — arbitrary code execution."
        ),
        detector=re.compile(r"(?:eval|exec)\s*\(\s*(?:request|input|data|body|params)"),  # bug-class-seed:exclude
        guard_test_id="tests/unit/test_adversarial_detector.py::TestBackdoorPatterns::test_eval_on_input_detected",
        remediation=(
            "Never eval/exec on untrusted input; use ast.literal_eval or "
            "structured parsing."
        ),
    ),
    BugClass(
        id="adversarial_shell_true",
        description=(
            "subprocess call with shell=True — command injection sink on "
            "untrusted input."
        ),
        detector=re.compile(r"subprocess\.\w+\s*\([^)]*shell\s*=\s*True"),  # bug-class-seed:exclude
        guard_test_id="tests/unit/test_adversarial_detector.py::TestBackdoorPatterns::test_shell_injection_detected",
        remediation=(
            "Use shell=False with list-based args; never shell=True on "
            "untrusted input."
        ),
    ),
    BugClass(
        id="adversarial_hardcoded_secret",
        description=(
            "Hardcoded API key / secret / password as a literal string "
            "(20+ chars of base64-ish text)."
        ),
        detector=re.compile(  # bug-class-seed:exclude
            r'(?:api_key|secret_key|password)\s*[:=]\s*["\']'
            r"[A-Za-z0-9_\-+/=]{20,}[\"']"
            r"(?!\s*(?:#|//).*seed)"
        ),
        guard_test_id="tests/unit/test_adversarial_detector.py::TestCredentialPatterns::test_hardcoded_api_key_detected",
        remediation=(
            "Use environment variables or secrets manager; never hardcode "
            "credentials."
        ),
    ),
    BugClass(
        id="adversarial_fail_open_auth",
        description=(
            "Auth check that defaults to allow after an exception — fail-open "
            "instead of fail-closed."
        ),
        detector=re.compile(r"except\s+\w+\s*:\s*(?:return\s+True|pass)\s*#\s*allow"),  # bug-class-seed:exclude
        guard_test_id="tests/unit/test_adversarial_detector.py::TestLogicDegradePatterns::test_default_allow_auth_detected",
        remediation=(
            "Auth must fail-closed: deny on exception, only allow on explicit "
            "success."
        ),
    ),
    BugClass(
        id="adversarial_obfuscated_exec",
        description=(
            "eval/exec on obfuscated payload (base64, codecs.decode, "
            "bytes.fromhex) — hidden code execution."
        ),
        detector=re.compile(r"(?:eval|exec)\s*\(\s*(?:base64|codecs\.decode|bytes\.fromhex)"),  # bug-class-seed:exclude
        guard_test_id="tests/unit/test_adversarial_detector.py::TestObfuscationPatterns::test_base64_exec_detected",
        remediation=(
            "Remove obfuscated code execution; all executed code must be "
            "auditable."
        ),
    ),
    BugClass(
        id="adversarial_typosquatted_import",
        description=(
            "Import of a typo-squatted package name (requsts, pyhtest, "
            "cryptograpy, nmpy, etc.) — dependency confusion attack."
        ),
        detector=re.compile(  # bug-class-seed:exclude
            r"\bimport\s+(?:requsts|pyhtest|cryptograpy|nmpy|panda|matplotlb|"
            r"tensorflw|pythorch|djngo|flsk|fastpi|starllete|uvcorn|pydentic|"
            r"sqlmodel|alembc|celry|pytz|datetmie|coloredlogs|rich-click)\b"
        ),
        guard_test_id="tests/unit/test_adversarial_detector.py::TestDependencyAttackPatterns::test_typosquatted_import_detected",
        remediation=(
            "Verify package name against PyPI; use hash-checking with pip."
        ),
    ),
)


__all__ = (
    "DEFAULT_BUG_CLASSES",
    "SEED_MARKER",
    "SWEEP_ROOT",
    "BugClass",
    "Occurrence",
    "sweep",
    "verify_guards",
)
