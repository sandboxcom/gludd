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

Stdlib only — no project imports — so the script CLI can load it standalone.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# A line carrying this sentinel is a registry seed definition (the pattern is
# data, not a real occurrence) and is excluded from every sweep. Keep the
# literal split so the marker string itself never trips a detector.
SEED_MARKER = "bug-class-seed" ":exclude"

# Where a sweep looks. Relative to the repo root passed to ``sweep``.
SWEEP_ROOT = Path("src") / "general_ludd"

# (path, lineno, line) for a single occurrence. lineno is 0 for whole-file
# predicate matches that aren't tied to one line.
Occurrence = tuple[str, int, str]


@dataclass(frozen=True)
class BugClass:
    """One class of bug: how to detect it and what regression test guards it.

    Attributes
    ----------
    id:
        Stable identifier (snake_case) used as the sweep result key.
    description:
        Human-readable summary of the failure mode.
    detector:
        A compiled :class:`re.Pattern` matched against each source line, OR a
        predicate ``(path, text) -> bool`` over the whole file. The predicate
        form is for heuristics that need multi-line context.
    guard_test_id:
        The pytest node id (e.g. ``tests/unit/test_x.py::test_y``) of the test
        that fails if this class recurs. Empty string means *no guard yet* — a
        prevention gap that :func:`verify_guards` will flag.
    remediation:
        Note on how to fix an occurrence (the systemic fix, not a point fix).
    """

    id: str
    description: str
    detector: re.Pattern[str] | Callable[[str, str], bool]
    guard_test_id: str
    remediation: str


# The registry's own source file: it embeds every detector pattern as data,
# so it is excluded from the sweep wholesale. Lines elsewhere can opt out via
# SEED_MARKER, but the seed file itself (including its prose docstring) must
# never appear as an occurrence.
_REGISTRY_FILENAME = Path(__file__).name


def _iter_source_files(repo_root: Path) -> list[Path]:
    """Return every ``src/general_ludd/**/*.py`` file under ``repo_root``.

    The registry's own seed file is excluded — it contains the detector
    patterns as data and prose, so including it would self-flag every class.
    Missing tree → empty list (callers may point at a temp dir without a real
    source tree). Sorted for deterministic output.
    """
    base = repo_root / SWEEP_ROOT
    if not base.is_dir():
        return []
    return sorted(
        p
        for p in base.rglob("*.py")
        if p.is_file() and p.name != _REGISTRY_FILENAME
    )


def _read_text(path: Path) -> str | None:
    """Read a source file as UTF-8 text, or None if binary/unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _scan_file(path: Path, text: str, bug_class: BugClass) -> list[Occurrence]:
    """Find occurrences of ``bug_class`` in a single file's ``text``.

    Lines carrying :data:`SEED_MARKER` are skipped so the registry never
    self-flags its own seed definitions.
    """
    detector = bug_class.detector
    occurrences: list[Occurrence] = []

    if isinstance(detector, re.Pattern):
        for lineno, line in enumerate(text.splitlines(), start=1):
            if SEED_MARKER in line:
                continue
            if detector.search(line):
                occurrences.append((str(path), lineno, line.strip()))
        return occurrences

    # Predicate detector over the whole file. Strip seed-marked lines so a
    # predicate can't match on the registry's own data either.
    cleaned_lines = [ln for ln in text.splitlines() if SEED_MARKER not in ln]
    cleaned = "\n".join(cleaned_lines)
    if detector(str(path), cleaned):
        # Report the first non-seed line as the anchor; predicate matches are
        # whole-file, so lineno 0 signals "file-level".
        anchor = cleaned_lines[0].strip() if cleaned_lines else ""
        occurrences.append((str(path), 0, anchor))
    return occurrences


def sweep(
    repo_root: str | Path,
    classes: list[BugClass],
) -> dict[str, list[Occurrence]]:
    """Walk the source tree and return ALL occurrences of every bug class.

    Parameters
    ----------
    repo_root:
        Repo root; the sweep looks under ``repo_root/src/general_ludd``.
    classes:
        Bug classes to detect.

    Returns
    -------
    dict
        ``{class_id: [(path, lineno, line), ...]}`` with an entry for *every*
        class (empty list when clean). Surfacing all instances system-wide is
        the whole point: a fix is never a point fix.
    """
    root = Path(repo_root)
    result: dict[str, list[Occurrence]] = {c.id: [] for c in classes}

    files = _iter_source_files(root)
    for path in files:
        text = _read_text(path)
        if text is None:
            continue
        for bug_class in classes:
            result[bug_class.id].extend(_scan_file(path, text, bug_class))
    return result


def verify_guards(
    classes: list[BugClass],
    known_test_ids: set[str],
) -> list[BugClass]:
    """Return classes whose ``guard_test_id`` is NOT in ``known_test_ids``.

    A bug class with no active regression test is a prevention gap: nothing
    fails if the class recurs. An empty ``guard_test_id`` is, by definition,
    always a gap. Order preserves the input order for stable reporting.
    """
    gaps: list[BugClass] = []
    for bug_class in classes:
        if not bug_class.guard_test_id or bug_class.guard_test_id not in known_test_ids:
            gaps.append(bug_class)
    return gaps


# ---------------------------------------------------------------------------
# Heuristic predicate detectors (need multi-line / contextual reasoning).
# These are module-level functions (not lambdas) so they're importable and
# typecheck cleanly. They do NOT carry SEED_MARKER because they are logic, not
# pattern data — the patterns they reference are built at runtime from chars.
# ---------------------------------------------------------------------------

# Tokens that introduce a subprocess argv list.
_SUBPROCESS_TOKENS = (
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "create_subprocess_exec",
)


def _detect_unvalidated_subprocess_argv(path: str, text: str) -> bool:
    """Heuristic: an f-string or ``+`` concatenation inside a subprocess argv.

    Flags lines that both call a subprocess primitive AND interpolate a value
    via an f-string (``f"..."``/``f'...'``) or string concatenation (``" + ``),
    which is the classic argv-injection / unvalidated-input shape.
    """
    for line in text.splitlines():
        if not any(tok in line for tok in _SUBPROCESS_TOKENS):
            continue
        has_fstring = 'f"' in line or "f'" in line
        has_concat = '" +' in line or "' +" in line or "+ '" in line or '+ "' in line
        if has_fstring or has_concat:
            return True
    return False


# Guards that, when present near a URL handed to a client/subprocess, make the
# SSRF flag a false positive.
_SSRF_SAFE_GUARDS = ("is_safe_url", "is_safe_host", "validate_url", "is_allowed_url")
_SSRF_URL_TOKENS = ("base_url", "clone_url", "clone(", "git clone")
_SSRF_SINK_TOKENS = ("subprocess", "create_subprocess", "httpx", "requests", "client(")


def _detect_ssrf_unvalidated_url(path: str, text: str) -> bool:
    """Heuristic: a base_url / clone url reaches a client/subprocess sink with
    no ``is_safe_*`` guard anywhere in the same file.
    """
    if any(guard in text for guard in _SSRF_SAFE_GUARDS):
        return False
    has_url = any(tok in text for tok in _SSRF_URL_TOKENS)
    has_sink = any(tok in text for tok in _SSRF_SINK_TOKENS)
    return has_url and has_sink


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
