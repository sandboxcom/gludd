"""Bounded local-model proposal comparison against a Codex reference."""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

_MAX_EDITS = 32
_MAX_TESTS = 64
_MAX_COMMANDS = 32
_MAX_CONTENT_BYTES = 1_048_576
_MAX_COMMAND_BYTES = 4096
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_TASK_RE = re.compile(r"^S[0-9]+(?:\.[0-9]+)?$")
_SHELL_METACHARACTERS = frozenset(";|&$()<>\n\r")
_SECRET_ASSIGNMENT_RE = re.compile(r"(?i)\b(token|psk|password|secret)=([^\s]+)")


_PROPOSAL_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "baseline_sha",
        "task_id",
        "edits",
        "tests",
        "make_commands",
        "commit_message",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "baseline_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "task_id": {"type": "string", "pattern": "^S[0-9.]+$"},
        "edits": {
            "type": "array",
            "minItems": 1,
            "maxItems": _MAX_EDITS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation", "path", "old_text", "new_text"],
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["replace", "create", "delete"],
                    },
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
            },
        },
        "tests": {
            "type": "array",
            "minItems": 1,
            "maxItems": _MAX_TESTS,
            "items": {"type": "string"},
        },
        "make_commands": {
            "type": "array",
            "minItems": 1,
            "maxItems": _MAX_COMMANDS,
            "items": {
                "type": "string",
                "pattern": "^make [^;|&$()<>\\n\\r]+$",
            },
        },
        "commit_message": {"type": "string"},
    },
}


class _LocalModel(Protocol):
    """Minimal llama.cpp-compatible inference protocol."""

    def __call__(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        echo: bool,
    ) -> object: ...


class _ChatLocalModel(Protocol):
    """llama.cpp chat-completion interface used for constrained JSON."""

    def create_chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        grammar: object,
    ) -> object: ...


class _ModelFactory(Protocol):
    """Typed constructor boundary for one local model."""

    def __call__(
        self,
        *,
        model_path: str,
        n_ctx: int,
        verbose: bool,
    ) -> _LocalModel: ...


class _GrammarConstructor(Protocol):
    """Typed llama.cpp grammar class boundary."""

    def from_json_schema(self, schema_json: str, *, verbose: bool) -> object:
        """Build one grammar from a JSON schema."""


class _LlamaCppRuntime(Protocol):
    """Typed optional llama.cpp module boundary."""

    Llama: _ModelFactory
    LlamaGrammar: _GrammarConstructor


_GrammarFactory = Callable[[str], object]


@dataclass(frozen=True)
class ProposalEdit:
    """One exact, confined patch operation on a repository-relative file."""

    operation: str
    path: str
    old_text: str
    new_text: str


@dataclass(frozen=True)
class ProposalManifest:
    """Strict, bounded local-model proposal with no direct tool authority."""

    schema_version: int
    baseline_sha: str
    task_id: str
    edits: tuple[ProposalEdit, ...]
    tests: tuple[str, ...]
    make_commands: tuple[str, ...]
    commit_message: str

    def to_json(self) -> str:
        """Serialize the validated proposal for an isolated worker exchange."""
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "baseline_sha": self.baseline_sha,
                "task_id": self.task_id,
                "edits": [
                    {
                        "operation": edit.operation,
                        "path": edit.path,
                        "old_text": edit.old_text,
                        "new_text": edit.new_text,
                    }
                    for edit in self.edits
                ],
                "tests": list(self.tests),
                "make_commands": list(self.make_commands),
                "commit_message": self.commit_message,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> ProposalManifest:
        """Parse one strict proposal object and reject ambiguous model output."""
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"proposal is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("proposal must be a JSON object")

        required = {
            "schema_version",
            "baseline_sha",
            "task_id",
            "edits",
            "tests",
            "make_commands",
            "commit_message",
        }
        unknown = set(value) - required
        missing = required - set(value)
        if unknown:
            raise ValueError(f"proposal has unknown fields: {sorted(unknown)}")
        if missing:
            raise ValueError(f"proposal is missing fields: {sorted(missing)}")
        if value["schema_version"] != 1:
            raise ValueError("schema_version must be 1")

        baseline_sha = value["baseline_sha"]
        if not isinstance(baseline_sha, str) or not _SHA_RE.fullmatch(baseline_sha):
            raise ValueError("baseline_sha must be exactly 40 lowercase hex characters")
        task_id = value["task_id"]
        if not isinstance(task_id, str) or not _TASK_RE.fullmatch(task_id):
            raise ValueError("task_id must use the canonical S<number>[.<number>] form")

        edits_raw = value["edits"]
        if not isinstance(edits_raw, list) or not edits_raw or len(edits_raw) > _MAX_EDITS:
            raise ValueError(f"edits must contain 1..{_MAX_EDITS} entries")
        edits: list[ProposalEdit] = []
        seen_edits: set[tuple[str, str, str, str]] = set()
        content_bytes = 0
        for item in edits_raw:
            required_edit_fields = {"operation", "path", "old_text", "new_text"}
            if not isinstance(item, dict) or set(item) != required_edit_fields:
                raise ValueError(
                    "each edit must contain exactly operation, path, old_text, and new_text"
                )
            operation = item["operation"]
            path = item["path"]
            old_text = item["old_text"]
            new_text = item["new_text"]
            if operation not in {"replace", "create", "delete"}:
                raise ValueError(f"unsupported edit operation: {operation!r}")
            if not isinstance(path, str) or not _safe_relative_path(path):
                raise ValueError(f"edit path is not repository-relative and confined: {path!r}")
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                raise ValueError(f"edit text must be UTF-8 text: {path}")
            if operation == "replace" and (
                not old_text or old_text == new_text
            ):
                raise ValueError("replace requires distinct non-empty old_text")
            if operation == "create" and (old_text or not new_text):
                raise ValueError("create requires empty old_text and non-empty new_text")
            if operation == "delete" and (not old_text or new_text):
                raise ValueError("delete requires non-empty old_text and empty new_text")
            identity = (operation, path, old_text, new_text)
            if identity in seen_edits:
                raise ValueError(f"duplicate edit operation: {path}")
            seen_edits.add(identity)
            content_bytes += len(old_text.encode("utf-8"))
            content_bytes += len(new_text.encode("utf-8"))
            edits.append(
                ProposalEdit(
                    operation=operation,
                    path=path,
                    old_text=old_text,
                    new_text=new_text,
                )
            )
        if content_bytes > _MAX_CONTENT_BYTES:
            raise ValueError(f"proposal edit content exceeds {_MAX_CONTENT_BYTES} bytes")

        tests = _parse_path_list(value["tests"], "test path", _MAX_TESTS)
        commands_raw = value["make_commands"]
        if (
            not isinstance(commands_raw, list)
            or not commands_raw
            or len(commands_raw) > _MAX_COMMANDS
        ):
            raise ValueError(f"make_commands must contain 1..{_MAX_COMMANDS} entries")
        commands: list[str] = []
        for command in commands_raw:
            if not isinstance(command, str) or not command.startswith("make "):
                raise ValueError("every tool step must be a make command")
            if len(command.encode("utf-8")) > _MAX_COMMAND_BYTES:
                raise ValueError("make command exceeds the bounded command size")
            if any(token in command for token in _SHELL_METACHARACTERS):
                raise ValueError("make command contains a forbidden shell metacharacter")
            commands.append(command)

        commit_message = value["commit_message"]
        if (
            not isinstance(commit_message, str)
            or not commit_message.strip()
            or "\n" in commit_message
            or len(commit_message.encode("utf-8")) > 200
        ):
            raise ValueError("commit_message must be one bounded non-empty line")

        return cls(
            schema_version=1,
            baseline_sha=baseline_sha,
            task_id=task_id,
            edits=tuple(edits),
            tests=tests,
            make_commands=tuple(commands),
            commit_message=commit_message.strip(),
        )

    def validate_paths(self, repo_root: Path) -> None:
        """Reject edit or test paths whose canonical identity escapes the root."""
        canonical_root = repo_root.resolve(strict=True)
        for path in (*[edit.path for edit in self.edits], *self.tests):
            candidate = (canonical_root / path).resolve(strict=False)
            if not candidate.is_relative_to(canonical_root):
                raise ValueError(f"proposal path escapes repository root: {path}")


@dataclass(frozen=True)
class CandidateEvidence:
    """Deterministic gate and repository evidence for one applied proposal."""

    changed_files: frozenset[str]
    tests_passed: bool
    warnings: int
    coverage_aggregate: float
    coverage_min_file: float
    ruff_passed: bool
    mypy_passed: bool
    docstrings_passed: bool
    markdown_passed: bool
    cleanup_passed: bool
    commit_count: int
    worktree_clean: bool
    elapsed_seconds: float
    changed_lines: int = 0


@dataclass(frozen=True)
class CodexReference:
    """Independent Codex patch boundary used as the comparison oracle."""

    baseline_sha: str
    reference_sha: str
    changed_files: frozenset[str]
    test_files: frozenset[str]
    changed_lines: int
    elapsed_seconds: float


@dataclass(frozen=True)
class ComparisonResult:
    """Scored parity result and deterministic retry feedback."""

    accepted: bool
    score: float
    blockers: tuple[str, ...]
    changed_file_precision: float
    changed_file_recall: float


def compare_with_codex(
    proposal: ProposalManifest,
    evidence: CandidateEvidence,
    reference: CodexReference,
) -> ComparisonResult:
    """Compare all proposed changes and gate evidence to the Codex reference."""
    blockers: list[str] = []
    if proposal.baseline_sha != reference.baseline_sha:
        blockers.append("baseline identity")
    if not evidence.tests_passed:
        blockers.append("tests")
    if evidence.warnings:
        blockers.append("warnings")
    if evidence.coverage_aggregate < 85.0:
        blockers.append("aggregate coverage")
    if evidence.coverage_min_file < 75.0:
        blockers.append("per-file coverage")
    if not evidence.ruff_passed:
        blockers.append("ruff")
    if not evidence.mypy_passed:
        blockers.append("mypy")
    if not evidence.docstrings_passed:
        blockers.append("docstrings")
    if not evidence.markdown_passed:
        blockers.append("markdown")
    if not evidence.cleanup_passed:
        blockers.append("resource cleanup")
    if evidence.commit_count != 1:
        blockers.append("atomic commit")
    if not evidence.worktree_clean:
        blockers.append("clean worktree")

    reference_files = reference.changed_files
    candidate_files = evidence.changed_files
    intersection = reference_files & candidate_files
    precision = len(intersection) / len(candidate_files) if candidate_files else 0.0
    recall = len(intersection) / len(reference_files) if reference_files else 1.0
    if precision < 1.0:
        blockers.append("changed-file precision")
    if recall < 1.0:
        blockers.append("changed-file recall")

    proposed_tests = frozenset(proposal.tests)
    if not reference.test_files <= proposed_tests:
        blockers.append("reference test coverage")

    score = 100.0
    score -= (1.0 - precision) * 20.0
    score -= (1.0 - recall) * 25.0
    score -= max(0, len(blockers) - int(precision < 1.0) - int(recall < 1.0)) * 5.0
    if (
        evidence.changed_lines > 0
        and reference.changed_lines > 0
        and evidence.changed_lines > reference.changed_lines * 1.5
    ):
        blockers.append("diff size")
        score -= min(10.0, 10.0 * evidence.changed_lines / reference.changed_lines / 4.0)
    if (
        reference.elapsed_seconds > 0
        and evidence.elapsed_seconds > reference.elapsed_seconds * 2.0
    ):
        blockers.append("tool efficiency")
        score -= min(10.0, evidence.elapsed_seconds / reference.elapsed_seconds)

    ordered_blockers = tuple(dict.fromkeys(blockers))
    score = round(max(0.0, score), 2)
    return ComparisonResult(
        accepted=not ordered_blockers and score == 100.0,
        score=score,
        blockers=ordered_blockers,
        changed_file_precision=precision,
        changed_file_recall=recall,
    )


def build_retry_prompt(
    task: str,
    comparison: ComparisonResult,
    *,
    diagnostics: str = "",
) -> str:
    """Build bounded, secret-redacted evidence for a subsequent local attempt."""
    gaps = ", ".join(comparison.blockers) if comparison.blockers else "none"
    raw_tail = diagnostics.replace("\x00", "")[-4096:].encode("utf-8")
    diagnostic_tail = raw_tail[-4096:].decode("utf-8", errors="replace")
    diagnostic_tail = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        diagnostic_tail,
    )
    failure_evidence = (
        f"\nExact bounded failure evidence:\n{diagnostic_tail}\n"
        if diagnostic_tail
        else ""
    )
    return (
        f"{task}\n\n"
        f"Previous proposal score: {comparison.score:.2f}/100.\n"
        f"Required corrections: {gaps}.\n"
        f"{failure_evidence}"
        "Do not broaden the changed-file set beyond the Codex reference. "
        "Return only the strict proposal JSON object."
    )


class LocalProposalGateway:
    """Generate strict proposal JSON with one explicit local GGUF model."""

    def __init__(
        self,
        model_path: Path,
        *,
        model_factory: _ModelFactory | None = None,
        grammar_factory: _GrammarFactory | None = None,
    ) -> None:
        """Bind one explicit GGUF and injectable llama.cpp factories."""
        if not model_path.is_file():
            raise FileNotFoundError(f"local GGUF is not readable: {model_path}")
        self._model_path = model_path
        self._model_factory = model_factory or _default_model_factory
        self._grammar_factory = grammar_factory or _default_grammar_factory
        self._model: _LocalModel | None = None

    def propose(self, prompt: str) -> ProposalManifest:
        """Run deterministic decode and parse one bounded proposal."""
        if self._model is None:
            self._model = self._model_factory(
                model_path=str(self._model_path),
                n_ctx=32768,
                verbose=False,
            )
        if hasattr(self._model, "create_chat_completion"):
            chat_model = cast("_ChatLocalModel", self._model)
            output = chat_model.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Return exactly one valid JSON proposal object. "
                            "Do not emit markdown or prose."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
                temperature=0.0,
                grammar=self._grammar_factory(
                    json.dumps(_PROPOSAL_JSON_SCHEMA, sort_keys=True)
                ),
            )
        else:
            output = self._model(
                prompt,
                max_tokens=4096,
                temperature=0.0,
                echo=False,
            )
        if not isinstance(output, Mapping):
            raise ValueError("local model returned a non-object response")
        choices = output.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], Mapping)
        ):
            raise ValueError("local model response has no choices")
        choice = choices[0]
        text = choice.get("text")
        if not isinstance(text, str):
            message = choice.get("message")
            text = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(text, str) or not text.strip():
            raise ValueError("local model response has no proposal text")
        return ProposalManifest.from_json(_extract_json_object(text))


def _safe_relative_path(raw: str) -> bool:
    if not raw or "\\" in raw or "\x00" in raw:
        return False
    path = PurePosixPath(raw)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.parts[0] not in {".git", ".venv"}
    )


def _parse_path_list(value: object, label: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ValueError(f"{label}s must contain 1..{maximum} entries")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _safe_relative_path(item):
            raise ValueError(f"{label} is not repository-relative and confined: {item!r}")
        result.append(item)
    if len(set(result)) != len(result):
        raise ValueError(f"duplicate {label}")
    return tuple(result)


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1])
            if stripped.lstrip().startswith("json"):
                stripped = stripped.lstrip()[4:].lstrip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    diagnostic = (
        f"head={stripped[:256]!r} tail={stripped[-768:]!r}"
    )
    if start < 0:
        raise ValueError(f"local model response has no JSON start: {diagnostic}")
    if end < start:
        raise ValueError(f"local model response contains incomplete JSON: {diagnostic}")
    return stripped[start : end + 1]


def _load_llama_cpp_runtime() -> _LlamaCppRuntime:
    """Load the optional local-inference runtime through one typed seam."""
    try:
        runtime = importlib.import_module("llama_cpp")
    except ImportError as exc:
        raise RuntimeError(
            "llama.cpp runtime is unavailable; run make sync-llama-cpp "
            "SYNC_LLAMA_CPP_VALIDATE_ONLY=0"
        ) from exc
    return cast("_LlamaCppRuntime", runtime)


def _default_model_factory(
    *,
    model_path: str,
    n_ctx: int,
    verbose: bool,
) -> _LocalModel:
    runtime = _load_llama_cpp_runtime()
    return runtime.Llama(model_path=model_path, n_ctx=n_ctx, verbose=verbose)


def _default_grammar_factory(schema_json: str) -> object:
    runtime = _load_llama_cpp_runtime()
    grammar_value = getattr(runtime, "LlamaGrammar", None)
    if grammar_value is None:
        raise RuntimeError("llama.cpp runtime does not expose JSON grammar support")
    grammar_type = cast("_GrammarConstructor", grammar_value)
    return grammar_type.from_json_schema(schema_json, verbose=False)
