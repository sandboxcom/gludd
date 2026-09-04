"""Project-owned path policy for keeping business logic out of self-improvement.

The policy intentionally operates on whole repository-relative paths.  Symbol-level
exceptions are unsafe because a model shown the containing file can infer or repeat
the supposedly private symbol.  Invalid policy data raises instead of degrading to
public access, allowing every caller to disable self-improvement fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Final, Protocol, cast

from pathspec import GitIgnoreSpec

SELF_IMPROVE_POLICY_PATH: Final = ".gludd/self-improve-policy.json"
MAX_POLICY_BYTES: Final = 65_536
MAX_POLICY_RULES: Final = 512
MAX_POLICY_PATTERN_LENGTH: Final = 1_024
MAX_REPOSITORY_PATH_LENGTH: Final = 4_096

_SCHEMA_VERSION: Final = 1
_POLICY_KEYS: Final = frozenset(
    {"schema_version", "default_access", "private_paths", "public_paths"}
)
_WINDOWS_ABSOLUTE_RE: Final = re.compile(r"^[A-Za-z]:/")


class PolicyAccess(StrEnum):
    """Whether self-improvement may observe and operate on a repository path."""

    PUBLIC = "public"
    PRIVATE = "private"


class SelfImprovePolicyError(ValueError):
    """Raised when policy data or a candidate path is unsafe or ambiguous."""


class _DuplicateObjectKeyError(ValueError):
    """Internal signal used by the strict JSON object decoder."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateObjectKeyError
        result[key] = value
    return result


def _validate_segments(value: str, *, rule: bool) -> None:
    label = "path rule" if rule else "repository path"
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SelfImprovePolicyError(f"invalid {label}") from exc
    if (
        not value
        or value != value.strip()
        or encoded_length > MAX_REPOSITORY_PATH_LENGTH
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or _WINDOWS_ABSOLUTE_RE.match(value) is not None
        or value.startswith("./")
        or "//" in value
    ):
        raise SelfImprovePolicyError(f"invalid {label}")

    candidate = value[:-1] if rule and value.endswith("/") else value
    if not candidate or any(part in {"", ".", ".."} for part in candidate.split("/")):
        raise SelfImprovePolicyError(f"invalid {label}")
    if not rule and value.endswith("/"):
        raise SelfImprovePolicyError(f"invalid {label}")


def _validate_rule(value: object) -> str:
    if not isinstance(value, str) or value.startswith(("!", "#")):
        raise SelfImprovePolicyError("invalid path rule")
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SelfImprovePolicyError("invalid path rule") from exc
    if encoded_length > MAX_POLICY_PATTERN_LENGTH:
        raise SelfImprovePolicyError("invalid path rule")
    _validate_segments(value, rule=True)
    return value


def _validated_rules(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SelfImprovePolicyError(f"{field_name} must be an array")
    rules = tuple(_validate_rule(item) for item in value)
    if len(set(rules)) != len(rules):
        raise SelfImprovePolicyError(f"duplicate {field_name} rule")
    return tuple(sorted(rules))


def _canonical_json(
    *,
    default_access: PolicyAccess,
    private_paths: tuple[str, ...],
    public_paths: tuple[str, ...],
) -> str:
    return json.dumps(
        {
            "default_access": default_access.value,
            "private_paths": private_paths,
            "public_paths": public_paths,
            "schema_version": _SCHEMA_VERSION,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, slots=True)
class SelfImprovePrivacyPolicy:
    """Canonical immutable policy with private-wins Git-ignore path matching."""

    default_access: PolicyAccess
    private_paths: tuple[str, ...]
    public_paths: tuple[str, ...]
    canonical_json: str
    digest: str
    _private_spec: GitIgnoreSpec = field(repr=False, compare=False)
    _public_spec: GitIgnoreSpec = field(repr=False, compare=False)

    def access_for(self, repository_path: str | PurePosixPath) -> PolicyAccess:
        """Classify one validated repository-relative POSIX path.

        The policy document itself is always private.  Explicit private matches
        then take precedence over public matches, independent of rule order.
        """
        if isinstance(repository_path, PurePosixPath):
            normalized = repository_path.as_posix()
        elif isinstance(repository_path, str):
            normalized = repository_path
        else:
            raise SelfImprovePolicyError("invalid repository path")
        _validate_segments(normalized, rule=False)

        if normalized == SELF_IMPROVE_POLICY_PATH or normalized.startswith(
            f"{SELF_IMPROVE_POLICY_PATH}/"
        ):
            return PolicyAccess.PRIVATE
        if self._private_spec.match_file(normalized):
            return PolicyAccess.PRIVATE
        if self._public_spec.match_file(normalized):
            return PolicyAccess.PUBLIC
        return self.default_access

    def is_private(self, repository_path: str | PurePosixPath) -> bool:
        """Return whether ``repository_path`` must stay outside self-improvement."""
        return self.access_for(repository_path) is PolicyAccess.PRIVATE


def parse_self_improve_policy(raw: str | bytes) -> SelfImprovePrivacyPolicy:
    """Parse bounded strict JSON into a canonical private-path policy.

    Raises:
        SelfImprovePolicyError: if the input is malformed, unbounded, ambiguous,
            or does not implement the exact supported schema.
    """
    if isinstance(raw, bytes):
        encoded = raw
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SelfImprovePolicyError("policy must be valid UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
        try:
            encoded = raw.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SelfImprovePolicyError("policy must be valid UTF-8") from exc
    else:
        raise SelfImprovePolicyError("policy must be UTF-8 JSON")
    if len(encoded) > MAX_POLICY_BYTES:
        raise SelfImprovePolicyError("policy exceeds maximum size")

    try:
        payload = json.loads(text, object_pairs_hook=_strict_object)
    except _DuplicateObjectKeyError as exc:
        raise SelfImprovePolicyError("policy contains a duplicate object key") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise SelfImprovePolicyError("policy must be valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _POLICY_KEYS:
        raise SelfImprovePolicyError("policy must use the exact supported schema")
    if (
        isinstance(payload["schema_version"], bool)
        or payload["schema_version"] != _SCHEMA_VERSION
    ):
        raise SelfImprovePolicyError("unsupported policy schema version")
    try:
        default_access = PolicyAccess(payload["default_access"])
    except (TypeError, ValueError) as exc:
        raise SelfImprovePolicyError("default_access must be public or private") from exc

    private_paths = _validated_rules(
        payload["private_paths"], field_name="private_paths"
    )
    public_paths = _validated_rules(payload["public_paths"], field_name="public_paths")
    if len(private_paths) + len(public_paths) > MAX_POLICY_RULES:
        raise SelfImprovePolicyError("policy exceeds maximum rule count")

    canonical = _canonical_json(
        default_access=default_access,
        private_paths=private_paths,
        public_paths=public_paths,
    )
    try:
        private_spec = GitIgnoreSpec.from_lines(private_paths)
        public_spec = GitIgnoreSpec.from_lines(public_paths)
    except (TypeError, ValueError) as exc:
        raise SelfImprovePolicyError("policy contains an invalid path rule") from exc
    return SelfImprovePrivacyPolicy(
        default_access=default_access,
        private_paths=private_paths,
        public_paths=public_paths,
        canonical_json=canonical,
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        _private_spec=private_spec,
        _public_spec=public_spec,
    )


def _public_default_policy() -> SelfImprovePrivacyPolicy:
    return parse_self_improve_policy(
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":[],"public_paths":[]}'
    )


def load_self_improve_policy(repository_root: Path) -> SelfImprovePrivacyPolicy:
    """Load the fixed project policy without following a policy-file symlink.

    A genuinely absent policy preserves backward compatibility with public-by-
    default behavior.  Every present-but-invalid or unreadable policy raises so
    the caller can disable self-improvement rather than leak project data.
    """
    policy_directory = repository_root / ".gludd"
    try:
        directory_stat = policy_directory.lstat()
    except FileNotFoundError:
        return _public_default_policy()
    except OSError as exc:
        raise SelfImprovePolicyError("policy directory cannot be inspected safely") from exc
    if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(
        directory_stat.st_mode
    ):
        raise SelfImprovePolicyError("policy directory must be a non-symlink directory")

    policy_path = policy_directory / "self-improve-policy.json"
    try:
        policy_stat = policy_path.lstat()
    except FileNotFoundError:
        return _public_default_policy()
    except OSError as exc:
        raise SelfImprovePolicyError("policy cannot be inspected safely") from exc
    if stat.S_ISLNK(policy_stat.st_mode) or not stat.S_ISREG(policy_stat.st_mode):
        raise SelfImprovePolicyError("policy must be a regular non-symlink file")
    if policy_stat.st_size > MAX_POLICY_BYTES:
        raise SelfImprovePolicyError("policy exceeds maximum size")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(policy_path, flags)
    except OSError as exc:
        raise SelfImprovePolicyError("policy cannot be opened safely") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise SelfImprovePolicyError("policy must be a regular non-symlink file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_POLICY_BYTES + 1)
    except OSError as exc:
        raise SelfImprovePolicyError("policy cannot be read safely") from exc
    finally:
        os.close(descriptor)
    return parse_self_improve_policy(raw)


_DIGEST_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_UNAVAILABLE_POLICY_DIGEST: Final = hashlib.sha256(
    b"self-improve-policy-unavailable-v1"
).hexdigest()


def _safe_policy_digest(value: object) -> str:
    if isinstance(value, str) and _DIGEST_RE.fullmatch(value):
        return value
    return _UNAVAILABLE_POLICY_DIGEST


@dataclass(frozen=True, slots=True)
class SelfImprovePolicyDecision:
    """One source-free policy decision suitable for events and enforcement."""

    policy: SelfImprovePrivacyPolicy | None
    policy_digest: str
    allowed_count: int
    blocked_hashes: tuple[str, ...]
    reason: str

    @property
    def allowed(self) -> bool:
        """Return whether the complete scope is public and stable."""
        return self.policy is not None and self.reason == "matched"


def is_empty_public_policy(policy: SelfImprovePrivacyPolicy) -> bool:
    """Return whether a policy preserves the historical public behavior."""
    return (
        policy.default_access is PolicyAccess.PUBLIC
        and not policy.private_paths
        and not policy.public_paths
    )


def _path_sha256(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8", errors="surrogatepass")).hexdigest()


def _path_is_public(
    repository_root: Path,
    path: str,
    policy: SelfImprovePrivacyPolicy,
) -> bool:
    if policy.access_for(path) is PolicyAccess.PRIVATE:
        return False
    try:
        canonical_root = repository_root.resolve(strict=True)
        lexical = canonical_root / path
        resolved = lexical.resolve(strict=False)
        if not resolved.is_relative_to(canonical_root) or resolved != lexical:
            return False
        relative = resolved.relative_to(canonical_root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return False
    return policy.access_for(relative) is PolicyAccess.PUBLIC


def decide_self_improve_policy(
    policy_root: Path,
    path_root: Path,
    paths: tuple[str, ...],
    *,
    expected_digest: str | None,
) -> SelfImprovePolicyDecision:
    """Load and classify one complete effect scope without exposing raw inputs."""
    safe_expected = _safe_policy_digest(expected_digest)
    try:
        if not policy_root.resolve(strict=True).is_dir():
            raise OSError
        policy = load_self_improve_policy(policy_root)
    except Exception:
        return SelfImprovePolicyDecision(
            None, safe_expected, 0, (), "policy_unavailable"
        )
    if expected_digest is not None:
        legacy_public = expected_digest == "" and is_empty_public_policy(policy)
        digest_matches = bool(
            _DIGEST_RE.fullmatch(expected_digest)
            and hmac.compare_digest(expected_digest, policy.digest)
        )
        if not legacy_public and not digest_matches:
            return SelfImprovePolicyDecision(
                None, safe_expected, 0, (), "policy_drift"
            )
    allowed = 0
    blocked: list[str] = []
    for path in sorted(set(paths)):
        try:
            public = _path_is_public(path_root, path, policy)
        except Exception:
            public = False
        if public:
            allowed += 1
        else:
            blocked.append(_path_sha256(path))
    if blocked:
        return SelfImprovePolicyDecision(
            None, policy.digest, allowed, tuple(blocked), "private_path"
        )
    return SelfImprovePolicyDecision(policy, policy.digest, allowed, (), "matched")


def emit_self_improve_policy_event(
    sink: Callable[[str], None],
    event: str,
    decision: SelfImprovePolicyDecision,
) -> None:
    """Publish only reason codes, counts, digests, and one-way path identities."""
    sink(
        f"{event} policy_digest={decision.policy_digest} "
        f"allowed_count={decision.allowed_count} "
        f"blocked_count={len(decision.blocked_hashes)} "
        f"reason={decision.reason} "
        f"path_hashes={json.dumps(decision.blocked_hashes)}"
    )


@dataclass(frozen=True, slots=True)
class SelfImproveRuntimePolicyGuard:
    """Reusable loader and fail-closed effect-boundary policy guard."""

    policy_root: Path
    expected_digest: str
    progress_sink: Callable[[str], None] = field(repr=False, compare=False)
    violation_factory: Callable[[], Exception] = field(repr=False, compare=False)

    @classmethod
    def load(
        cls,
        policy_root: Path,
        progress_sink: Callable[[str], None],
        violation_factory: Callable[[], Exception],
    ) -> SelfImproveRuntimePolicyGuard:
        """Load the initial policy before any repository observation."""
        decision = decide_self_improve_policy(
            policy_root, policy_root, (), expected_digest=None
        )
        if not decision.allowed:
            emit_self_improve_policy_event(
                progress_sink, "SELF_IMPROVE_POLICY_BLOCKED", decision
            )
            raise violation_factory()
        return cls(
            policy_root,
            decision.policy_digest,
            progress_sink,
            violation_factory,
        )

    @classmethod
    def bound(
        cls,
        policy_root: Path,
        expected_digest: str,
        progress_sink: Callable[[str], None],
        violation_factory: Callable[[], Exception],
    ) -> SelfImproveRuntimePolicyGuard:
        """Create a guard for an existing approval-bound policy digest."""
        return cls(
            policy_root,
            expected_digest,
            progress_sink,
            violation_factory,
        )

    def decision(
        self,
        paths: tuple[str, ...],
        *,
        path_root: Path | None = None,
    ) -> SelfImprovePolicyDecision:
        """Re-load and classify paths against this guard's approved digest."""
        return decide_self_improve_policy(
            self.policy_root,
            path_root or self.policy_root,
            paths,
            expected_digest=self.expected_digest,
        )

    def require(
        self,
        paths: tuple[str, ...],
        *,
        path_root: Path | None = None,
        emit_loaded: bool = False,
    ) -> SelfImprovePrivacyPolicy:
        """Require a stable public scope and optionally publish its load event."""
        decision = self.decision(paths, path_root=path_root)
        if not decision.allowed:
            emit_self_improve_policy_event(
                self.progress_sink,
                "SELF_IMPROVE_POLICY_BLOCKED",
                decision,
            )
            raise self.violation_factory()
        if emit_loaded:
            emit_self_improve_policy_event(
                self.progress_sink,
                "SELF_IMPROVE_POLICY_LOADED",
                decision,
            )
        return cast(SelfImprovePrivacyPolicy, decision.policy)

    def require_learning(
        self,
        task_text: str,
        expected_task_digest: str,
        paths: tuple[str, ...],
    ) -> None:
        """Require public learning input and emit a distinct skipped event."""
        actual_task_digest = hashlib.sha256(
            task_text.encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        if not hmac.compare_digest(actual_task_digest, expected_task_digest):
            decision = SelfImprovePolicyDecision(
                None,
                _safe_policy_digest(self.expected_digest),
                0,
                (),
                "task_drift",
            )
        else:
            decision = self.decision(paths)
        if decision.allowed:
            return
        emit_self_improve_policy_event(
            self.progress_sink,
            "SELF_IMPROVE_LEARNING_SKIPPED",
            decision,
        )
        emit_self_improve_policy_event(
            self.progress_sink,
            "SELF_IMPROVE_POLICY_BLOCKED",
            decision,
        )
        raise self.violation_factory()
@dataclass(frozen=True, slots=True)
class SelfImproveLearningScope:
    """One-way namespace binding durable learning to a protected project."""

    digest: str
    isolated: bool

    @classmethod
    def build(
        cls,
        *,
        project_id: str,
        repository_identity: str,
        baseline_sha: str,
        policy: SelfImprovePrivacyPolicy,
    ) -> SelfImproveLearningScope:
        """Derive a scope without persisting a project identifier or path."""
        payload = {
            "baseline_sha": baseline_sha,
            "policy_digest": policy.digest,
            "project_identity": hashlib.sha256(
                project_id.encode("utf-8", errors="surrogatepass")
            ).hexdigest(),
            "protocol": "self-improve-runtime-learning-scope-v1",
            "repository_identity": repository_identity,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return cls(
            hashlib.sha256(encoded).hexdigest(),
            not is_empty_public_policy(policy),
        )

    def bind_attempt(self, attempt_identity_digest: str) -> str:
        """Bind protected-project outcomes while preserving public compatibility."""
        if _DIGEST_RE.fullmatch(attempt_identity_digest) is None:
            raise ValueError("attempt identity must be a lowercase SHA-256 digest")
        if not self.isolated:
            return attempt_identity_digest
        encoded = json.dumps(
            {
                "attempt_identity_digest": attempt_identity_digest,
                "runtime_learning_scope_digest": self.digest,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


class SelfImproveOutcomeDelegate(Protocol):
    """Minimal durable learning interface wrapped by project privacy."""

    planner_store: object

    def load_failed_model_ids(
        self,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]:
        """Load failures for one already-scoped attempt identity."""
        ...

    def record_outcome(
        self,
        *,
        task_text: str,
        candidate: object,
        succeeded: bool,
        attempt_identity_digest: str,
    ) -> object:
        """Record one result for one already-scoped attempt identity."""
        ...


class SelfImprovePolicyBoundOutcomeAdapter:
    """Recheck policy and isolate every protected-project learning operation."""

    def __init__(
        self,
        delegate: SelfImproveOutcomeDelegate,
        scope: SelfImproveLearningScope,
        guard: Callable[[str], None],
    ) -> None:
        """Bind a delegate to one immutable scope and effect guard."""
        self._delegate = delegate
        self._scope = scope
        self._guard = guard
        self.planner_store = delegate.planner_store

    def load_failed_model_ids(
        self,
        *,
        task_text: str,
        attempt_identity_digest: str,
    ) -> tuple[str, ...]:
        """Load only failures from this exact public project scope."""
        self._guard(task_text)
        failures = self._delegate.load_failed_model_ids(
            task_text=task_text,
            attempt_identity_digest=self._scope.bind_attempt(
                attempt_identity_digest
            ),
        )
        self._guard(task_text)
        return failures

    def record_outcome(
        self,
        *,
        task_text: str,
        candidate: object,
        succeeded: bool,
        attempt_identity_digest: str,
    ) -> object:
        """Persist one public outcome under the project-scoped identity."""
        self._guard(task_text)
        return self._delegate.record_outcome(
            task_text=task_text,
            candidate=candidate,
            succeeded=succeeded,
            attempt_identity_digest=self._scope.bind_attempt(
                attempt_identity_digest
            ),
        )
