"""NL -> (subsystem, change-kind, target files) classifier (issue #81).

Turns a natural-language ``"update gludd: <request>"`` into a
:class:`~general_ludd.self_update.model.SelfUpdatePlan` skeleton. The routing is
keyword/heuristic — deterministic, auditable, and offline. An LLM-assisted hook
(:func:`llm_route`) is provided as a stub so a richer classifier can be wired in
later without changing the apply ladder's contract.

Design posture (default-DENY-ish):
  * Subsystem is decided by the highest-scoring keyword group; ties + zero
    matches fall to :data:`Subsystem.UNKNOWN` so an unroutable request never
    lands somewhere plausible-but-wrong.
  * Change-kind defaults to the *safest* applicable tier: a request that merely
    tweaks a value routes to ``VALUE_EDIT`` (config tier); only explicit
    new-thing language routes to ``SCAFFOLD``; only explicit code/logic language
    routes to ``CODE_CHANGE``.
  * Confidence reflects keyword-match strength and biases the apply ladder
    toward requiring approval when low.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from general_ludd.self_update.model import (
    ApplyTier,
    ChangeKind,
    SelfUpdatePlan,
    SelfUpdateRequest,
    Subsystem,
)

#: ``update gludd:`` (or ``update gludd -``) prefix the intake strips.
_PREFIX_RE = re.compile(r"^\s*update\s+gludd\s*[:\-]\s*", re.IGNORECASE)

#: Per-subsystem keyword groups. A request scores a subsystem by how many of its
#: keywords appear; the top scorer wins. Keywords are matched on word boundaries
#: so "role" does not match "control".
_SUBSYSTEM_KEYWORDS: dict[Subsystem, tuple[str, ...]] = {
    Subsystem.ROLES: ("role", "roles", "agent", "agents", "persona"),
    Subsystem.CONNECTORS: (
        "connector",
        "connectors",
        "integration",
        "webhook",
        "slack",
        "github",
        "jira",
    ),
    Subsystem.PROFILES: (
        "profile",
        "profiles",
        "model profile",
        "provider",
        "openrouter",
        "anthropic",
        "deepseek",
    ),
    Subsystem.PROMPTS: (
        "prompt",
        "prompts",
        "template",
        "templates",
        "jinja",
        "wording",
    ),
    Subsystem.SCORING: ("scoring", "score", "priority", "rank", "weighting", "heuristic"),
    Subsystem.GATEWAY: ("gateway", "routing", "model routing", "fallback", "llm route"),
    Subsystem.SPEND: ("spend", "budget", "cost", "billing", "quota", "cap"),
    Subsystem.OBSERVABILITY: (
        "observability",
        "metric",
        "metrics",
        "logging",
        "log level",
        "telemetry",
        "trace",
        "heartbeat",
    ),
    Subsystem.SCHEDULING: ("schedule", "scheduler", "concurrency", "batch", "parallelism"),
    Subsystem.SECURITY: (
        "security",
        "guardrail",
        "guardrails",
        "permission",
        "permissions",
        "policy",
        "capability",
        "auth",
    ),
    # CONFIG is the catch-all for generic value/setting/timeout/yaml language.
    Subsystem.CONFIG: ("config", "setting", "settings", "yaml", "yml", "timeout", "limit", "value", "variable"),
}

#: Language that signals a brand-new thing is being added (-> SCAFFOLD).
_SCAFFOLD_VERBS: tuple[str, ...] = (
    "add a new",
    "add new",
    "create a new",
    "create new",
    "scaffold",
    "new role",
    "new connector",
    "new profile",
    "new template",
    "introduce",
    "register a",
)

#: Language that signals real Python/logic must change (-> CODE_CHANGE).
_CODE_VERBS: tuple[str, ...] = (
    "rewrite",
    "refactor",
    "fix the bug",
    "fix bug",
    "implement",
    "change the logic",
    "change the algorithm",
    "the function",
    "the method",
    "the class",
    "patch the code",
    "code path",
)

#: Language that signals a simple value/setting tweak (-> VALUE_EDIT). Used to
#: bump confidence, not to override an explicit scaffold/code signal.
_VALUE_VERBS: tuple[str, ...] = (
    "set ",
    "change the",
    "increase",
    "decrease",
    "raise the",
    "lower the",
    "bump",
    "tune",
    "adjust",
    "update the",
    "enable",
    "disable",
    "turn on",
    "turn off",
)

#: Best-guess target-file globs per (subsystem, change-kind). These are
#: *suggestions* the apply ladder refines; an empty tuple means "the classifier
#: could not pin a concrete file" and the apply ladder must not auto-apply.
_CONFIG_FILE_HINTS: dict[Subsystem, tuple[str, ...]] = {
    Subsystem.PROFILES: ("config/model_profiles/",),
    Subsystem.GATEWAY: ("config/model_routing.yml",),
    Subsystem.SPEND: ("config/general-ludd.yml",),
    Subsystem.SCORING: ("config/general-ludd.yml",),
    Subsystem.OBSERVABILITY: ("config/general-ludd.yml",),
    Subsystem.SCHEDULING: ("config/general-ludd.yml",),
    Subsystem.PROMPTS: ("templates/prompts/",),
    Subsystem.CONFIG: ("config/general-ludd.yml",),
    Subsystem.ROLES: ("config/general-ludd.yml",),
    Subsystem.CONNECTORS: ("config/general-ludd.yml",),
    Subsystem.SECURITY: ("config/general-ludd.yml",),
}


def strip_prefix(text: str) -> str:
    """Strip a leading ``update gludd:`` / ``update gludd -`` marker if present."""
    return _PREFIX_RE.sub("", text or "").strip()


def _count_keyword_hits(text: str, keywords: Iterable[str]) -> int:
    hits = 0
    for kw in keywords:
        if " " in kw:
            if kw in text:
                hits += 1
        elif re.search(rf"\b{re.escape(kw)}\b", text):
            hits += 1
    return hits


def _any_phrase(text: str, phrases: Iterable[str]) -> bool:
    return any(p in text for p in phrases)


def classify_subsystem(text: str) -> tuple[Subsystem, int]:
    """Return ``(subsystem, score)`` for the best-matching subsystem.

    SECURITY and the specific subsystems are scored ahead of the CONFIG
    catch-all: when a specific group and CONFIG tie on raw hits, the specific
    one wins (CONFIG is the residual sink, never a preferred destination).
    Zero hits everywhere -> ``(UNKNOWN, 0)``.
    """
    norm = text.lower()
    best: Subsystem = Subsystem.UNKNOWN
    best_score = 0
    for subsystem, keywords in _SUBSYSTEM_KEYWORDS.items():
        score = _count_keyword_hits(norm, keywords)
        if score == 0:
            continue
        # Prefer a specific subsystem over the CONFIG catch-all on a tie.
        if score > best_score or (
            score == best_score
            and best is Subsystem.CONFIG
            and subsystem is not Subsystem.CONFIG
        ):
            best = subsystem
            best_score = score
    return best, best_score


def classify_change_kind(text: str) -> ChangeKind:
    """Decide the change kind from explicit verbs (safest-applicable default).

    Precedence: explicit CODE language > explicit SCAFFOLD language > otherwise
    VALUE_EDIT. Empty/garbage text -> UNKNOWN.
    """
    norm = text.lower()
    if not norm.strip():
        return ChangeKind.UNKNOWN
    if _any_phrase(norm, _CODE_VERBS):
        return ChangeKind.CODE_CHANGE
    if _any_phrase(norm, _SCAFFOLD_VERBS):
        return ChangeKind.SCAFFOLD
    # A value verb, a known subsystem, or generic text all default to the
    # safest tier: a config/value edit.
    return ChangeKind.VALUE_EDIT


def _tier_for_kind(kind: ChangeKind) -> ApplyTier:
    return {
        ChangeKind.VALUE_EDIT: ApplyTier.CONFIG,
        ChangeKind.SCAFFOLD: ApplyTier.SCAFFOLD,
        ChangeKind.CODE_CHANGE: ApplyTier.CODE,
        ChangeKind.UNKNOWN: ApplyTier.CODE,  # unknown => most cautious tier
    }[kind]


def _target_files(subsystem: Subsystem, kind: ChangeKind) -> tuple[str, ...]:
    if kind is ChangeKind.UNKNOWN:
        return ()
    if kind in (ChangeKind.VALUE_EDIT, ChangeKind.SCAFFOLD):
        return _CONFIG_FILE_HINTS.get(subsystem, ())
    # CODE_CHANGE: we cannot safely guess a Python file from keywords alone;
    # leave empty so the apply ladder requires an operator-supplied target.
    return ()


def _confidence(subsystem: Subsystem, subsystem_score: int, kind: ChangeKind, text: str) -> float:
    """Heuristic confidence in [0, 1]: keyword strength + verb agreement."""
    if subsystem is Subsystem.UNKNOWN or kind is ChangeKind.UNKNOWN:
        return 0.0
    base = min(0.6, 0.3 + 0.15 * subsystem_score)
    norm = text.lower()
    if kind is ChangeKind.VALUE_EDIT and _any_phrase(norm, _VALUE_VERBS):
        base += 0.25
    if kind is ChangeKind.SCAFFOLD and _any_phrase(norm, _SCAFFOLD_VERBS):
        base += 0.25
    if kind is ChangeKind.CODE_CHANGE and _any_phrase(norm, _CODE_VERBS):
        base += 0.15
    return round(min(base, 1.0), 3)


def classify(request: SelfUpdateRequest) -> SelfUpdatePlan:
    """Route a request to a :class:`SelfUpdatePlan` skeleton.

    Pure + deterministic: same text always yields the same plan. The
    ``apply_tier`` here is the *classifier's* proposed tier; the apply ladder
    (:mod:`.apply`) is the authority that may downgrade it to ``REFUSED`` after
    consulting the capability lattice.
    """
    text = strip_prefix(request.raw_text)
    subsystem, score = classify_subsystem(text)
    kind = classify_change_kind(text)
    tier = _tier_for_kind(kind)
    files = _target_files(subsystem, kind)
    conf = _confidence(subsystem, score, kind, text)

    rationale = (
        f"routed to subsystem={subsystem.value} (kw-score={score}), "
        f"change_kind={kind.value}, proposed_tier={tier.value}"
    )
    # A code-tier or unknown change always proposes approval; config/scaffold
    # are decided downstream by the apply ladder (path guards may force it).
    requires_approval = tier in (ApplyTier.CODE,) or kind is ChangeKind.UNKNOWN

    return SelfUpdatePlan(
        subsystem=subsystem,
        change_kind=kind,
        target_files=files,
        apply_tier=tier,
        requires_approval=requires_approval,
        rationale=rationale,
        confidence=conf,
    )


def llm_route(request: SelfUpdateRequest) -> SelfUpdatePlan | None:
    """LLM-assisted routing hook (STUB).

    Intentionally returns ``None`` so the deterministic keyword classifier is
    authoritative today. A future implementation would call the model gateway
    with a structured prompt and return a higher-confidence
    :class:`SelfUpdatePlan`; callers fall back to :func:`classify` on ``None``.
    """
    return None
