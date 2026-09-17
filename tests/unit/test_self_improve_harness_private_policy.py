"""Privacy-boundary tests for the self-improvement gap-analysis harness."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from general_ludd.self_improve.harness import SelfImprovementHarness
from general_ludd.self_improve.private_policy import parse_self_improve_policy

PRIVATE_CANARY = "PRIVATE_PRICE_FORMULA_CANARY_7b21"
PUBLIC_CANARY = "PUBLIC_ADAPTER_CANARY_2a48"


class _RecordingGateway:
    """Minimal real-shape gateway that preserves calls for leak assertions."""

    def __init__(self, content: str, *, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[tuple[str, list[dict[str, str]], dict[str, Any]]] = []

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> SimpleNamespace:
        self.calls.append((profile_id, messages, kwargs))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=self.content)


def _write_policy(
    root: Path,
    *,
    default_access: str = "public",
    private_paths: list[str] | None = None,
    public_paths: list[str] | None = None,
) -> Path:
    policy_path = root / ".gludd" / "self-improve-policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_access": default_access,
                "private_paths": private_paths or [],
                "public_paths": public_paths or [],
            }
        ),
        encoding="utf-8",
    )
    return policy_path


def _write_sources(root: Path) -> None:
    source = root / "src"
    source.mkdir(parents=True)
    (source / "private_pricing.py").write_text(PRIVATE_CANARY, encoding="utf-8")
    (source / "public_adapter.py").write_text(PUBLIC_CANARY, encoding="utf-8")


def _serialized(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def test_private_source_and_private_model_finding_never_escape(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """Only a public sibling reaches the provider and survives its response."""
    _write_sources(tmp_path)
    _write_policy(tmp_path, private_paths=["src/private_pricing.py"])
    gateway = _RecordingGateway(
        json.dumps(
            [
                {
                    "title": "copy private formula",
                    "description": PRIVATE_CANARY,
                    "priority": "high",
                    "tier": "code",
                    "file": "src/private_pricing.py",
                },
                {
                    "title": "improve public adapter",
                    "description": "public result",
                    "priority": "medium",
                    "tier": "code",
                    "file": "src/public_adapter.py",
                },
            ]
        )
    )
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    with caplog.at_level(logging.WARNING):
        findings = harness.run_gap_analysis()

    assert len(gateway.calls) == 1
    assert PUBLIC_CANARY in _serialized(gateway.calls)
    assert findings == [
        {
            "title": "improve public adapter",
            "description": "public result",
            "priority": "medium",
            "tier": "code",
            "file": "src/public_adapter.py",
        }
    ]
    observable = _serialized(
        {
            "calls": gateway.calls,
            "findings": findings,
            "decision": harness.last_policy_decision,
            "logs": caplog.messages,
        }
    )
    assert PRIVATE_CANARY not in observable
    assert "src/private_pricing.py" not in observable
    assert set(harness.last_policy_decision) == {
        "policy_digest",
        "allowed_count",
        "blocked_count",
        "path_hashes",
    }
    assert harness.last_policy_decision["blocked_count"] >= 1


def test_private_recurring_signal_blocks_before_provider_call(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """A path-scoped private input blocks the whole cycle without echoing it."""
    _write_policy(tmp_path, private_paths=["src/private_pricing.py"])
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)
    records = [
        {
            "path": "src/private_pricing.py",
            "task_type": "code",
            "blocker_kind": PRIVATE_CANARY,
            "incident_count": 4,
            "recent_todo_ids": ["PRIVATE-TODO"],
        }
    ]

    with caplog.at_level(logging.WARNING):
        result = harness.run_gap_analysis(recurring_failures=records)

    assert result == []
    assert gateway.calls == []
    observable = _serialized(
        {
            "result": result,
            "decision": harness.last_policy_decision,
            "logs": caplog.messages,
        }
    )
    assert PRIVATE_CANARY not in observable
    assert "src/private_pricing.py" not in observable
    assert harness.last_policy_decision["blocked_count"] == 1
    assert len(harness.last_policy_decision["path_hashes"]) == 1


def test_malformed_policy_disables_generation_without_leaking_raw_data(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """Present-but-invalid policy is a silent fail-closed generation boundary."""
    policy_path = tmp_path / ".gludd" / "self-improve-policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text("{" + PRIVATE_CANARY, encoding="utf-8")
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    with caplog.at_level(logging.WARNING):
        result = harness.run_gap_analysis(
            recurring_failures=[{"message": PRIVATE_CANARY}]
        )

    assert result == []
    assert gateway.calls == []
    observable = _serialized(
        {
            "result": result,
            "decision": harness.last_policy_decision,
            "logs": caplog.messages,
        }
    )
    assert PRIVATE_CANARY not in observable
    assert harness.last_policy_decision["policy_digest"] is None
    assert harness.last_policy_decision["blocked_count"] == 1


def test_missing_policy_preserves_public_backward_compatibility(tmp_path: Path) -> None:
    """A repository without the new file still gets normal model analysis."""
    _write_sources(tmp_path)
    gateway = _RecordingGateway(
        '[{"title":"public","description":"ok","priority":"low","tier":"code"}]'
    )
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    result = harness.run_gap_analysis()

    assert len(gateway.calls) == 1
    assert PUBLIC_CANARY in _serialized(gateway.calls)
    assert result[0]["title"] == "public"
    assert harness.last_policy_decision["policy_digest"]


def test_default_private_policy_sends_only_explicit_public_allowlist(
    tmp_path: Path,
) -> None:
    """Default-private mode turns public_paths into a strict provider allowlist."""
    _write_sources(tmp_path)
    _write_policy(
        tmp_path,
        default_access="private",
        public_paths=["src/public_adapter.py"],
    )
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    assert harness.run_gap_analysis() == []

    assert len(gateway.calls) == 1
    prompt = _serialized(gateway.calls)
    assert PUBLIC_CANARY in prompt
    assert PRIVATE_CANARY not in prompt
    assert "src/private_pricing.py" not in prompt


def test_direct_source_read_and_todo_boundaries_filter_private_paths(
    tmp_path: Path,
) -> None:
    """Private data stays blocked even when lower harness APIs are called directly."""
    _write_sources(tmp_path)
    _write_policy(tmp_path, private_paths=["src/private_pricing.py"])
    harness = SelfImprovementHarness(repo_root=str(tmp_path))

    source = harness._read_all_src()
    todos = harness.generate_fix_todos(
        [
            {
                "type": "code_gap",
                "file": "src/private_pricing.py",
                "message": PRIVATE_CANARY,
            },
            {
                "type": "code_gap",
                "file": "src/public_adapter.py",
                "message": "public work",
            },
        ]
    )
    enqueued = harness.enqueue_todos(
        [
            {"title": PRIVATE_CANARY, "source_file": "src/private_pricing.py"},
            {"title": "public work", "source_file": "src/public_adapter.py"},
        ]
    )

    assert PRIVATE_CANARY not in source
    assert "private_pricing.py" not in source
    assert PUBLIC_CANARY in source
    assert len(todos) == 1
    assert todos[0]["source_file"] == "src/public_adapter.py"
    assert enqueued == 1
    assert harness._todos == [
        {"title": "public work", "source_file": "src/public_adapter.py"}
    ]


def test_gateway_exception_text_is_not_reflected_in_logs(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """An untrusted provider exception cannot reflect a private canary."""
    _write_policy(tmp_path)
    gateway = _RecordingGateway("", error=RuntimeError(PRIVATE_CANARY))
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    with caplog.at_level(logging.WARNING):
        assert harness.run_gap_analysis() == []

    assert len(gateway.calls) == 1
    assert PRIVATE_CANARY not in _serialized(caplog.messages)


def test_policy_is_loaded_before_any_source_file_is_opened(tmp_path: Path) -> None:
    """Repository source observation cannot precede policy validation."""
    _write_sources(tmp_path)
    policy = parse_self_improve_policy(
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":[],"public_paths":[]}'
    )
    state = {"loaded": False}
    real_open = open

    def _load(_root: Path) -> object:
        state["loaded"] = True
        return policy

    def _guarded_open(*args: Any, **kwargs: Any) -> Any:
        assert state["loaded"] is True
        return real_open(*args, **kwargs)

    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)
    with (
        patch("general_ludd.self_improve.harness.load_self_improve_policy", _load),
        patch("builtins.open", _guarded_open),
    ):
        assert harness.run_gap_analysis() == []

    assert len(gateway.calls) == 1


def test_policy_digest_change_before_dispatch_blocks_provider(tmp_path: Path) -> None:
    """A policy TOCTOU change after source collection fails closed."""
    _write_sources(tmp_path)
    public = parse_self_improve_policy(
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":[],"public_paths":[]}'
    )
    private = parse_self_improve_policy(
        '{"schema_version":1,"default_access":"private",'
        '"private_paths":[],"public_paths":[]}'
    )
    policies = iter((public, private))
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    with patch(
        "general_ludd.self_improve.harness.load_self_improve_policy",
        side_effect=lambda _root: next(policies),
    ):
        assert harness.run_gap_analysis() == []

    assert gateway.calls == []
    assert harness.last_policy_decision["policy_digest"] == private.digest
    assert harness.last_policy_decision["blocked_count"] == 1


def test_only_private_source_causes_zero_provider_calls(tmp_path: Path) -> None:
    """A project with no approved source does not spend or disclose via a model."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "private_pricing.py").write_text(PRIVATE_CANARY, encoding="utf-8")
    _write_policy(tmp_path, private_paths=["src/private_pricing.py"])
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    assert harness.run_gap_analysis() == []

    assert gateway.calls == []
    assert PRIVATE_CANARY not in _serialized(harness.last_policy_decision)


def test_symlinked_source_directory_is_blocked_before_provider(tmp_path: Path) -> None:
    """A source-directory symlink cannot bypass path policy or trigger a call."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pricing.py").write_text(PRIVATE_CANARY, encoding="utf-8")
    source = tmp_path / "src"
    source.mkdir()
    (source / "linked").symlink_to(outside, target_is_directory=True)
    _write_policy(tmp_path, private_paths=["src/linked/**"])
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    assert harness.run_gap_analysis() == []

    assert gateway.calls == []
    assert PRIVATE_CANARY not in _serialized(harness.last_policy_decision)


def test_policy_approved_source_snapshot_has_a_hard_size_bound(tmp_path: Path) -> None:
    """Large public projects cannot create an unbounded model prompt."""
    source = tmp_path / "src"
    source.mkdir()
    for index in range(4):
        (source / f"public_{index}.py").write_text("x" * 100_000, encoding="utf-8")
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    assert harness.run_gap_analysis() == []

    assert len(gateway.calls) == 1
    prompt = gateway.calls[0][1][0]["content"]
    assert len(prompt.encode("utf-8")) < 270_000


@pytest.mark.parametrize(
    "record",
    [
        {"affected_paths": ["src/public.py", "src/private_pricing.py"]},
        {"affected_paths": "src/private_pricing.py"},
        {"path": 7},
    ],
)
def test_private_or_malformed_path_collections_block_whole_input(
    tmp_path: Path,
    record: dict[str, object],
) -> None:
    """Every supported path-field shape is checked before dispatch."""
    _write_policy(tmp_path, private_paths=["src/private_pricing.py"])
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    assert harness.run_gap_analysis(recurring_failures=[record]) == []

    assert gateway.calls == []
    assert harness.last_policy_decision["blocked_count"] == 1


def test_outside_absolute_input_path_fails_closed(tmp_path: Path) -> None:
    """An absolute path outside the bound project cannot reach a provider."""
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    result = harness.run_gap_analysis(
        recurring_failures=[{"path": "/outside/private.py"}]
    )

    assert result == []
    assert gateway.calls == []


def test_policy_reload_error_before_dispatch_fails_closed_without_echo(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """A policy that becomes unreadable after collection blocks model dispatch."""
    _write_sources(tmp_path)
    public = parse_self_improve_policy(
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":[],"public_paths":[]}'
    )
    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    with (
        patch(
            "general_ludd.self_improve.harness.load_self_improve_policy",
            side_effect=(public, RuntimeError(PRIVATE_CANARY)),
        ),
        caplog.at_level(logging.WARNING),
    ):
        assert harness.run_gap_analysis() == []

    assert gateway.calls == []
    assert PRIVATE_CANARY not in _serialized(
        {"logs": caplog.messages, "decision": harness.last_policy_decision}
    )


def test_malformed_policy_blocks_direct_generation_and_enqueue(tmp_path: Path) -> None:
    """Direct lower-level entry points cannot bypass invalid-policy shutdown."""
    policy_path = tmp_path / ".gludd" / "self-improve-policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text("{malformed", encoding="utf-8")
    harness = SelfImprovementHarness(repo_root=str(tmp_path))
    private = [{"file": "src/private.py", "message": PRIVATE_CANARY}]

    assert harness._read_all_src() == ""
    assert harness.generate_fix_todos(private) == []
    assert harness.enqueue_todos(private) == 0
    assert harness._todos == []


def test_static_scanners_filter_private_source_and_coverage_paths(
    tmp_path: Path,
) -> None:
    """Local fallback findings obey the same policy as model-driven findings."""
    package = tmp_path / "src" / "general_ludd"
    package.mkdir(parents=True)
    (package / "private_pricing.py").write_text(PRIVATE_CANARY, encoding="utf-8")
    (package / "public_adapter.py").write_text(PUBLIC_CANARY, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "coverage.xml").write_text(
        "<coverage><packages><package><classes>"
        '<class filename="src/general_ludd/private_pricing.py" line-rate="0.1"/>'
        '<class filename="src/general_ludd/public_adapter.py" line-rate="0.1"/>'
        "</classes></package></packages></coverage>",
        encoding="utf-8",
    )
    _write_policy(tmp_path, private_paths=["src/general_ludd/private_pricing.py"])
    harness = SelfImprovementHarness(repo_root=str(tmp_path))

    findings = harness.run_gap_analysis()

    rendered = _serialized(findings)
    assert "private_pricing.py" not in rendered
    assert PRIVATE_CANARY not in rendered
    assert "public_adapter.py" in rendered


def test_record_property_failure_is_a_redacted_fail_closed_boundary(
    tmp_path: Path,
) -> None:
    """Hostile record objects cannot bypass pre-dispatch classification."""

    class _HostileRecord:
        @property
        def path(self) -> str:
            raise RuntimeError(PRIVATE_CANARY)

    gateway = _RecordingGateway("[]")
    harness = SelfImprovementHarness(repo_root=str(tmp_path), model_gateway=gateway)

    assert harness.run_gap_analysis(recurring_failures=[_HostileRecord()]) == []

    assert gateway.calls == []
    assert PRIVATE_CANARY not in _serialized(harness.last_policy_decision)
